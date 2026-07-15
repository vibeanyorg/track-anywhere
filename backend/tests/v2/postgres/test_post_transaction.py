from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    AccountClosed,
    JournalWriteForbidden,
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.ledger_committer import BookWritePaused, LedgerCommitter
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.money import MoneyError
from track_anywhere.infrastructure.db.event_store import StreamVersionConflict
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.auth import BookMemberRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.repositories.catalogs import CatalogRepository
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 14, 12, 30, tzinfo=UTC)


def _command(
    scenario: JournalScenario,
    *,
    kind: TransactionKind = TransactionKind.STANDARD,
    amount: object = "12.34",
    expected_stream_version: int = 0,
) -> PostTransactionCommand:
    return PostTransactionCommand(
        book_id=scenario.book_id,
        command_id=scenario.command_id,
        transaction_id=scenario.transaction_id,
        expected_stream_version=expected_stream_version,
        kind=kind,
        postings=(
            PostTransactionPosting(
                posting_id=scenario.debit_posting_id,
                account_id=scenario.debit_account_id,
                asset_code="USD",
                side=PostingSide.DEBIT,
                amount=amount,  # type: ignore[arg-type]
            ),
            PostTransactionPosting(
                posting_id=scenario.credit_posting_id,
                account_id=scenario.credit_account_id,
                asset_code="USD",
                side=PostingSide.CREDIT,
                amount=amount,  # type: ignore[arg-type]
            ),
        ),
        effective_at=EFFECTIVE_AT,
    )


def _execute(pg_engine, scenario: JournalScenario, command: PostTransactionCommand):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    return execute_post_transaction(
        command,
        raw_key=f"post:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
    )


def _seed_usdt_accounts(pg_engine, scenario: JournalScenario) -> JournalScenario:
    usdt_scenario = replace(
        scenario,
        debit_account_id=uuid4(),
        credit_account_id=uuid4(),
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into assets ("
                "asset_code, kind, ledger_scale, input_scale, display_scale, "
                "current_name, status"
                ") values ('USDT', 'crypto', 8, 6, 6, 'Tether', 'active')"
            )
        )
        for account_id, name in (
            (usdt_scenario.debit_account_id, "USDT debit"),
            (usdt_scenario.credit_account_id, "USDT credit"),
        ):
            connection.execute(
                text(
                    "insert into accounts ("
                    "book_id, account_id, asset_code, account_type, current_name, status"
                    ") values (:book_id, :account_id, 'USDT', 'asset', :name, 'active')"
                ),
                {
                    "book_id": scenario.book_id,
                    "account_id": account_id,
                    "name": name,
                },
            )
    return usdt_scenario


@pytest.mark.parametrize(
    "kind",
    [
        TransactionKind.STANDARD,
        TransactionKind.OPENING,
        TransactionKind.ADJUSTMENT,
        TransactionKind.TRANSFER,
    ],
)
def test_posts_supported_journal_kinds_and_projects_exact_units(
    pg_engine,
    kind: TransactionKind,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)

    outcome = _execute(pg_engine, scenario, _command(scenario, kind=kind))

    assert outcome.replayed is False
    assert outcome.result.status_code == 201
    assert outcome.result.body == {
        "transaction_id": str(scenario.transaction_id),
        "as_of_book_position": 1,
    }
    assert (outcome.result.first_book_position, outcome.result.last_book_position) == (
        1,
        1,
    )
    with Session(pg_engine) as session:
        event = session.scalar(select(LedgerEventRecord))
        assert event is not None
        assert (event.event_type, event.event_schema_version) == (
            "JournalTransactionPosted",
            1,
        )
        assert event.stream_version == 1
        assert event.effective_at == EFFECTIVE_AT
        assert event.payload["kind"] == kind.value
        assert [posting["units"] for posting in event.payload["postings"]] == [
            "1234",
            "1234",
        ]
        assert [posting["position"] for posting in event.payload["postings"]] == [0, 1]

        transaction = session.get(
            JournalTransactionRecord,
            (scenario.book_id, scenario.transaction_id),
        )
        assert transaction is not None
        assert transaction.transaction_kind == kind.value
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord).order_by(
                    JournalPostingRecord.posting_position
                )
            )
        )
        assert tuple(int(posting.units) for posting in postings) == (1234, 1234)
        balances = {
            balance.account_id: int(balance.balance_units)
            for balance in session.scalars(select(AccountBalanceRecord))
        }
        assert balances == {
            scenario.debit_account_id: 1234,
            scenario.credit_account_id: -1234,
        }
        receipt = session.scalar(select(CommandReceiptRecord))
        assert receipt is not None
        assert (
            receipt.status,
            receipt.result_status,
            receipt.result_body,
            receipt.first_book_position,
            receipt.last_book_position,
        ) == (
            "completed",
            201,
            outcome.result.body,
            1,
            1,
        )


