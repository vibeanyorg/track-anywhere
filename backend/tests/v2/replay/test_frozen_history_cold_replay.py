from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tests.v2.postgres.test_frozen_import_catalog import (
    seed_full_catalog,
    seed_target_baseline,
)
from backend.tools.frozen_v1_history import verify as frozen_verify
from backend.tools.frozen_v1_history.verify import (
    FrozenHistoryVerificationError,
    replay_frozen_history_events,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    plan_sha256,
)
from track_anywhere.application.imports.import_frozen_financial_history import (
    import_frozen_financial_history,
)
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.application.privacy.service import ProtectedContentService
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.verification import read_ledger_readback_facts


def _plan():
    return build_valid_fixture_plan(
        target_book_id=UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
    )


def _cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": bytes(range(32))},
        )
    )


def _seed_replay_descriptions(engine, plan, cipher) -> None:
    service = ProtectedContentService(
        cipher=cipher,
        repository=ProtectedContentRepository(),
    )
    with Session(engine) as session, session.begin():
        for description in plan.descriptions:
            service.create_or_exact_verify(
                session,
                book_id=plan.target_book_id,
                sidecar_id=description.sidecar_id,
                kind="transaction_description",
                canonical_plaintext=description.canonical_plaintext,
            )


def _dirty_different_stream_event(plan) -> PendingEvent:
    planned = next(
        event for event in plan.events if event.event_type == "JournalTransactionPosted"
    )
    transaction_id = uuid4()
    raw_payload = planned.payload.model_dump(mode="python")
    raw_payload["transaction_id"] = transaction_id
    postings = raw_payload["postings"]
    assert type(postings) is tuple
    raw_payload["postings"] = tuple(
        {**posting, "posting_id": uuid4()} for posting in postings
    )
    payload = type(planned.payload).model_validate(raw_payload)
    return PendingEvent(
        event_id=uuid4(),
        stream_type="journal_transaction",
        stream_id=transaction_id,
        payload=payload,
        command_id=uuid4(),
        actor_subject_id="offline:cold-replay-dirty-target-test",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=planned.effective_at,
    )


