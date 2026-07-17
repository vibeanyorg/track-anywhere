from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tests.v2.postgres.test_frozen_import_catalog import seed_target_baseline
from backend.tools.frozen_v1_history.reference_reducer import reduce_canonical_plan
from backend.tools.frozen_v1_history.verify import (
    read_frozen_history_observation,
    verify_frozen_history,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    canonical_plan_bytes,
    plan_sha256,
)
from track_anywhere.application.imports.import_frozen_financial_history import (
    import_frozen_financial_history,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.privacy import (
    ProtectedDescriptionSidecarRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.serialization.canonical_json import canonical_json_bytes


TARGET_BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
DETERMINISTIC_ID_HASHES = frozenset(
    {
        "accounts",
        "assets",
        "categories",
        "external_references",
        "journal_postings",
        "journal_transactions",
        "reversals",
    }
)
BALANCE_AND_PROJECTION_HASHES = frozenset(
    {
        "account_balances_semantic",
        "async_projection",
        "balances",
        "cards",
        "reporting",
        "reversal_semantic",
        "synchronous_projection",
        "usdt_postings",
    }
)


def _cipher(*, variant: int) -> ProtectedContentCipher:
    counter = 0

    def unique_nonce(size: int) -> bytes:
        nonlocal counter
        assert size == 12
        counter += 1
        return bytes([variant]) + counter.to_bytes(11, "big")

    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="synthetic",
            keys={"synthetic": bytes(range(32))},
        ),
        nonce_source=unique_nonce,
    )


def _import_and_project(engine, *, plan, cipher: ProtectedContentCipher) -> None:
    seed_target_baseline(engine, plan)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    outcome = import_frozen_financial_history(
        plan,
        expected_plan_hash=plan_sha256(plan),
        raw_key="two-isolated-targets",
        actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        protected_content_cipher=cipher,
    )
    assert outcome.replayed is False

    worker = AsyncProjectionWorker(session_factory, batch_size=31)
    for _ in range(8):
        projection = worker.run_once(plan.target_book_id)
        assert projection.paused is False
        if projection.processed_events == 0:
            assert projection.last_book_position == len(plan.events)
            break
    else:  # pragma: no cover - bounded fail-closed guard
        raise AssertionError("synthetic projection did not converge")


def _event_order_and_payloads(
    session: Session,
) -> tuple[tuple[tuple[int, str], ...], tuple[bytes, ...]]:
    events = tuple(
        session.scalars(
            select(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == TARGET_BOOK_ID)
            .order_by(LedgerEventRecord.book_position)
        )
    )
    order = tuple((event.book_position, str(event.event_id)) for event in events)
    payloads = tuple(
        canonical_json_bytes(
            {
                "event_id": str(event.event_id),
                "event_schema_version": event.event_schema_version,
                "event_type": event.event_type,
                "payload": event.payload,
            }
        )
        for event in events
    )
    return order, payloads


def _protected_envelope_digest(session: Session) -> str:
    rows = tuple(
        session.scalars(
            select(ProtectedDescriptionSidecarRecord)
            .where(ProtectedDescriptionSidecarRecord.book_id == TARGET_BOOK_ID)
            .order_by(ProtectedDescriptionSidecarRecord.sidecar_id)
        )
    )
    digest = hashlib.sha256()
    for row in rows:
        assert row.ciphertext is not None and row.nonce is not None
        digest.update(row.sidecar_id.bytes)
        digest.update(len(row.nonce).to_bytes(2, "big"))
        digest.update(row.nonce)
        digest.update(len(row.ciphertext).to_bytes(8, "big"))
        digest.update(row.ciphertext)
    return digest.hexdigest()


def test_two_isolated_targets_have_identical_semantic_verification_facts(
    migrated_postgres_source_target,
) -> None:
    target_a, target_b = migrated_postgres_source_target
    engine_a = create_engine(target_a.runtime_url, pool_pre_ping=True)
    engine_b = create_engine(target_b.runtime_url, pool_pre_ping=True)
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw_plan = json.loads(canonical_plan_bytes(plan))
    assert type(raw_plan) is dict
    reference = reduce_canonical_plan(raw_plan)
    cipher_a = _cipher(variant=1)
    cipher_b = _cipher(variant=2)
    try:
        _import_and_project(engine_a, plan=plan, cipher=cipher_a)
        _import_and_project(engine_b, plan=plan, cipher=cipher_b)

        with Session(engine_a) as session_a, Session(engine_b) as session_b:
            observation_a = read_frozen_history_observation(
                session_a,
                reference=reference,
                cipher=cipher_a,
            )
            observation_b = read_frozen_history_observation(
                session_b,
                reference=reference,
                cipher=cipher_b,
            )
            event_facts_a = _event_order_and_payloads(session_a)
            event_facts_b = _event_order_and_payloads(session_b)
            envelope_digest_a = _protected_envelope_digest(session_a)
            envelope_digest_b = _protected_envelope_digest(session_b)

        report_a = verify_frozen_history(reference, observation_a)
        report_b = verify_frozen_history(reference, observation_b)

        assert report_a.status == report_b.status == "PASS"
        assert report_a.issues == report_b.issues == ()
        assert dict(report_a.counts) == dict(report_b.counts)
        assert event_facts_a == event_facts_b
        assert observation_a.ledger.terminal_position == len(plan.events)
        assert (
            observation_a.ledger.terminal_hash
            == observation_b.ledger.terminal_hash
            == plan.expected_terminal_hash
        )
        for key in (
            DETERMINISTIC_ID_HASHES
            | BALANCE_AND_PROJECTION_HASHES
            | {
                "events",
                "journal",
            }
        ):
            assert report_a.hashes[key] == report_b.hashes[key]
        assert (
            observation_a.description_aggregate_sha256
            == observation_b.description_aggregate_sha256
        )
        assert (
            observation_a.archive_plaintext_sha256
            == observation_b.archive_plaintext_sha256
        )
        assert (
            observation_a.archive_metadata_hash == observation_b.archive_metadata_hash
        )
        assert observation_a.archive_seal == observation_b.archive_seal
        assert envelope_digest_a != envelope_digest_b
        assert "ciphertext" not in report_a.hashes and "nonce" not in report_a.hashes
        assert dict(report_a.hashes) == dict(report_b.hashes)
    finally:
        engine_b.dispose()
        engine_a.dispose()