def test_replay_returns_the_stored_response_without_appending_again(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    command = _command(scenario)

    first = _execute(pg_engine, scenario, command)
    second = _execute(pg_engine, scenario, command)

    assert second.replayed is True
    assert second.result == first.result
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1


@pytest.mark.parametrize("raw", ["1e2", "1.234", "01.", ".1", "0", 12.34])
def test_rejects_inexact_or_non_string_amounts_without_partial_writes(
    pg_engine,
    raw: object,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)

    with pytest.raises((MoneyError, ValueError)):
        _execute(pg_engine, scenario, _command(scenario, amount=raw))

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 0


def test_rejects_closed_accounts_after_the_book_head_lock(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update accounts set status = 'closed' "
                "where book_id = :book_id and account_id = :account_id"
            ),
            {"book_id": scenario.book_id, "account_id": scenario.debit_account_id},
        )
    order: list[str] = []
    original_lock = LedgerCommitter.execute_under_book_lock
    original_account = CatalogRepository.get_account

    def traced_lock(self, session, book_id):
        order.append("book_lock")
        return original_lock(self, session, book_id)

    def traced_account(self, book_id, account_id, *, lock):
        order.append("account_read")
        return original_account(self, book_id, account_id, lock=lock)

    monkeypatch.setattr(LedgerCommitter, "execute_under_book_lock", traced_lock)
    monkeypatch.setattr(CatalogRepository, "get_account", traced_account)

    with pytest.raises(AccountClosed):
        _execute(pg_engine, scenario, _command(scenario))

    assert order[0] == "book_lock"
    assert "account_read" in order[1:]


def test_general_journal_rejects_credit_card_accounts_without_partial_writes(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        pg_engine,
        scenario,
        credit_account_type="liability",
        credit_account_subtype="credit_card",
    )

    with pytest.raises(RuntimeError, match="credit-card semantic command"):
        _execute(pg_engine, scenario, _command(scenario))

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 0


def test_locked_account_read_refreshes_a_stale_identity_map(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)

    class PreloadingUnitOfWork(SqlAlchemyUnitOfWork):
        def __enter__(self):
            entered = super().__enter__()
            cached = self.session.get(
                AccountRecord,
                (scenario.book_id, scenario.debit_account_id),
            )
            assert cached is not None and cached.status == "active"
            with pg_engine.begin() as connection:
                connection.execute(
                    text(
                        "update accounts set status = 'closed' "
                        "where book_id = :book_id and account_id = :account_id"
                    ),
                    {
                        "book_id": scenario.book_id,
                        "account_id": scenario.debit_account_id,
                    },
                )
            assert cached.status == "active"
            return entered

    with pytest.raises(AccountClosed):
        execute_post_transaction(
            _command(scenario),
            raw_key="stale-account-cache",
            actor=CommandActor(subject_id=scenario.actor_subject_id),
            uow_factory=lambda: PreloadingUnitOfWork(factory),
        )


