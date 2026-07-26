from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    pending_posted_event,
    posted_event,
    seed_journal_scenario,
)
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.entries import PreparedEntryIntentRecord
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    JournalTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.infrastructure.db.repositories.entries import (
    EverydayEntryDuplicateRepository,
    PreparedEntryIntentRepository,
    ProposedExternalReference,
    ProposedPreparedIntent,
    ProposedSourceFingerprint,
    hash_commit_token,
    hmac_external_reference,
    hmac_source_fingerprint,
)
from track_anywhere.application.privacy import (
    NarrativeAmountSource,
    NarrativeMoney,
    TransactionNarrativeV2,
)
from track_anywhere.application.privacy.service import ProtectedContentService
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)
from track_anywhere.infrastructure.projections.synchronous_appliers.journal import (
    apply_financial_transaction,
)
from track_anywhere.queries.protected_content import get_transaction_narratives
from track_anywhere.serialization.canonical_json import canonical_json_bytes


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPOSITORY_ROOT / "alembic.ini"
ACTOR_ID = "human:everyday-storage"
TOKEN = "entry-commit-token-" + "x" * 32
HMAC_KEY = bytes(range(32))


def _seed_transaction(pg_engine, *, transaction_kind: str = "standard") -> JournalScenario:
    base = JournalScenario.create()
    scenario = JournalScenario(
        book_id=base.book_id,
        debit_account_id=base.debit_account_id,
        credit_account_id=base.credit_account_id,
        transaction_id=base.transaction_id,
        event_id=base.event_id,
        command_id=base.command_id,
        debit_posting_id=base.debit_posting_id,
        credit_posting_id=base.credit_posting_id,
        actor_subject_id=ACTOR_ID,
    )
    seed_journal_scenario(pg_engine, scenario)
    pending = pending_posted_event(scenario)
    payload = posted_event(scenario)
    with Session(pg_engine) as session, session.begin():
        appended = PostgresEventStore()._append_batch(
            session,
            book_id=scenario.book_id,
            expected_stream_versions={pending.stream_key: 0},
            events=(pending,),
        )
        stored = session.get(LedgerEventRecord, appended.event_ids[0])
        assert stored is not None
        session.add(
            SynchronousProjectionAppliedEventRecord(
                book_id=scenario.book_id,
                event_id=stored.event_id,
                projection_version=1,
            )
        )
        apply_financial_transaction(
            session,
            stored,
            transaction_id=scenario.transaction_id,
            transaction_kind=transaction_kind,
            description_ref=None,
            postings=payload.postings,
        )
    return scenario


