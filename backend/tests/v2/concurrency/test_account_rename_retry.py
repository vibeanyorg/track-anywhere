from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.catalogs.rename_account import (
    RenameAccount,
    rename_account,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.infrastructure.db.models.catalog import (
    AccountMutationRecord,
    AccountRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def test_concurrent_exact_retry_and_later_rename_preserve_one_mutation(pg_engine):
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update book_members set scopes='[\"book:write\"]' where book_id=:book_id"
            ),
            {"book_id": scenario.book_id},
        )
    sessions = sessionmaker(pg_engine, expire_on_commit=False)
    actor = CommandActor(scenario.actor_subject_id)
    command = RenameAccount(
        scenario.book_id, scenario.credit_account_id, "1945 - CNY", uuid4()
    )
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return rename_account(
            command, actor=actor, uow_factory=lambda: SqlAlchemyUnitOfWork(sessions)
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = list(workers.map(lambda _: submit(), range(2)))
    assert sorted([first[1], second[1]]) == [False, True]
    assert first[0] == second[0]
    with Session(pg_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountMutationRecord)
                .where(AccountMutationRecord.book_id == scenario.book_id)
            )
            == 1
        )
        assert (
            session.get(
                AccountRecord, (scenario.book_id, scenario.credit_account_id)
            ).version
            == 2
        )

    rename_account(
        RenameAccount(
            scenario.book_id, scenario.credit_account_id, "Later name", uuid4()
        ),
        actor=actor,
        uow_factory=lambda: SqlAlchemyUnitOfWork(sessions),
    )
    replay, replayed = rename_account(
        command, actor=actor, uow_factory=lambda: SqlAlchemyUnitOfWork(sessions)
    )
    assert replayed and replay["current_name"] == "1945 - CNY"
    with Session(pg_engine) as session:
        account = session.get(
            AccountRecord, (scenario.book_id, scenario.credit_account_id)
        )
        assert account.current_name == "Later name"
        assert account.version == 3