@pytest.mark.parametrize(
    ("raw", "expected_units"),
    [("0.123456", "12345600"), ("1", "100000000")],
)
def test_uses_the_locked_asset_online_scale_policy(
    pg_engine,
    raw: str,
    expected_units: str,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    scenario = _seed_usdt_accounts(pg_engine, scenario)
    command = _command(scenario, amount=raw)
    command = replace(
        command,
        postings=tuple(
            replace(posting, asset_code="USDT") for posting in command.postings
        ),
    )

    _execute(pg_engine, scenario, command)

    with Session(pg_engine) as session:
        event = session.scalar(select(LedgerEventRecord))
        assert event is not None
        assert [posting["units"] for posting in event.payload["postings"]] == [
            expected_units,
            expected_units,
        ]


def test_rejects_fractional_digits_beyond_asset_input_scale(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    scenario = _seed_usdt_accounts(pg_engine, scenario)
    command = _command(scenario, amount="0.1234560")
    command = replace(
        command,
        postings=tuple(
            replace(posting, asset_code="USDT") for posting in command.postings
        ),
    )

    with pytest.raises(MoneyError):
        _execute(pg_engine, scenario, command)

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0


def test_rejects_paused_book_before_reading_accounts(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update books set write_state = 'paused_integrity' where book_id = :id"
            ),
            {"id": scenario.book_id},
        )
    account_reads = 0

    def forbidden_account_read(*args, **kwargs):
        nonlocal account_reads
        account_reads += 1
        raise AssertionError("account state was read before paused Book rejection")

    monkeypatch.setattr(CatalogRepository, "get_account", forbidden_account_read)

    with pytest.raises(BookWritePaused):
        _execute(pg_engine, scenario, _command(scenario))
    assert account_reads == 0


@pytest.mark.parametrize(
    "membership_update",
    [
        "update book_members set status = 'revoked', revoked_at = clock_timestamp()",
        "update book_members set scopes = '[]'::jsonb",
    ],
)
def test_requires_an_active_ledger_writer_membership(
    pg_engine,
    membership_update: str,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(text(membership_update))

    with pytest.raises(JournalWriteForbidden):
        _execute(pg_engine, scenario, _command(scenario))

    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )


def test_authorization_refreshes_a_stale_membership_identity_map(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)

    class PreloadingMembershipUnitOfWork(SqlAlchemyUnitOfWork):
        def __enter__(self):
            entered = super().__enter__()
            cached = self.session.get(
                BookMemberRecord,
                (scenario.book_id, scenario.actor_subject_id),
            )
            assert cached is not None and cached.status == "active"
            with pg_engine.begin() as connection:
                connection.execute(
                    text(
                        "update book_members "
                        "set status = 'revoked', revoked_at = clock_timestamp() "
                        "where book_id = :book_id and user_id = :user_id"
                    ),
                    {
                        "book_id": scenario.book_id,
                        "user_id": scenario.actor_subject_id,
                    },
                )
            assert cached.status == "active"
            return entered

    with pytest.raises(JournalWriteForbidden):
        execute_post_transaction(
            _command(scenario),
            raw_key="stale-membership-cache",
            actor=CommandActor(subject_id=scenario.actor_subject_id),
            uow_factory=lambda: PreloadingMembershipUnitOfWork(factory),
        )

    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )


def test_enforces_expected_transaction_stream_version(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    first = _command(scenario)
    _execute(pg_engine, scenario, first)
    second = replace(first, command_id=uuid4())

    with pytest.raises(StreamVersionConflict) as error_info:
        _execute(pg_engine, scenario, second)

    assert (error_info.value.expected_version, error_info.value.actual_version) == (
        0,
        1,
    )
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1


def test_committed_write_is_immediately_visible_from_an_independent_worker(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _execute(pg_engine, scenario, _command(scenario))

    worker_b = Session(pg_engine)
    try:
        transaction = worker_b.get(
            JournalTransactionRecord,
            (scenario.book_id, scenario.transaction_id),
        )
        balance = worker_b.get(
            AccountBalanceRecord,
            (scenario.book_id, scenario.debit_account_id, "USD"),
        )
        assert transaction is not None and transaction.source_position == 1
        assert balance is not None and balance.balance_units == Decimal(1234)
    finally:
        worker_b.close()
