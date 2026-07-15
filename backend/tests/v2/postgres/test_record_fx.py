from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.idempotency import (
    CommandActor,
    IdempotencyValidationError,
)
from track_anywhere.application.journal.record_fx import (
    RecordFxCommand,
    execute_record_fx,
)
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.journal import InvalidFxTransaction
from track_anywhere.domain.money import MoneyError
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.repositories.catalogs import CatalogRepository
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 14, 12, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FxScenario:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    source_account_id: UUID
    source_trading_account_id: UUID
    target_trading_account_id: UUID
    target_account_id: UUID
    actor_subject_id: str = "human:fx-test"

    @classmethod
    def create(cls) -> FxScenario:
        return cls(
            book_id=uuid4(),
            command_id=uuid4(),
            transaction_id=uuid4(),
            source_account_id=uuid4(),
            source_trading_account_id=uuid4(),
            target_trading_account_id=uuid4(),
            target_account_id=uuid4(),
        )


def _seed_fx_scenario(
    pg_engine,
    scenario: FxScenario,
    *,
    source_trading_role: str | None = "fx_trading",
    target_account_type: str = "asset",
    target_account_subtype: str | None = None,
) -> None:
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values
                    ('CNY', 'fiat', 2, 2, 2, 'Chinese Yuan', 'active'),
                    ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
                on conflict (asset_code) do nothing
                """
            )
        )
        connection.execute(
            text(
                """
                insert into books (book_id, current_name, base_asset_code, write_state)
                values (:book_id, 'FX Book', 'CNY', 'active')
                """
            ),
            {"book_id": scenario.book_id},
        )
        connection.execute(
            text(
                """
                insert into book_event_heads (book_id, last_position, last_hash)
                values (:book_id, 0, :zero_hash)
                """
            ),
            {"book_id": scenario.book_id, "zero_hash": bytes(32)},
        )
        accounts = (
            (scenario.source_account_id, "CNY", "asset", None, None, "CNY Bank"),
            (
                scenario.source_trading_account_id,
                "CNY",
                "asset",
                None,
                source_trading_role,
                "CNY FX Trading",
            ),
            (
                scenario.target_trading_account_id,
                "USD",
                "asset",
                None,
                "fx_trading",
                "USD FX Trading",
            ),
            (
                scenario.target_account_id,
                "USD",
                target_account_type,
                target_account_subtype,
                None,
                "USD Wallet",
            ),
        )
        for (
            account_id,
            asset_code,
            account_type,
            account_subtype,
            system_role,
            name,
        ) in accounts:
            connection.execute(
                text(
                    """
                    insert into accounts (
                        book_id, account_id, asset_code, account_type,
                        account_subtype, system_role, current_name, status
                    ) values (
                        :book_id, :account_id, :asset_code, :account_type,
                        :account_subtype, :system_role, :name, 'active'
                    )
                    """
                ),
                {
                    "book_id": scenario.book_id,
                    "account_id": account_id,
                    "asset_code": asset_code,
                    "account_type": account_type,
                    "account_subtype": account_subtype,
                    "system_role": system_role,
                    "name": name,
                },
            )
        connection.execute(
            text(
                """
                insert into users (user_id, subject_type, current_display_name, status)
                values (:user_id, 'human', 'FX Test', 'active')
                """
            ),
            {"user_id": scenario.actor_subject_id},
        )
        connection.execute(
            text(
                """
                insert into book_members (book_id, user_id, role, status, scopes)
                values (:book_id, :user_id, 'owner', 'active', '["ledger:write"]')
                """
            ),
            {"book_id": scenario.book_id, "user_id": scenario.actor_subject_id},
        )


def _command(scenario: FxScenario) -> RecordFxCommand:
    return RecordFxCommand(
        book_id=scenario.book_id,
        command_id=scenario.command_id,
        transaction_id=scenario.transaction_id,
        expected_stream_version=0,
        source_account_id=scenario.source_account_id,
        source_trading_account_id=scenario.source_trading_account_id,
        source_asset_code="CNY",
        source_amount="700.00",
        target_trading_account_id=scenario.target_trading_account_id,
        target_account_id=scenario.target_account_id,
        target_asset_code="USD",
        target_amount="100.00",
        effective_at=EFFECTIVE_AT,
    )


def _execute(pg_engine, scenario: FxScenario, command: RecordFxCommand):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    return execute_record_fx(
        command,
        raw_key=f"fx:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
    )


def test_records_one_four_leg_fx_event_with_exact_units_and_display_rate(
    pg_engine,
) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(pg_engine, scenario)

    outcome = _execute(pg_engine, scenario, _command(scenario))

    assert outcome.replayed is False
    assert outcome.result.status_code == 201
    assert outcome.result.body == {
        "transaction_id": str(scenario.transaction_id),
        "as_of_book_position": 1,
    }
    with Session(pg_engine) as session:
        event = session.scalar(select(LedgerEventRecord))
        assert event is not None
        assert (event.event_type, event.event_schema_version) == (
            "JournalTransactionPosted",
            1,
        )
        assert event.payload["kind"] == "fx"
        assert [
            (
                posting["account_id"],
                posting["asset_code"],
                posting["side"],
                posting["units"],
            )
            for posting in event.payload["postings"]
        ] == [
            (str(scenario.target_account_id), "USD", "debit", "10000"),
            (str(scenario.target_trading_account_id), "USD", "credit", "10000"),
            (str(scenario.source_trading_account_id), "CNY", "debit", "70000"),
            (str(scenario.source_account_id), "CNY", "credit", "70000"),
        ]

        transaction = session.get(
            JournalTransactionRecord,
            (scenario.book_id, scenario.transaction_id),
        )
        assert transaction is not None and transaction.transaction_kind == "fx"
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord).order_by(
                    JournalPostingRecord.posting_position
                )
            )
        )
        assert tuple(int(posting.units) for posting in postings) == (
            10_000,
            10_000,
            70_000,
            70_000,
        )
        balances = {
            balance.account_id: int(balance.balance_units)
            for balance in session.scalars(select(AccountBalanceRecord))
        }
        assert balances == {
            scenario.target_account_id: 10_000,
            scenario.target_trading_account_id: -10_000,
            scenario.source_trading_account_id: 70_000,
            scenario.source_account_id: -70_000,
        }

        source_units = Decimal(event.payload["postings"][2]["units"])
        target_units = Decimal(event.payload["postings"][0]["units"])
        display_rate = source_units.scaleb(-2) / target_units.scaleb(-2)
        assert display_rate == Decimal("7")


def test_replay_returns_the_stored_fx_result_without_appending_again(pg_engine) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(pg_engine, scenario)
    command = _command(scenario)

    first = _execute(pg_engine, scenario, command)
    second = _execute(pg_engine, scenario, command)

    assert second.replayed is True
    assert second.result == first.result
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("source_amount", "7e2"),
        ("source_amount", "700.001"),
        ("source_amount", 700.0),
        ("target_amount", "0"),
        ("target_amount", 100.0),
    ],
)
def test_rejects_non_exact_fx_amounts_without_partial_writes(
    pg_engine,
    field: str,
    raw: object,
) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(pg_engine, scenario)

    with pytest.raises((IdempotencyValidationError, MoneyError)):
        command = replace(_command(scenario), **{field: raw})
        _execute(pg_engine, scenario, command)

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 0


def test_fx_rejects_credit_card_accounts_without_partial_writes(pg_engine) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(
        pg_engine,
        scenario,
        target_account_type="liability",
        target_account_subtype="credit_card",
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


def test_requires_book_owned_fx_trading_accounts(pg_engine) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(pg_engine, scenario, source_trading_role=None)

    with pytest.raises(InvalidFxTransaction, match="trading-account"):
        _execute(pg_engine, scenario, _command(scenario))

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0


def test_locks_the_book_before_reading_fx_catalogs(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = FxScenario.create()
    _seed_fx_scenario(pg_engine, scenario)
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

    _execute(pg_engine, scenario, _command(scenario))

    assert order[0] == "book_lock"
    assert order[1:] == ["account_read"] * 4
