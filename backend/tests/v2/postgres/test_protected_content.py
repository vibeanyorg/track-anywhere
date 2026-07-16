from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from track_anywhere.application.privacy import (
    ImportArchiveProposal,
    ImportArchiveRecordCounts,
    ProtectedContentConflict,
    ProtectedContentService,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.privacy import (
    ImportArchiveManifestRecord,
    ProtectedDescriptionSidecarRecord,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)


BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
OTHER_BOOK_ID = UUID("b682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
ARCHIVE_ID = UUID("11111111-2222-5333-8444-555555555555")
OTHER_ARCHIVE_ID = UUID("21111111-2222-5333-8444-555555555555")
MASTER_KEY = bytes(range(32))
HASHES = {
    "source_dump_hash": b"d" * 32,
    "source_manifest_hash": b"m" * 32,
    "card_review_hash": b"c" * 32,
    "plan_hash": b"p" * 32,
    "archive_content_commitment": b"a" * 32,
}
RECORD_COUNTS = {
    "classification_audit_records": 1,
    "investment_activities": 2,
    "investment_valuations": 0,
    "uncategorized_fx_reporting_facts": 3,
    "institution_metadata_records": 4,
    "counterparty_records": 5,
    "omission_records": 6,
}


def _cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": MASTER_KEY},
        )
    )


class _CountingNonceSource:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, size: int) -> bytes:
        self.calls += 1
        return bytes([self.calls]) * size