def test_cold_replay_uses_only_the_supported_ledger_committer_path() -> None:
    tree = ast.parse(Path(frozen_verify.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert any(module.endswith("application.ledger_committer") for module in imported)
    assert not any(
        module.endswith("infrastructure.db.event_store")
        or module.endswith("infrastructure.projections.synchronous")
        for module in imported
    )


def test_pg17_cold_replay_reproduces_all_financial_projection_digests(
    migrated_postgres_source_target,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    plan = _plan()
    cipher = _cipher()
    try:
        seed_target_baseline(source_engine, plan)
        seed_target_baseline(target_engine, plan)
        seed_full_catalog(target_engine, plan)
        _seed_replay_descriptions(target_engine, plan, cipher)

        source_factory = sessionmaker(source_engine, expire_on_commit=False)
        import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="cold-replay-source",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(source_factory),
            protected_content_cipher=cipher,
        )

        with Session(source_engine) as source, Session(target_engine) as target:
            with target.begin():
                replay = replay_frozen_history_events(
                    source,
                    target,
                    book_id=plan.target_book_id,
                )
            source_facts = read_ledger_readback_facts(source, plan.target_book_id)
            target_facts = read_ledger_readback_facts(target, plan.target_book_id)

        assert replay.event_count == 176
        assert replay.terminal_hash == plan.expected_terminal_hash
        assert target_facts.terminal_position == source_facts.terminal_position == 176
        assert target_facts.terminal_hash == source_facts.terminal_hash
        for digest in ("events", "journal", "balances", "reversals", "reporting"):
            assert target_facts.hashes[digest] == source_facts.hashes[digest]
    finally:
        target_engine.dispose()
        source_engine.dispose()


def test_pg17_dirty_target_fails_before_append_even_when_caller_catches_and_commits(
    migrated_postgres_source_target,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    plan = _plan()
    cipher = _cipher()
    try:
        seed_target_baseline(source_engine, plan)
        seed_target_baseline(target_engine, plan)
        seed_full_catalog(target_engine, plan)
        _seed_replay_descriptions(target_engine, plan, cipher)

        source_factory = sessionmaker(source_engine, expire_on_commit=False)
        import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="cold-replay-dirty-source",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(source_factory),
            protected_content_cipher=cipher,
        )

        dirty = _dirty_different_stream_event(plan)
        with Session(target_engine) as target, target.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(target, plan.target_book_id)
            committer.append_and_project(
                target,
                locked_head=locked,
                expected_stream_versions={dirty.stream_key: 0},
                events=(dirty,),
            )

        with Session(target_engine) as target:
            before = read_ledger_readback_facts(target, plan.target_book_id)

        caught = False
        with Session(source_engine) as source, Session(target_engine) as target:
            with target.begin():
                try:
                    replay_frozen_history_events(
                        source,
                        target,
                        book_id=plan.target_book_id,
                    )
                except FrozenHistoryVerificationError as error:
                    caught = True
                    assert str(error) == "cold_replay_target_not_empty"

        with Session(target_engine) as target:
            after = read_ledger_readback_facts(target, plan.target_book_id)

        assert caught
        assert before == after
        assert after.terminal_position == 1
    finally:
        target_engine.dispose()
        source_engine.dispose()


def test_pg17_source_hash_mismatch_fails_before_append_when_caller_catches_and_commits(
    migrated_postgres_source_target,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    source_admin_engine = create_engine(source_database.admin_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    plan = _plan()
    cipher = _cipher()
    try:
        seed_target_baseline(source_engine, plan)
        seed_target_baseline(target_engine, plan)
        seed_full_catalog(target_engine, plan)
        _seed_replay_descriptions(target_engine, plan, cipher)

        source_factory = sessionmaker(source_engine, expire_on_commit=False)
        import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="cold-replay-post-append-mismatch-source",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(source_factory),
            protected_content_cipher=cipher,
        )

        with Session(source_admin_engine) as source, source.begin():
            source.execute(text("ALTER TABLE ledger_events DISABLE TRIGGER ALL"))
            stored_events = tuple(
                source.scalars(
                    select(LedgerEventRecord)
                    .where(LedgerEventRecord.book_id == plan.target_book_id)
                    .order_by(LedgerEventRecord.book_position)
                )
            )
            reversed_event_ids = {
                UUID(str(stored.payload["original_event_id"]))
                for stored in stored_events
                if stored.event_type == "JournalTransactionReversed"
            }
            candidate = next(
                stored
                for stored in stored_events
                if stored.event_type == "JournalTransactionPosted"
                and stored.event_id not in reversed_event_ids
            )
            payload = json.loads(json.dumps(candidate.payload))
            payload["kind"] = (
                "transfer" if payload.get("kind") != "transfer" else "adjustment"
            )
            candidate.payload = payload
            source.flush()
            source.execute(text("ALTER TABLE ledger_events ENABLE TRIGGER ALL"))

        caught = False
        with Session(source_engine) as source, Session(target_engine) as target:
            with target.begin():
                try:
                    replay_frozen_history_events(
                        source,
                        target,
                        book_id=plan.target_book_id,
                    )
                except FrozenHistoryVerificationError as error:
                    caught = True
                    assert str(error) == "cold_replay_source_invalid"

        with Session(target_engine) as target:
            head = target.get(BookEventHeadRecord, plan.target_book_id)
            facts = read_ledger_readback_facts(target, plan.target_book_id)

        assert caught
        assert head is not None
        assert head.last_position == 0
        assert head.last_hash == bytes(32)
        assert facts.terminal_position == 0
        for key in (
            "credit_card_transactions",
            "journal_postings",
            "journal_transactions",
            "ledger_events",
            "reporting_lines",
            "reversals",
            "synchronous_projection_applied_events",
        ):
            assert facts.counts[key] == 0
    finally:
        target_engine.dispose()
        source_admin_engine.dispose()
        source_engine.dispose()


def test_pg17_post_append_mismatch_rolls_back_and_clears_lock_capability(
    migrated_postgres_source_target,
    monkeypatch,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    plan = _plan()
    cipher = _cipher()
    try:
        seed_target_baseline(source_engine, plan)
        seed_target_baseline(target_engine, plan)
        seed_full_catalog(target_engine, plan)
        _seed_replay_descriptions(target_engine, plan, cipher)

        source_factory = sessionmaker(source_engine, expire_on_commit=False)
        import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="cold-replay-injected-post-append-mismatch",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(source_factory),
            protected_content_cipher=cipher,
        )

        original_append = LedgerCommitter.append_and_project

        def append_then_report_mismatch(self, session, **kwargs):
            appended = original_append(self, session, **kwargs)
            locked_head = kwargs["locked_head"]
            session.info.setdefault("track_anywhere_v2_book_lock_capabilities", {})[
                locked_head.book_id
            ] = (object(), session.get_transaction())
            return replace(appended, terminal_hash=bytes(32))

        monkeypatch.setattr(
            LedgerCommitter,
            "append_and_project",
            append_then_report_mismatch,
        )

        caught = False
        with Session(source_engine) as source, Session(target_engine) as target:
            with target.begin():
                try:
                    replay_frozen_history_events(
                        source,
                        target,
                        book_id=plan.target_book_id,
                    )
                except FrozenHistoryVerificationError as error:
                    caught = True
                    assert str(error) == "cold_replay_target_mismatch"
                capabilities = target.info.get(
                    "track_anywhere_v2_book_lock_capabilities", {}
                )
                assert plan.target_book_id not in capabilities

        with Session(target_engine) as target:
            head = target.get(BookEventHeadRecord, plan.target_book_id)
            facts = read_ledger_readback_facts(target, plan.target_book_id)

        assert caught
        assert head is not None
        assert head.last_position == 0
        assert head.last_hash == bytes(32)
        assert facts.terminal_position == 0
        assert facts.counts["ledger_events"] == 0
        assert facts.counts["journal_transactions"] == 0
        assert facts.counts["journal_postings"] == 0
    finally:
        target_engine.dispose()
        source_engine.dispose()