def _proposal(
    scenario: JournalScenario,
    *,
    intent_id: UUID | None = None,
    token: str = TOKEN,
) -> ProposedPreparedIntent:
    return ProposedPreparedIntent(
        book_id=scenario.book_id,
        actor_id=ACTOR_ID,
        intent_id=intent_id or uuid4(),
        prepared_status="ready",
        commit_token_hash=hash_commit_token(token),
        canonical_payload={
            "kind": "expense",
            "units": "1000",
            "asset_code": "USD",
            "transaction_id": str(scenario.transaction_id),
            "postings": [{"side": "debit"}, {"side": "credit"}],
        },
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def test_migration_accepts_non_card_refund_projection_kind(pg_engine) -> None:
    scenario = _seed_transaction(pg_engine, transaction_kind="refund")

    with Session(pg_engine) as session:
        projected = session.get(
            JournalTransactionRecord,
            (scenario.book_id, scenario.transaction_id),
        )
        assert projected is not None
        assert projected.transaction_kind == "refund"


def test_intent_is_actor_scoped_claimed_once_and_never_enters_events(
    pg_engine,
) -> None:
    scenario = _seed_transaction(pg_engine)
    proposed = _proposal(scenario)
    with Session(pg_engine) as session, session.begin():
        before_events = session.scalar(
            select(func.count()).select_from(LedgerEventRecord)
        )
        inserted = PreparedEntryIntentRepository(session).insert_or_exact_get(proposed)
        after_events = session.scalar(
            select(func.count()).select_from(LedgerEventRecord)
        )
        assert inserted.lifecycle_status == "created"
        assert inserted.commit_token_hash == hash_commit_token(TOKEN)
        assert before_events == after_events == 1

    with Session(pg_engine) as session:
        repository = PreparedEntryIntentRepository(session)
        assert repository.get(
            book_id=scenario.book_id,
            actor_id="human:other-actor",
            intent_id=proposed.intent_id,
        ) is None
        assert repository.claim_ready(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
            commit_token_hash=hash_commit_token(TOKEN + "-wrong"),
            request_id=uuid4(),
            transaction_id=scenario.transaction_id,
        ) is None
        session.rollback()

    request_id = uuid4()
    with Session(pg_engine) as session, session.begin():
        claimed = PreparedEntryIntentRepository(session).claim_ready(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
            commit_token_hash=hash_commit_token(TOKEN),
            request_id=request_id,
            transaction_id=scenario.transaction_id,
        )
        assert claimed is not None
        assert claimed.lifecycle_status == "consumed"
        assert claimed.committed_request_id == request_id

    with Session(pg_engine) as session:
        repository = PreparedEntryIntentRepository(session)
        assert repository.claim_ready(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
            commit_token_hash=hash_commit_token(TOKEN),
            request_id=request_id,
            transaction_id=scenario.transaction_id,
        ) is None
        snapshot = repository.get(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
        )
        assert snapshot is not None
        assert snapshot.lifecycle_status == "consumed"
        assert TOKEN not in repr(snapshot)


def test_claim_rolls_back_with_the_financial_transaction(pg_engine) -> None:
    scenario = _seed_transaction(pg_engine)
    proposed = _proposal(scenario)
    with Session(pg_engine) as session, session.begin():
        PreparedEntryIntentRepository(session).insert_or_exact_get(proposed)

    with pytest.raises(RuntimeError, match="forced financial rollback"):
        with Session(pg_engine) as session, session.begin():
            claimed = PreparedEntryIntentRepository(session).claim_ready(
                book_id=scenario.book_id,
                actor_id=ACTOR_ID,
                intent_id=proposed.intent_id,
                commit_token_hash=hash_commit_token(TOKEN),
                request_id=uuid4(),
                transaction_id=scenario.transaction_id,
            )
            assert claimed is not None
            raise RuntimeError("forced financial rollback")

    with Session(pg_engine) as session:
        snapshot = PreparedEntryIntentRepository(session).get(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
        )
        assert snapshot is not None
        assert snapshot.lifecycle_status == "created"
        assert snapshot.consumed_at is None


def test_concurrent_claim_has_exactly_one_winner(pg_engine) -> None:
    scenario = _seed_transaction(pg_engine)
    proposed = _proposal(scenario)
    with Session(pg_engine) as session, session.begin():
        PreparedEntryIntentRepository(session).insert_or_exact_get(proposed)

    barrier = Barrier(2)

    def claim() -> bool:
        with Session(pg_engine) as session, session.begin():
            barrier.wait(timeout=10)
            return (
                PreparedEntryIntentRepository(session).claim_ready(
                    book_id=scenario.book_id,
                    actor_id=ACTOR_ID,
                    intent_id=proposed.intent_id,
                    commit_token_hash=hash_commit_token(TOKEN),
                    request_id=uuid4(),
                    transaction_id=scenario.transaction_id,
                )
                is not None
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(future.result(timeout=20) for future in (executor.submit(claim), executor.submit(claim)))
    assert sorted(results) == [False, True]


def test_expired_intent_cannot_be_claimed(
    pg_engine,
    migrated_postgres_database,
) -> None:
    scenario = _seed_transaction(pg_engine)
    intent_id = uuid4()
    migrator = create_engine(migrated_postgres_database.migrator_url)
    try:
        with migrator.begin() as connection:
            connection.execute(
                text(f'SET ROLE "{migrated_postgres_database.owner_role}"')
            )
            connection.execute(
                text(
                    """
                    insert into prepared_entry_intents (
                        book_id, intent_id, actor_id, contract_version,
                        prepared_status, lifecycle_status, commit_token_hash,
                        canonical_payload, expires_at, created_at
                    ) values (
                        :book_id, :intent_id, :actor_id, 1, 'ready', 'created',
                        :token_hash, '{"kind":"expense","units":"1000"}'::jsonb,
                        clock_timestamp() - interval '1 minute',
                        clock_timestamp() - interval '2 minutes'
                    )
                    """
                ),
                {
                    "book_id": scenario.book_id,
                    "intent_id": intent_id,
                    "actor_id": ACTOR_ID,
                    "token_hash": hash_commit_token(TOKEN),
                },
            )
    finally:
        migrator.dispose()

    with Session(pg_engine) as session:
        assert PreparedEntryIntentRepository(session).claim_ready(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=intent_id,
            commit_token_hash=hash_commit_token(TOKEN),
            request_id=uuid4(),
            transaction_id=scenario.transaction_id,
        ) is None
        snapshot = PreparedEntryIntentRepository(session).get(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=intent_id,
        )
        assert snapshot is not None
        assert snapshot.lifecycle_status == "created"


def test_strong_reference_and_soft_fingerprint_store_only_hmacs(pg_engine) -> None:
    scenario = _seed_transaction(pg_engine)
    proposed_intent = _proposal(scenario)
    with Session(pg_engine) as session, session.begin():
        PreparedEntryIntentRepository(session).insert_or_exact_get(proposed_intent)

    raw_reference = "private-order-20260724"
    reference_hmac = hmac_external_reference(
        key=HMAC_KEY,
        provider_code="merchant",
        reference_kind="provider_order",
        reference=raw_reference,
    )
    reference_proposal = ProposedExternalReference(
        book_id=scenario.book_id,
        transaction_id=scenario.transaction_id,
        source_intent_id=proposed_intent.intent_id,
        provider_code="merchant",
        reference_kind="provider_order",
        reference_hmac=reference_hmac,
    )
    with Session(pg_engine) as session:
        with pytest.raises(DBAPIError) as error:
            EverydayEntryDuplicateRepository(
                session
            ).insert_external_reference_or_get(reference_proposal)
            session.commit()
        assert getattr(error.value.orig, "sqlstate", "") == "23514"
        session.rollback()

    with Session(pg_engine) as session, session.begin():
        claimed = PreparedEntryIntentRepository(session).claim_ready(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed_intent.intent_id,
            commit_token_hash=hash_commit_token(TOKEN),
            request_id=uuid4(),
            transaction_id=scenario.transaction_id,
        )
        assert claimed is not None

        duplicate_repository = EverydayEntryDuplicateRepository(session)
        reference, created = duplicate_repository.insert_external_reference_or_get(
            reference_proposal
        )
        replay, replay_created = (
            duplicate_repository.insert_external_reference_or_get(
                ProposedExternalReference(
                    book_id=scenario.book_id,
                    transaction_id=scenario.transaction_id,
                    source_intent_id=proposed_intent.intent_id,
                    provider_code="merchant",
                    reference_kind="provider_order",
                    reference_hmac=reference_hmac,
                )
            )
        )
        assert created is True
        assert replay_created is False
        assert replay == reference
        assert raw_reference not in repr(reference)

        raw_fingerprint_part = "private-source-text"
        fingerprint_hmac = hmac_source_fingerprint(
            key=HMAC_KEY,
            normalized_parts=(
                "merchant",
                raw_fingerprint_part,
                "1000",
                "USD",
            ),
        )
        fingerprint = duplicate_repository.insert_source_fingerprint(
            ProposedSourceFingerprint(
                book_id=scenario.book_id,
                transaction_id=scenario.transaction_id,
                source_intent_id=proposed_intent.intent_id,
                fingerprint_hmac=fingerprint_hmac,
            )
        )
        matches = duplicate_repository.find_source_fingerprints(
            book_id=scenario.book_id,
            fingerprint_hmac=fingerprint_hmac,
            created_since=datetime.now(UTC) - timedelta(minutes=1),
        )
        assert matches == (fingerprint,)
        assert raw_fingerprint_part not in repr(matches)

    with pg_engine.connect() as connection:
        columns = {
            row.column_name
            for row in connection.execute(
                text(
                    """
                    select column_name
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name in (
                           'everyday_entry_external_references',
                           'everyday_entry_source_fingerprints'
                       )
                    """
                )
            )
        }
    assert "reference_value" not in columns
    assert "source_text" not in columns
    assert {"reference_hmac", "fingerprint_hmac"} <= columns


def test_transaction_narrative_v2_round_trips_through_encrypted_persistence(
    pg_engine,
) -> None:
    scenario = _seed_transaction(pg_engine)
    sidecar_id = uuid4()
    legacy_sidecar_id = uuid4()
    narrative = TransactionNarrativeV2(
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
        merchant="Encrypted Merchant",
        note="encrypted note",
        net_amount=NarrativeMoney(value="10.00", asset_code="USD"),
    )
    plaintext = canonical_json_bytes(narrative.model_dump(mode="json"))
    cipher = ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v2",
            keys={"v2": bytes(range(32))},
        ),
        nonce_source=lambda size: b"n" * size,
    )
    repository = ProtectedContentRepository()

    with Session(pg_engine) as session, session.begin():
        persisted = ProtectedContentService(
            cipher=cipher,
            repository=repository,
        ).create_or_exact_verify(
            session,
            book_id=scenario.book_id,
            sidecar_id=sidecar_id,
            kind="transaction_narrative_v2",
            canonical_plaintext=plaintext,
        )
        assert persisted.kind == "transaction_narrative_v2"
        assert b"I spent $10.00" not in (persisted.ciphertext or b"")
        assert b"gross was $12.00" not in (persisted.ciphertext or b"")
        assert b"Encrypted Merchant" not in (persisted.ciphertext or b"")
        ProtectedContentService(
            cipher=cipher,
            repository=repository,
        ).create_or_exact_verify(
            session,
            book_id=scenario.book_id,
            sidecar_id=legacy_sidecar_id,
            kind="transaction_description",
            canonical_plaintext=canonical_json_bytes(
                {
                    "line_memos": [],
                    "purpose": None,
                    "transaction_memo": None,
                }
            ),
        )
        intent = _proposal(scenario, intent_id=uuid4())
        PreparedEntryIntentRepository(session).insert_or_exact_get(
            ProposedPreparedIntent(
                book_id=intent.book_id,
                actor_id=intent.actor_id,
                intent_id=intent.intent_id,
                prepared_status=intent.prepared_status,
                commit_token_hash=intent.commit_token_hash,
                canonical_payload=intent.canonical_payload,
                expires_at=intent.expires_at,
                protected_content_ref=sidecar_id,
            )
        )

    invalid_intent = _proposal(scenario, intent_id=uuid4(), token=TOKEN + "-legacy")
    with Session(pg_engine) as session:
        with pytest.raises(DBAPIError) as error:
            PreparedEntryIntentRepository(session).insert_or_exact_get(
                ProposedPreparedIntent(
                    book_id=invalid_intent.book_id,
                    actor_id=invalid_intent.actor_id,
                    intent_id=invalid_intent.intent_id,
                    prepared_status=invalid_intent.prepared_status,
                    commit_token_hash=invalid_intent.commit_token_hash,
                    canonical_payload=invalid_intent.canonical_payload,
                    expires_at=invalid_intent.expires_at,
                    protected_content_ref=legacy_sidecar_id,
                )
            )
            session.commit()
        assert getattr(error.value.orig, "sqlstate", "") == "23514"
        session.rollback()

    with Session(pg_engine) as session:
        decoded = get_transaction_narratives(
            session,
            scenario.book_id,
            narrative_refs=(sidecar_id,),
            cipher=cipher,
            repository=repository,
        )
        assert decoded[sidecar_id].merchant == "Encrypted Merchant"
        assert decoded[sidecar_id].amount_sources == narrative.amount_sources
        assert decoded[sidecar_id].net_amount == NarrativeMoney(
            value="10.00",
            asset_code="USD",
        )


