from __future__ import annotations

import multiprocessing
import traceback
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.catalogs.close_account import (
    AccountAlreadyClosed,
    CloseAccount,
    close_account,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    AccountClosed,
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import JournalPostingRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:close-post-race")
EFFECTIVE_AT = datetime(2026, 7, 14, 18, tzinfo=UTC)


def _uow(runtime_url: str):
    engine = create_engine(runtime_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    return engine, lambda: SqlAlchemyUnitOfWork(factory)


def _post_worker(
    runtime_url: str, scenario: tuple[str, str, str], index, start, results
):
    engine, uow_factory = _uow(runtime_url)
    book_id, debit_id, credit_id = map(UUID, scenario)
    try:
        start.wait(timeout=30)
        transaction_id = uuid5(NAMESPACE_URL, f"close-post-race:tx:{book_id}:{index}")
        command = PostTransactionCommand(
            book_id=book_id,
            command_id=uuid5(NAMESPACE_URL, f"close-post-race:cmd:{book_id}:{index}"),
            transaction_id=transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=uuid5(
                        NAMESPACE_URL, f"close-post-race:debit:{book_id}:{index}"
                    ),
                    account_id=debit_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount="1.00",
                ),
                PostTransactionPosting(
                    posting_id=uuid5(
                        NAMESPACE_URL, f"close-post-race:credit:{book_id}:{index}"
                    ),
                    account_id=credit_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="1.00",
                ),
            ),
            effective_at=EFFECTIVE_AT + timedelta(microseconds=index),
        )
        outcome = execute_post_transaction(
            command,
            raw_key=f"close-post-race:{index}",
            actor=ACTOR,
            uow_factory=uow_factory,
        )
        results.put(("post_ok", outcome.result.last_book_position))
    except AccountClosed:
        results.put(("post_closed", index))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        engine.dispose()


def _close_worker(
    runtime_url: str, book_id: str, account_id: str, index, start, results
):
    engine, uow_factory = _uow(runtime_url)
    try:
        start.wait(timeout=30)
        outcome = close_account(
            CloseAccount(book_id=UUID(book_id), account_id=UUID(account_id)),
            actor=ACTOR,
            uow_factory=uow_factory,
        )
        results.put(("close_ok", outcome["as_of_book_position"]))
    except AccountAlreadyClosed:
        results.put(("close_already", index))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        engine.dispose()


def test_close_and_post_share_one_book_serialization_order(
    migrated_postgres_database,
) -> None:
    runtime_url = migrated_postgres_database.runtime_url
    engine = create_engine(runtime_url, pool_pre_ping=True)
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
        actor_subject_id=ACTOR.subject_id,
    )
    seed_journal_scenario(engine, scenario)
    with engine.begin() as connection:
        connection.execute(
            text(
                "update book_members set scopes = "
                '\'["book:write", "ledger:write"]\'::jsonb '
                "where book_id = :book_id and user_id = :user_id"
            ),
            {"book_id": scenario.book_id, "user_id": ACTOR.subject_id},
        )

    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    scenario_ids = tuple(
        map(
            str,
            (scenario.book_id, scenario.debit_account_id, scenario.credit_account_id),
        )
    )
    processes = [
        ctx.Process(
            target=_post_worker,
            args=(runtime_url, scenario_ids, index, start, results),
        )
        for index in range(20)
    ] + [
        ctx.Process(
            target=_close_worker,
            args=(
                runtime_url,
                str(scenario.book_id),
                str(scenario.debit_account_id),
                index,
                start,
                results,
            ),
        )
        for index in range(20)
    ]
    for process in processes:
        process.start()
    try:
        start.set()
        outcomes = [results.get(timeout=180) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    assert not [outcome for outcome in outcomes if outcome[0] == "error"], outcomes
    close_positions = [outcome[1] for outcome in outcomes if outcome[0] == "close_ok"]
    post_positions = [outcome[1] for outcome in outcomes if outcome[0] == "post_ok"]
    assert len(close_positions) == 1
    assert sum(outcome[0] == "close_already" for outcome in outcomes) == 19
    assert (
        len(post_positions) + sum(outcome[0] == "post_closed" for outcome in outcomes)
        == 20
    )
    assert all(position <= close_positions[0] for position in post_positions)
    assert len(post_positions) == close_positions[0]

    with Session(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(LedgerEventRecord)
        ) == len(post_positions)
        assert session.scalar(
            select(func.count()).select_from(JournalPostingRecord)
        ) == 2 * len(post_positions)
    with engine.begin() as connection:
        try:
            connection.execute(
                text(
                    "delete from accounts where book_id = :book_id and account_id = :account_id"
                ),
                {"book_id": scenario.book_id, "account_id": scenario.debit_account_id},
            )
        except DBAPIError:
            pass
        else:
            raise AssertionError(
                "a referenced account must not be physically deletable"
            )
    engine.dispose()
