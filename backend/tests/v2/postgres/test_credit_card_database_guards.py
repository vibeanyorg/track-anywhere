from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.postgres.test_credit_card_transactions import (
    EFFECTIVE_AT,
    _append_bypassed_reversal,
    _bypassed_reversal_pending,
    _charge,
    _execute,
    _refund,
    _seed_card_accounts,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def test_database_rejects_reversal_of_a_credit_card_reversal(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    _execute(pg_engine, scenario, charge)
    first = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=charge.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    execute_reverse_transaction(
        first,
        raw_key=f"reverse:{first.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(pg_engine),
    )
    pending = _bypassed_reversal_pending(
        pg_engine,
        scenario,
        source_transaction_id=first.reversal_transaction_id,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )

    with pytest.raises(
        IntegrityError, match="credit-card reversals cannot be reversed"
    ):
        _append_bypassed_reversal(pg_engine, scenario, pending)


def test_database_rejects_credit_card_reversal_before_its_source(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    _execute(pg_engine, scenario, charge)
    pending = _bypassed_reversal_pending(
        pg_engine,
        scenario,
        source_transaction_id=charge.transaction_id,
        effective_at=EFFECTIVE_AT - timedelta(microseconds=1),
    )

    with pytest.raises(
        IntegrityError, match="credit-card reversal precedes its source"
    ):
        _append_bypassed_reversal(pg_engine, scenario, pending)


def test_database_rejects_charge_reversal_with_an_active_refund(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    refund = _refund(scenario, charge.transaction_id, "25.00", offset=1)
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, refund)
    pending = _bypassed_reversal_pending(
        pg_engine,
        scenario,
        source_transaction_id=charge.transaction_id,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )

    with pytest.raises(IntegrityError, match="credit-card charge has active refunds"):
        _append_bypassed_reversal(pg_engine, scenario, pending)


@pytest.mark.parametrize(
    "corruption",
    [
        pytest.param("missing_posting", id="requires-exactly-two-postings"),
        pytest.param("units_mismatch", id="requires-exact-event-payload"),
    ],
)
def test_database_rejects_credit_card_projection_corruption_even_for_owner(
    migrated_postgres_database,
    corruption: str,
) -> None:
    runtime_engine = create_engine(migrated_postgres_database.runtime_url)
    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        scenario, _ = _seed_card_accounts(runtime_engine)
        charge = _charge(scenario)
        _execute(runtime_engine, scenario, charge)

        with pytest.raises(
            IntegrityError,
            match="credit-card projection must bind its exact typed event",
        ):
            with owner_engine.begin() as connection:
                connection.exec_driver_sql(
                    f'SET ROLE "{migrated_postgres_database.owner_role}"'
                )
                connection.exec_driver_sql(
                    "alter table public.credit_card_transactions disable trigger "
                    "trg_credit_card_transactions_immutable"
                )
                relation = (
                    connection.execute(
                        text(
                            "delete from public.credit_card_transactions "
                            "where book_id=:book_id and transaction_id=:transaction_id "
                            "returning book_id, transaction_id, intent, card_account_id, "
                            "counter_account_id, asset_code, units, "
                            "original_transaction_id, source_event_id, source_position"
                        ),
                        {
                            "book_id": scenario.book_id,
                            "transaction_id": charge.transaction_id,
                        },
                    )
                    .mappings()
                    .one()
                )
                connection.exec_driver_sql(
                    "alter table public.credit_card_transactions enable trigger "
                    "trg_credit_card_transactions_immutable"
                )
                values = dict(relation)
                if corruption == "missing_posting":
                    connection.exec_driver_sql(
                        "alter table public.journal_postings disable trigger "
                        "trg_journal_postings_balanced_commit"
                    )
                    connection.execute(
                        text(
                            "delete from public.journal_postings "
                            "where book_id=:book_id and transaction_id=:transaction_id "
                            "and posting_position=1"
                        ),
                        {
                            "book_id": scenario.book_id,
                            "transaction_id": charge.transaction_id,
                        },
                    )
                    connection.exec_driver_sql(
                        "alter table public.journal_postings enable trigger "
                        "trg_journal_postings_balanced_commit"
                    )
                else:
                    values["units"] = int(values["units"]) + 1

                connection.execute(
                    text(
                        "insert into public.credit_card_transactions ("
                        "book_id, transaction_id, intent, card_account_id, "
                        "counter_account_id, asset_code, units, "
                        "original_transaction_id, source_event_id, source_position"
                        ") values ("
                        ":book_id, :transaction_id, :intent, :card_account_id, "
                        ":counter_account_id, :asset_code, :units, "
                        ":original_transaction_id, :source_event_id, :source_position"
                        ")"
                    ),
                    values,
                )
    finally:
        runtime_engine.dispose()
        owner_engine.dispose()
