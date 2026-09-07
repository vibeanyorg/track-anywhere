from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    pending_posted_event,
    posted_event,
    projection_state,
    seed_journal_scenario,
)
from track_anywhere.application.catalogs.rename_account import (
    RenameAccount,
    SystemManagedAccount,
    rename_account,
)
from track_anywhere.application.idempotency import CommandActor, IdempotencyConflict
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.journal.models import TransactionKind
from track_anywhere.infrastructure.db.models.catalog import (
    AccountMutationRecord,
    AccountRecord,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.queries.catalogs import get_account
from track_anywhere.queries.everyday_entries import get_everyday_entry


def test_rename_is_audited_idempotent_and_preserves_financial_facts(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(
            text("update book_members set scopes='[\"book:write\"]' where book_id=:book_id"),
            {"book_id": scenario.book_id},
        )
    committer = LedgerCommitter()
    with Session(pg_engine) as session, session.begin():
        locked = committer.execute_under_book_lock(session, scenario.book_id)
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={("journal_transaction", scenario.transaction_id): 0},
            events=(
                pending_posted_event(
                    scenario,
                    payload=posted_event(scenario).model_copy(
                        update={"kind": TransactionKind.TRANSFER}
                    ),
                ),
            ),
        )
    with Session(pg_engine) as session:
        facts_before = projection_state(session, scenario)
        hashes_before = tuple(
            session.scalars(
                select(LedgerEventRecord.event_hash).where(
                    LedgerEventRecord.book_id == scenario.book_id
                )
            )
        )

    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    request_id = uuid4()
    command = RenameAccount(
        book_id=scenario.book_id,
        account_id=scenario.credit_account_id,
        current_name="  交通银行信用卡(1945) - CNY  ",
        request_id=request_id,
    )
    renamed, replayed = rename_account(
        command,
        actor=CommandActor(scenario.actor_subject_id),
        uow_factory=uow_factory,
    )
    replay, replayed_again = rename_account(
        command,
        actor=CommandActor(scenario.actor_subject_id),
        uow_factory=uow_factory,
    )

    assert replayed is False
    assert replayed_again is True
    assert replay == renamed
    assert renamed["current_name"] == "交通银行信用卡(1945) - CNY"
    assert renamed["version"] == 2
    with Session(pg_engine) as session:
        assert get_account(
            session, scenario.book_id, scenario.credit_account_id
        ).current_name == "交通银行信用卡(1945) - CNY"
        entry = get_everyday_entry(
            session, scenario.book_id, scenario.transaction_id
        )
        displays = {
            value.account_id: value.display_name
            for value in (
                entry.source_account,
                entry.target_account,
                entry.payment_account,
            )
            if value is not None
        }
        assert displays[scenario.credit_account_id] == "交通银行信用卡(1945) - CNY"
        assert projection_state(session, scenario) == facts_before
        assert tuple(
            session.scalars(
                select(LedgerEventRecord.event_hash).where(
                    LedgerEventRecord.book_id == scenario.book_id
                )
            )
        ) == hashes_before
        receipt = session.get(AccountMutationRecord, (scenario.book_id, request_id))
        assert receipt is not None
        assert receipt.before["current_name"] == "Credit"
        assert receipt.after == renamed

    with pytest.raises(IdempotencyConflict):
        rename_account(
            RenameAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
                current_name="conflicting retry",
                request_id=request_id,
            ),
            actor=CommandActor(scenario.actor_subject_id),
            uow_factory=uow_factory,
        )
    with pytest.raises(PermissionError):
        rename_account(
            RenameAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
                current_name="unauthorized",
                request_id=uuid4(),
            ),
            actor=CommandActor("human:outsider"),
            uow_factory=uow_factory,
        )


def test_system_account_cannot_be_renamed(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario, credit_account_type="system")
    with pg_engine.begin() as connection:
        connection.execute(
            text("update book_members set scopes='[\"book:write\"]' where book_id=:book_id"),
            {"book_id": scenario.book_id},
        )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(SystemManagedAccount):
        rename_account(
            RenameAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
                current_name="forbidden",
                request_id=uuid4(),
            ),
            actor=CommandActor(scenario.actor_subject_id),
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        )
    with Session(pg_engine) as session:
        account = session.get(
            AccountRecord, (scenario.book_id, scenario.credit_account_id)
        )
        assert account is not None and account.current_name == "Credit"
