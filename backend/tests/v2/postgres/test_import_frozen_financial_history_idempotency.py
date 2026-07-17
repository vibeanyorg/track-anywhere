from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.imports._plan_factory import (
    build_valid_fixture_plan,
    fixture_id,
)
from backend.tests.v2.postgres.test_frozen_import_catalog import seed_target_baseline
from backend.tests.v2.postgres.test_import_frozen_financial_history import (
    _cipher,
    _fixed_synthetic_plan,
)
import track_anywhere.application.imports.import_frozen_financial_history as frozen_import
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    plan_sha256,
)
from track_anywhere.application.idempotency import CommandActor, IdempotencyConflict
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.privacy import (
    ImportArchiveManifestRecord,
    ProtectedDescriptionSidecarRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def _row_counts(session: Session) -> tuple[int, ...]:
    return tuple(
        int(session.scalar(select(func.count()).select_from(model)) or 0)
        for model in (
            LedgerEventRecord,
            ProtectedDescriptionSidecarRecord,
            ImportArchiveManifestRecord,
            CommandReceiptRecord,
        )
    )


def test_exact_replay_after_simulated_commit_response_loss_inserts_zero_rows(
    pg_engine,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)

    class SimulatedCommitResponseLoss(RuntimeError):
        pass

    class LostResponseUnitOfWork(SqlAlchemyUnitOfWork):
        def __exit__(self, exc_type, exc, traceback):
            result = super().__exit__(exc_type, exc, traceback)
            if exc_type is None:
                raise SimulatedCommitResponseLoss("commit response was lost")
            return result

    common = {
        "expected_plan_hash": plan_sha256(plan),
        "raw_key": "frozen-import-receipt",
        "actor": CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        "protected_content_cipher": _cipher(),
    }

    with pytest.raises(SimulatedCommitResponseLoss, match="commit response was lost"):
        frozen_import.import_frozen_financial_history(
            plan,
            **common,
            uow_factory=lambda: LostResponseUnitOfWork(session_factory),
        )
    with Session(pg_engine) as session:
        before = _row_counts(session)
        first_head = session.get(BookEventHeadRecord, plan.target_book_id)
        assert first_head is not None
        first_terminal = (first_head.last_position, first_head.last_hash)

    replay = frozen_import.import_frozen_financial_history(
        plan,
        **common,
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
    )

    assert replay.replayed is True
    assert replay.result.first_book_position == 1
    assert replay.result.last_book_position == 176
    assert replay.result.body["plan_hash"] == plan_sha256(plan)
    with Session(pg_engine) as session:
        assert _row_counts(session) == before
        replay_head = session.get(BookEventHeadRecord, plan.target_book_id)
        assert replay_head is not None
        assert (replay_head.last_position, replay_head.last_hash) == first_terminal


def test_same_receipt_key_rejects_a_different_structurally_valid_plan(
    pg_engine,
) -> None:
    plan = _fixed_synthetic_plan()
    altered = build_valid_fixture_plan(
        target_book_id=plan.target_book_id,
        command_id=fixture_id(987_654),
    )
    assert plan_sha256(altered) != plan_sha256(plan)
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    actor = CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID)

    def uow_factory():
        return SqlAlchemyUnitOfWork(session_factory)

    frozen_import.import_frozen_financial_history(
        plan,
        expected_plan_hash=plan_sha256(plan),
        raw_key="frozen-import-receipt",
        actor=actor,
        uow_factory=uow_factory,
        protected_content_cipher=_cipher(),
    )
    with Session(pg_engine) as session:
        before = _row_counts(session)

    with pytest.raises(IdempotencyConflict):
        frozen_import.import_frozen_financial_history(
            altered,
            expected_plan_hash=plan_sha256(altered),
            raw_key="frozen-import-receipt",
            actor=actor,
            uow_factory=uow_factory,
            protected_content_cipher=_cipher(),
        )

    with Session(pg_engine) as session:
        assert _row_counts(session) == before


def test_replay_never_reopens_the_retired_alias(pg_engine) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    alias = next(account for account in plan.accounts if account.close_after_import)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    arguments = {
        "expected_plan_hash": plan_sha256(plan),
        "raw_key": "frozen-import-receipt",
        "actor": CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        "uow_factory": lambda: SqlAlchemyUnitOfWork(session_factory),
        "protected_content_cipher": _cipher(),
    }

    frozen_import.import_frozen_financial_history(plan, **arguments)
    replay = frozen_import.import_frozen_financial_history(plan, **arguments)

    assert replay.replayed is True
    with Session(pg_engine) as session:
        stored = session.get(
            AccountRecord,
            (plan.target_book_id, alias.account_id),
        )
        assert stored is not None and stored.status == "closed"


def test_existing_partial_financial_prefix_is_rejected_without_appending(
    pg_engine,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    planned_prefix = plan.events[0]
    prefix = frozen_import._pending_event(planned_prefix)
    prefix = replace(
        prefix,
        payload=planned_prefix.payload.model_copy(update={"description_ref": None}),
    )
    with Session(pg_engine) as session, session.begin():
        committer = LedgerCommitter()
        locked = committer.execute_under_book_lock(session, plan.target_book_id)
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={prefix.stream_key: 0},
            events=(prefix,),
        )

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(
        frozen_import.FrozenFinancialHistoryImportError,
        match="fixed contract",
    ):
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            protected_content_cipher=_cipher(),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, plan.target_book_id)
        assert head is not None and head.last_position == 1
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