def test_intent_payload_and_terminal_state_are_database_immutable(pg_engine) -> None:
    scenario = _seed_transaction(pg_engine)
    proposed = _proposal(scenario)
    with Session(pg_engine) as session, session.begin():
        PreparedEntryIntentRepository(session).insert_or_exact_get(proposed)

    with pg_engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(DBAPIError) as error:
            connection.execute(
                text(
                    """
                    insert into prepared_entry_intents (
                        book_id, intent_id, actor_id, contract_version,
                        prepared_status, lifecycle_status, commit_token_hash,
                        canonical_payload, expires_at
                    ) values (
                        :book_id, :intent_id, :actor_id, 1,
                        'ready', 'created', null, '{"kind":"expense"}'::jsonb,
                        clock_timestamp() + interval '10 minutes'
                    )
                    """
                ),
                {
                    "book_id": scenario.book_id,
                    "intent_id": uuid4(),
                    "actor_id": ACTOR_ID,
                },
            )
        assert getattr(error.value.orig, "sqlstate", "") == "23514"
        transaction.rollback()

    with pg_engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(DBAPIError) as error:
            connection.execute(
                text(
                    """
                    update prepared_entry_intents
                       set canonical_payload = '{"kind":"transfer"}'::jsonb
                     where book_id = :book_id and intent_id = :intent_id
                    """
                ),
                {"book_id": scenario.book_id, "intent_id": proposed.intent_id},
            )
        assert getattr(error.value.orig, "sqlstate", "") == "42501"
        transaction.rollback()

    with Session(pg_engine) as session, session.begin():
        cancelled = PreparedEntryIntentRepository(session).cancel(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
        )
        assert cancelled is not None
    with Session(pg_engine) as session, session.begin():
        assert PreparedEntryIntentRepository(session).cancel(
            book_id=scenario.book_id,
            actor_id=ACTOR_ID,
            intent_id=proposed.intent_id,
        ) is None


