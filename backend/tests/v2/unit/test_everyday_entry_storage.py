from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from inspect import signature
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from track_anywhere.application.privacy import (
    NarrativeAmountSource,
    NarrativeExternalReference,
    NarrativeMoney,
    TransactionDescription,
    TransactionNarrativeV2,
    upcast_transaction_description,
)
from track_anywhere.domain.credit_cards.events import CreditCardIntent
from track_anywhere.infrastructure.db.repositories.entries import (
    PreparedEntryIntentRepository,
    ProposedPreparedIntent,
    hash_commit_token,
    hmac_external_reference,
    hmac_source_fingerprint,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentSnapshot,
)
from track_anywhere.infrastructure.projections.monthly_summary import (
    _credit_card_reporting_sign,
    _journal_reporting_sign,
)
from track_anywhere.queries.protected_content import (
    ProtectedContentUnavailable,
    get_transaction_narratives,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


def _proposal(**updates: object) -> ProposedPreparedIntent:
    values: dict[str, object] = {
        "book_id": uuid4(),
        "actor_id": "human:entry-owner",
        "intent_id": uuid4(),
        "prepared_status": "ready",
        "commit_token_hash": hash_commit_token("opaque-" + "x" * 32),
        "canonical_payload": {
            "kind": "expense",
            "units": "66000",
            "resolved_account_id": str(uuid4()),
        },
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
    }
    values.update(updates)
    return ProposedPreparedIntent(**values)  # type: ignore[arg-type]


def test_commit_tokens_and_duplicate_inputs_only_produce_irreversible_digests() -> None:
    token = "opaque-" + "sensitive-token-value-" * 2
    first = hash_commit_token(token)
    second = hash_commit_token(token)
    other = hash_commit_token(token + "-other")

    assert first == second
    assert first != other
    assert len(first) == 32
    assert token.encode() not in first
    assert token not in repr(_proposal(commit_token_hash=first))

    key = bytes(range(32))
    external = hmac_external_reference(
        key=key,
        provider_code="merchant",
        reference_kind="provider_order",
        reference="private-order-123",
    )
    fingerprint = hmac_source_fingerprint(
        key=key,
        normalized_parts=("merchant", "private-order-123", "66000", "CNY"),
    )
    assert len(external) == len(fingerprint) == 32
    assert external != fingerprint
    assert external != hmac_external_reference(
        key=bytes(reversed(range(32))),
        provider_code="merchant",
        reference_kind="provider_order",
        reference="private-order-123",
    )


@pytest.mark.parametrize(
    "private_key",
    (
        "merchant",
        "channel",
        "note",
        "source_text",
        "reference",
        "transaction_memo",
        "line_memos",
    ),
)
def test_prepared_payload_rejects_private_plaintext_recursively(
    private_key: str,
) -> None:
    secret = "must-not-escape"
    with pytest.raises(
        ValueError,
        match="^private entry fields require protected-content storage$",
    ) as error:
        _proposal(
            canonical_payload={
                "compiled": {
                    "safe": "units",
                    private_key: secret,
                }
            }
        )
    assert secret not in str(error.value)


def test_prepared_payload_rejects_float_and_non_json_values() -> None:
    with pytest.raises(
        ValueError,
        match="^prepared intent payload must use exact canonical JSON values$",
    ):
        _proposal(canonical_payload={"amount": 6.6})
    with pytest.raises(
        ValueError,
        match="^prepared intent payload must use exact canonical JSON values$",
    ):
        _proposal(canonical_payload={"opaque": b"private"})


def test_intent_repository_exposes_only_book_actor_intent_scoped_lookup() -> None:
    assert list(signature(PreparedEntryIntentRepository.get).parameters) == [
        "self",
        "book_id",
        "actor_id",
        "intent_id",
    ]
    assert list(signature(PreparedEntryIntentRepository.claim_ready).parameters)[:4] == [
        "self",
        "book_id",
        "actor_id",
        "intent_id",
    ]
    assert not hasattr(PreparedEntryIntentRepository, "get_global")
    assert not hasattr(PreparedEntryIntentRepository, "get_by_intent_id")


def test_narrative_v1_and_v2_upcast_to_one_private_shape() -> None:
    legacy = upcast_transaction_description(
        TransactionDescription(
            purpose="legacy purpose",
            transaction_memo="legacy memo",
            line_memos=("legacy line",),
        )
    )
    current_contract = TransactionNarrativeV2(
        amount_sources=(
            NarrativeAmountSource(
                field_path="amount",
                source_text="我花了4.55元",
            ),
            NarrativeAmountSource(
                field_path="category_allocations.0.amount",
                source_text="咖啡4.05元",
            ),
            NarrativeAmountSource(
                field_path="narrative.gross_amount",
                source_text="原价4.55元",
            ),
            NarrativeAmountSource(
                field_path="narrative.discount_amount",
                source_text="优惠0.50元",
            ),
        ),
        merchant="Private Merchant",
        channel="private-channel",
        note="private note",
        external_reference=NarrativeExternalReference(
            provider_code="merchant",
            kind="provider_order",
            reference="private-order-123",
        ),
        gross_amount=NarrativeMoney(value="4.55", asset_code="CNY"),
        discount_amount=NarrativeMoney(value="0.50", asset_code="CNY"),
        net_amount=NarrativeMoney(value="4.05", asset_code="CNY"),
    )
    current = upcast_transaction_description(current_contract)

    assert legacy.purpose == "legacy purpose"
    assert legacy.amount_sources == ()
    assert legacy.merchant is None
    assert tuple(source.field_path for source in current.amount_sources) == (
        "amount",
        "category_allocations.0.amount",
        "narrative.gross_amount",
        "narrative.discount_amount",
    )
    assert current.amount_sources[0].source_text == "我花了4.55元"
    assert current.merchant == "Private Merchant"
    assert current.net_amount == NarrativeMoney(value="4.05", asset_code="CNY")
    representations = (repr(current_contract), repr(current))
    for private_value in (
        "Private Merchant",
        "我花了4.55元",
        "咖啡4.05元",
        "原价4.55元",
        "优惠0.50元",
        "private-channel",
        "private note",
        "private-order-123",
    ):
        assert all(private_value not in value for value in representations)
    source_representation = repr(current_contract.amount_sources[0])
    assert "field_path='amount'" in source_representation
    assert "我花了4.55元" not in source_representation
    with pytest.raises((FrozenInstanceError, TypeError, ValidationError)):
        current.note = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("source_text", (" ", "private-source-" * 20))
def test_narrative_amount_source_text_is_bounded_and_redacted(
    source_text: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        NarrativeAmountSource(field_path="amount", source_text=source_text)

    assert "input_value" not in str(error.value)
    assert "private-source-" not in str(error.value)


@pytest.mark.parametrize("field_path", ("source_amount", "fee_amount"))
def test_fx_card_amount_source_paths_are_supported(field_path: str) -> None:
    source = NarrativeAmountSource(field_path=field_path, source_text="private amount")

    assert source.field_path == field_path


@pytest.mark.parametrize(
    "field_path",
    (
        "category_allocations.00.amount",
        "category_allocations.64.amount",
        "narrative.net_amount",
        "Amount",
        "amount.source_text",
    ),
)
def test_narrative_amount_source_paths_are_strict_and_errors_are_redacted(
    field_path: str,
) -> None:
    source_text = "must-not-escape"
    with pytest.raises(ValidationError) as error:
        NarrativeAmountSource(field_path=field_path, source_text=source_text)

    assert source_text not in str(error.value)


def test_narrative_amount_source_paths_must_be_unique() -> None:
    source = NarrativeAmountSource(
        field_path="amount",
        source_text="private amount",
    )
    with pytest.raises(
        ValidationError,
        match="amount source field paths must be unique",
    ) as error:
        TransactionNarrativeV2(amount_sources=(source, source))

    assert "private amount" not in str(error.value)


def test_refund_reporting_signs_do_not_double_invert_card_refunds() -> None:
    assert _journal_reporting_sign("standard") == 1
    assert _journal_reporting_sign("refund") == -1
    assert _credit_card_reporting_sign(CreditCardIntent.CHARGE) == 1
    assert _credit_card_reporting_sign(CreditCardIntent.FEE) == 1
    assert _credit_card_reporting_sign(CreditCardIntent.PAYMENT) == 0
    assert _credit_card_reporting_sign(CreditCardIntent.REFUND) == -1


class _NarrativeRepository:
    def __init__(self, snapshots: dict[UUID, ProtectedContentSnapshot]) -> None:
        self._snapshots = snapshots

    def get_active_batch(
        self,
        _session: Session,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> dict[UUID, ProtectedContentSnapshot]:
        return {
            sidecar_id: snapshot
            for sidecar_id in sidecar_ids
            if (snapshot := self._snapshots.get(sidecar_id)) is not None
            and snapshot.book_id == book_id
            and snapshot.status == "active"
        }

    def get_batch(
        self,
        _session: Session,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> dict[UUID, ProtectedContentSnapshot]:
        return {
            sidecar_id: snapshot
            for sidecar_id in sidecar_ids
            if (snapshot := self._snapshots.get(sidecar_id)) is not None
            and snapshot.book_id == book_id
        }


def _narrative_cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v2",
            keys={"v2": bytes(range(32))},
        ),
        nonce_source=lambda size: b"n" * size,
    )


def _narrative_snapshot(
    *,
    book_id: UUID,
    sidecar_id: UUID,
    kind: str,
    plaintext: bytes,
) -> ProtectedContentSnapshot:
    cipher = _narrative_cipher()
    sealed = cipher.encrypt(
        book_id=book_id,
        sidecar_id=sidecar_id,
        kind=kind,  # type: ignore[arg-type]
        plaintext=plaintext,
    )
    return ProtectedContentSnapshot(
        book_id=book_id,
        sidecar_id=sidecar_id,
        kind=kind,
        ciphertext=sealed.ciphertext,
        key_ref=sealed.key_ref,
        nonce=sealed.nonce,
        algorithm=sealed.algorithm,
        content_hash=sealed.content_hash,
        status="active",
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        erased_at=None,
    )


def test_full_refund_empty_amount_sources_upcast_through_canonical_codec() -> None:
    book_id = uuid4()
    sidecar_id = uuid4()
    full_refund = TransactionNarrativeV2(amount_sources=())
    canonical = canonical_json_bytes(full_refund.model_dump(mode="json"))

    assert b'"amount_sources":[]' in canonical
    decoded = get_transaction_narratives(
        cast(Session, object()),
        book_id,
        narrative_refs=(sidecar_id,),
        cipher=_narrative_cipher(),
        repository=_NarrativeRepository(
            {
                sidecar_id: _narrative_snapshot(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    kind="transaction_narrative_v2",
                    plaintext=canonical,
                )
            }
        ),  # type: ignore[arg-type]
    )

    assert decoded[sidecar_id].amount_sources == ()
    assert upcast_transaction_description(full_refund).amount_sources == ()


def test_strict_v1_v2_decoder_boundary_upcasts_without_plaintext_errors() -> None:
    book_id = uuid4()
    legacy_id = uuid4()
    current_id = uuid4()
    malformed_id = uuid4()
    noncanonical_id = uuid4()
    legacy_plaintext = canonical_json_bytes(
        TransactionDescription(
            purpose="legacy private",
            transaction_memo=None,
            line_memos=(),
        ).model_dump(mode="json")
    )
    current_contract = TransactionNarrativeV2(
        amount_sources=(
            NarrativeAmountSource(
                field_path="amount",
                source_text="I spent $10.00",
            ),
            NarrativeAmountSource(
                field_path="narrative.gross_amount",
                source_text="gross was $12.00",
            ),
        ),
        merchant="current private",
        net_amount=NarrativeMoney(value="10.00", asset_code="USD"),
    )
    current_plaintext = canonical_json_bytes(
        current_contract.model_dump(mode="json")
    )
    private_malformed = canonical_json_bytes(
        {
            **current_contract.model_dump(mode="json"),
            "amount_sources": [
                {
                    "field_path": "narrative.net_amount",
                    "source_text": "must-not-escape",
                }
            ],
        }
    )
    repository = _NarrativeRepository(
        {
            legacy_id: _narrative_snapshot(
                book_id=book_id,
                sidecar_id=legacy_id,
                kind="transaction_description",
                plaintext=legacy_plaintext,
            ),
            current_id: _narrative_snapshot(
                book_id=book_id,
                sidecar_id=current_id,
                kind="transaction_narrative_v2",
                plaintext=current_plaintext,
            ),
            malformed_id: _narrative_snapshot(
                book_id=book_id,
                sidecar_id=malformed_id,
                kind="transaction_narrative_v2",
                plaintext=private_malformed,
            ),
            noncanonical_id: _narrative_snapshot(
                book_id=book_id,
                sidecar_id=noncanonical_id,
                kind="transaction_narrative_v2",
                plaintext=b" " + current_plaintext,
            ),
        }
    )

    narratives = get_transaction_narratives(
        cast(Session, object()),
        book_id,
        narrative_refs=(legacy_id, current_id),
        cipher=_narrative_cipher(),
        repository=repository,  # type: ignore[arg-type]
    )
    assert narratives[legacy_id].purpose == "legacy private"
    assert narratives[legacy_id].amount_sources == ()
    assert narratives[legacy_id].merchant is None
    assert narratives[current_id].amount_sources == current_contract.amount_sources
    assert narratives[current_id].merchant == "current private"
    assert narratives[current_id].net_amount == NarrativeMoney(
        value="10.00",
        asset_code="USD",
    )

    for invalid_id in (malformed_id, noncanonical_id):
        with pytest.raises(
            ProtectedContentUnavailable,
            match="^protected content is unavailable$",
        ) as error:
            get_transaction_narratives(
                cast(Session, object()),
                book_id,
                narrative_refs=(invalid_id,),
                cipher=_narrative_cipher(),
                repository=repository,  # type: ignore[arg-type]
            )
        assert "must-not-escape" not in str(error.value)
        assert "I spent $10.00" not in str(error.value)