def _seed_book(pg_engine, book_id: UUID = BOOK_ID) -> None:
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
                on conflict (asset_code) do nothing
                """
            )
        )
        connection.execute(
            text(
                """
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, 'Privacy test', 'USD', 'active')
                """
            ),
            {"book_id": book_id},
        )


def _service(
    nonce_source: _CountingNonceSource | None = None,
) -> ProtectedContentService:
    return ProtectedContentService(
        cipher=ProtectedContentCipher(
            ProtectedContentKeyring.from_mapping(
                active_key_ref="v1",
                keys={"v1": MASTER_KEY},
            ),
            nonce_source=nonce_source or _CountingNonceSource(),
        ),
        repository=ProtectedContentRepository(),
    )


def _archive_proposal(**updates: object) -> ImportArchiveProposal:
    values: dict[str, object] = {
        "contract_version": 1,
        "book_id": BOOK_ID,
        "archive_id": ARCHIVE_ID,
        "source_dump_hash": HASHES["source_dump_hash"],
        "source_manifest_hash": HASHES["source_manifest_hash"],
        "card_review_hash": HASHES["card_review_hash"],
        "plan_hash": HASHES["plan_hash"],
        "record_counts": ImportArchiveRecordCounts(**RECORD_COUNTS),
        "canonical_ndjson": b'{ "record": 1 }\n',
    }
    values.update(updates)
    return ImportArchiveProposal(**values)


def test_protected_content_models_include_archive_manifests() -> None:
    assert ProtectedDescriptionSidecarRecord.__tablename__ == (
        "protected_description_sidecars"
    )
    assert ImportArchiveManifestRecord.__tablename__ == "import_archive_manifests"


def test_archive_seal_is_keyed_deterministic_and_bound_to_the_public_manifest() -> None:
    cipher = _cipher()

    seal = cipher.commit_archive_seal(
        book_id=BOOK_ID,
        archive_id=ARCHIVE_ID,
        key_ref="v1",
        contract_version=1,
        record_counts=RECORD_COUNTS,
        **HASHES,
    )

    assert len(seal) == 32
    assert seal == cipher.commit_archive_seal(
        book_id=BOOK_ID,
        archive_id=ARCHIVE_ID,
        key_ref="v1",
        contract_version=1,
        record_counts=dict(reversed(RECORD_COUNTS.items())),
        **HASHES,
    )
    assert seal != cipher.commit_archive_seal(
        book_id=OTHER_BOOK_ID,
        archive_id=ARCHIVE_ID,
        key_ref="v1",
        contract_version=1,
        record_counts=RECORD_COUNTS,
        **HASHES,
    )
    assert seal != cipher.commit_archive_seal(
        book_id=BOOK_ID,
        archive_id=OTHER_ARCHIVE_ID,
        key_ref="v1",
        contract_version=1,
        record_counts=RECORD_COUNTS,
        **HASHES,
    )
    changed_counts = dict(RECORD_COUNTS)
    changed_counts["omission_records"] += 1
    assert seal != cipher.commit_archive_seal(
        book_id=BOOK_ID,
        archive_id=ARCHIVE_ID,
        key_ref="v1",
        contract_version=1,
        record_counts=changed_counts,
        **HASHES,
    )


def test_archive_record_counts_are_an_exact_nonnegative_integer_contract() -> None:
    counts = ImportArchiveRecordCounts(**RECORD_COUNTS)

    assert counts.model_dump(mode="python") == RECORD_COUNTS
    for invalid in (
        {**RECORD_COUNTS, "omission_records": True},
        {**RECORD_COUNTS, "omission_records": -1},
        {key: value for key, value in RECORD_COUNTS.items() if key != "omission_records"},
        {**RECORD_COUNTS, "unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            ImportArchiveRecordCounts(**invalid)


def test_archive_proposal_is_strict_frozen_and_keeps_content_out_of_repr() -> None:
    plaintext = b'{"record":1}\n'
    proposal = ImportArchiveProposal(
        contract_version=1,
        book_id=BOOK_ID,
        archive_id=ARCHIVE_ID,
        source_dump_hash=HASHES["source_dump_hash"],
        source_manifest_hash=HASHES["source_manifest_hash"],
        card_review_hash=HASHES["card_review_hash"],
        plan_hash=HASHES["plan_hash"],
        record_counts=ImportArchiveRecordCounts(**RECORD_COUNTS),
        canonical_ndjson=plaintext,
    )

    assert proposal.contract_version == 1
    assert plaintext.decode() not in repr(proposal)
    with pytest.raises(ValidationError):
        ImportArchiveProposal(
            **{
                **proposal.model_dump(mode="python"),
                "source_dump_hash": b"short",
            }
        )
    with pytest.raises(ValidationError):
        ImportArchiveProposal(
            **{
                **proposal.model_dump(mode="python"),
                "contract_version": 2,
            }
        )


def test_create_or_exact_verify_reuses_existing_ciphertext_without_reencrypting(
    pg_engine,
) -> None:
    _seed_book(pg_engine)
    nonce_source = _CountingNonceSource()
    service = _service(nonce_source)
    plaintext = b'{"purpose":"coffee"}'

    with Session(pg_engine) as session, session.begin():
        first = service.create_or_exact_verify(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
            kind="transaction_description",
            canonical_plaintext=plaintext,
        )
        replay = service.create_or_exact_verify(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
            kind="transaction_description",
            canonical_plaintext=plaintext,
        )

    assert replay == first
    assert nonce_source.calls == 1
    assert first.status == "active"
    assert first.nonce == bytes([1]) * 12
    assert first.ciphertext == replay.ciphertext


def test_sidecar_conflicts_are_fail_closed_and_do_not_disclose_plaintext(
    pg_engine,
) -> None:
    _seed_book(pg_engine)
    service = _service()
    original = b'{"purpose":"coffee"}'
    changed = b'{"purpose":"private-changed-value"}'

    with Session(pg_engine) as session, session.begin():
        service.create_or_exact_verify(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
            kind="transaction_description",
            canonical_plaintext=original,
        )
        for kind, plaintext in (
            ("import_archive", original),
            ("transaction_description", changed),
        ):
            with pytest.raises(
                ProtectedContentConflict,
                match="^protected content conflicts with existing data$",
            ) as conflict:
                service.create_or_exact_verify(
                    session,
                    book_id=BOOK_ID,
                    sidecar_id=ARCHIVE_ID,
                    kind=kind,
                    canonical_plaintext=plaintext,
                )
            assert "coffee" not in str(conflict.value)
            assert "private-changed-value" not in str(conflict.value)


def test_erased_sidecar_cannot_be_recreated_and_controlled_erasure_is_idempotent(
    pg_engine,
) -> None:
    _seed_book(pg_engine)
    service = _service()
    plaintext = b'{"purpose":"coffee"}'

    with Session(pg_engine) as session, session.begin():
        service.create_or_exact_verify(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
            kind="transaction_description",
            canonical_plaintext=plaintext,
        )
        erased = service.erase(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
        )
        replayed_erasure = service.erase(
            session,
            book_id=BOOK_ID,
            sidecar_id=ARCHIVE_ID,
        )
        with pytest.raises(ProtectedContentConflict):
            service.create_or_exact_verify(
                session,
                book_id=BOOK_ID,
                sidecar_id=ARCHIVE_ID,
                kind="transaction_description",
                canonical_plaintext=plaintext,
            )

    assert erased.status == replayed_erasure.status == "erased"
    assert erased.ciphertext is erased.nonce is None
    assert erased.key_ref is None
    assert erased.content_hash == replayed_erasure.content_hash


def test_sidecar_get_and_batch_are_strictly_book_scoped(pg_engine) -> None:
    _seed_book(pg_engine)
    _seed_book(pg_engine, OTHER_BOOK_ID)
    service = _service()
    repository = ProtectedContentRepository()

    with Session(pg_engine) as session, session.begin():
        for book_id, plaintext in (
            (BOOK_ID, b'{"book":1}'),
            (OTHER_BOOK_ID, b'{"book":2}'),
        ):
            service.create_or_exact_verify(
                session,
                book_id=book_id,
                sidecar_id=ARCHIVE_ID,
                kind="transaction_description",
                canonical_plaintext=plaintext,
            )
        first_book = repository.get_active_batch(
            session,
            book_id=BOOK_ID,
            sidecar_ids=(ARCHIVE_ID, OTHER_ARCHIVE_ID),
        )

    assert tuple(first_book) == (ARCHIVE_ID,)
    assert first_book[ARCHIVE_ID].book_id == BOOK_ID


def test_archive_create_and_exact_replay_canonicalize_once_and_seal_manifest(
    pg_engine,
) -> None:
    _seed_book(pg_engine)
    nonce_source = _CountingNonceSource()
    service = _service(nonce_source)
    proposal = _archive_proposal()

    with Session(pg_engine) as session, session.begin():
        first = service.create_or_exact_verify_archive(session, archive=proposal)
        replay = service.create_or_exact_verify_archive(
            session,
            archive=_archive_proposal(canonical_ndjson=b'{"record":1}\n'),
        )
        listed = ProtectedContentRepository().list_archive_manifests(
            session,
            book_id=BOOK_ID,
        )

    assert first == replay
    assert listed == (first,)
    assert nonce_source.calls == 1
    assert first.contract_version == 1
    assert first.archive_content_commitment == first.sidecar.content_hash
    assert len(first.seal) == 32


@pytest.mark.parametrize(
    "update",
    (
        {"source_dump_hash": b"x" * 32},
        {
            "record_counts": ImportArchiveRecordCounts(
                **{**RECORD_COUNTS, "omission_records": 7}
            )
        },
        {"canonical_ndjson": b'{"record":"private-changed-value"}\n'},
    ),
)
def test_archive_replay_rejects_every_manifest_or_content_difference(
    pg_engine,
    update: dict[str, object],
) -> None:
    _seed_book(pg_engine)
    service = _service()

    with Session(pg_engine) as session, session.begin():
        service.create_or_exact_verify_archive(session, archive=_archive_proposal())
        with pytest.raises(ProtectedContentConflict) as conflict:
            service.create_or_exact_verify_archive(
                session,
                archive=_archive_proposal(**update),
            )

    assert "private-changed-value" not in str(conflict.value)