def test_category_usage_kind_has_safe_historical_default_and_constraint(
    pg_engine,
) -> None:
    scenario = _seed_transaction(pg_engine)
    category_id = uuid4()
    version_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into categories (
                    book_id, category_id, current_name, status
                ) values (:book_id, :category_id, 'Historical', 'active')
                """
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                """
                insert into category_versions (
                    book_id, category_id, category_version_id,
                    name, status, change_reason_code
                ) values (
                    :book_id, :category_id, :version_id,
                    'Historical', 'active', 'created'
                )
                """
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
        assert connection.execute(
            text(
                """
                select usage_kind from category_versions
                 where book_id = :book_id
                   and category_id = :category_id
                   and category_version_id = :version_id
                """
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        ).scalar_one() == "both"
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(DBAPIError) as error:
            connection.execute(
                text(
                    """
                    insert into category_versions (
                        book_id, category_id, category_version_id,
                        name, status, usage_kind, change_reason_code
                    ) values (
                        :book_id, :category_id, :version_id,
                        'Invalid', 'active', 'transfer', 'created'
                    )
                    """
                ),
                {
                    "book_id": scenario.book_id,
                    "category_id": category_id,
                    "version_id": uuid4(),
                },
            )
        assert getattr(error.value.orig, "sqlstate", "") == "23514"
        transaction.rollback()


def test_v2_0014_is_reversible_to_0013_and_reapplies(
    migrated_postgres_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TRACK_ANYWHERE_DATABASE_URL",
        migrated_postgres_database.migrator_url,
    )
    monkeypatch.setenv(
        "TRACK_ANYWHERE_DB_RUNTIME_ROLE",
        migrated_postgres_database.runtime_role,
    )
    config = Config(str(ALEMBIC_INI))

    command.downgrade(config, "v2_0013_frozen_import_fence")
    inspector = create_engine(migrated_postgres_database.migrator_url)
    try:
        with inspector.connect() as connection:
            assert connection.scalar(
                text(
                    "select to_regclass('public.prepared_entry_intents') is null"
                )
            )
            assert connection.scalar(
                text(
                    """
                    select count(*) = 0
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name = 'category_versions'
                       and column_name = 'usage_kind'
                    """
                )
            )
    finally:
        inspector.dispose()

    command.upgrade(config, "head")
    runtime = create_engine(migrated_postgres_database.runtime_url)
    try:
        with runtime.connect() as connection:
            assert connection.scalar(
                text(
                    "select to_regclass('public.prepared_entry_intents') is not null"
                )
            )
    finally:
        runtime.dispose()
