from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError


def _insert_catalog(engine: Engine) -> tuple[UUID, UUID, UUID]:
    book_id = uuid4()
    debit_account_id = uuid4()
    credit_account_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
                on conflict (asset_code) do nothing
                """
            )
        )
        connection.execute(
            text(
                """
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, 'Projection book', 'USD', 'active')
                """
            ),
            {"book_id": book_id},
        )
        for account_id, name in (
            (debit_account_id, "Debit account"),
            (credit_account_id, "Credit account"),
        ):
            connection.execute(
                text(
                    """
                    insert into accounts (
                        book_id, account_id, asset_code, account_type,
                        system_role, current_name, status
                    ) values (
                        :book_id, :account_id, 'USD', 'asset', null,
                        :name, 'active'
                    )
                    """
                ),
                {"book_id": book_id, "account_id": account_id, "name": name},
            )
    return book_id, debit_account_id, credit_account_id


def _insert_asset(engine: Engine, asset_code: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values (
                    :asset_code, 'fiat', 2, 2, 2, :current_name, 'active'
                ) on conflict (asset_code) do nothing
                """
            ),
            {"asset_code": asset_code, "current_name": asset_code},
        )


def _insert_account(
    engine: Engine, *, book_id: UUID, account_id: UUID, asset_code: str
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into accounts (
                    book_id, account_id, asset_code, account_type,
                    system_role, current_name, status
                ) values (
                    :book_id, :account_id, :asset_code, 'asset', null,
                    :current_name, 'active'
                )
                """
            ),
            {
                "book_id": book_id,
                "account_id": account_id,
                "asset_code": asset_code,
                "current_name": f"{asset_code} account",
            },
        )


def _insert_event_and_transaction(
    connection: Connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    event_id: UUID,
    book_position: int,
) -> None:
    connection.execute(
        text(
            """
            insert into ledger_events (
                event_id, book_id, book_position, stream_type, stream_id,
                stream_version, event_type, event_schema_version, command_id,
                actor_subject_id, correlation_id, causation_event_id,
                effective_at, payload, previous_hash, event_hash
            ) values (
                :event_id, :book_id, :book_position, 'journal_transaction',
                :transaction_id, 1, 'JournalTransactionPosted', 1,
                :command_id, 'human:test-user', :correlation_id, null,
                :effective_at, '{}'::jsonb, :previous_hash, :event_hash
            )
            """
        ),
        {
            "event_id": event_id,
            "book_id": book_id,
            "book_position": book_position,
            "transaction_id": transaction_id,
            "command_id": uuid4(),
            "correlation_id": uuid4(),
            "effective_at": datetime.now(UTC),
            "previous_hash": bytes(32),
            "event_hash": event_id.bytes + event_id.bytes,
        },
    )
    connection.execute(
        text(
            """
            insert into journal_transactions (
                book_id, transaction_id, source_event_id, source_position,
                effective_at, transaction_kind, description_ref
            ) values (
                :book_id, :transaction_id, :event_id, :book_position,
                :effective_at, 'standard', null
            )
            """
        ),
        {
            "book_id": book_id,
            "transaction_id": transaction_id,
            "event_id": event_id,
            "book_position": book_position,
            "effective_at": datetime.now(UTC),
        },
    )
    connection.execute(
        text(
            """
            insert into synchronous_projection_applied_events (
                book_id, event_id, projection_version
            ) values (:book_id, :event_id, 1)
            """
        ),
        {"book_id": book_id, "event_id": event_id},
    )


def _insert_posting(
    connection: Connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    account_id: UUID,
    posting_position: int,
    side: str,
    units: str = "100",
    asset_code: str = "USD",
    posting_id: UUID | None = None,
) -> None:
    connection.execute(
        text(
            """
            insert into journal_postings (
                book_id, transaction_id, posting_id, posting_position,
                account_id, asset_code, side, units
            ) values (
                :book_id, :transaction_id, :posting_id, :posting_position,
                :account_id, :asset_code, cast(:side as posting_side), cast(:units as numeric)
            )
            """
        ),
        {
            "book_id": book_id,
            "transaction_id": transaction_id,
            "posting_id": posting_id or uuid4(),
            "posting_position": posting_position,
            "account_id": account_id,
            "side": side,
            "units": units,
            "asset_code": asset_code,
        },
    )


def _insert_balanced_pair(
    connection: Connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    debit_account_id: UUID,
    credit_account_id: UUID,
    asset_code: str = "USD",
    position_offset: int = 0,
    units: str = "100",
) -> None:
    _insert_posting(
        connection,
        book_id=book_id,
        transaction_id=transaction_id,
        account_id=debit_account_id,
        asset_code=asset_code,
        posting_position=position_offset,
        side="debit",
        units=units,
    )
    _insert_posting(
        connection,
        book_id=book_id,
        transaction_id=transaction_id,
        account_id=credit_account_id,
        asset_code=asset_code,
        posting_position=position_offset + 1,
        side="credit",
        units=units,
    )


@pytest.mark.parametrize("posting_count", [0, 1])
def test_incomplete_transaction_is_rejected_only_when_the_transaction_commits(
    pg_engine,
    posting_count: int,
) -> None:
    book_id, debit_account_id, _ = _insert_catalog(pg_engine)
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        transaction_id = uuid4()
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        if posting_count:
            _insert_posting(
                connection,
                book_id=book_id,
                transaction_id=transaction_id,
                account_id=debit_account_id,
                posting_position=0,
                side="debit",
            )

        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_one_posting_can_be_completed_to_two_balanced_postings_before_commit(
    pg_engine,
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    event_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=event_id,
            book_position=1,
        )
        _insert_posting(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            account_id=debit_account_id,
            posting_position=0,
            side="debit",
        )
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                  from journal_postings
                 where book_id = :book_id and transaction_id = :transaction_id
                """
                ),
                {"book_id": book_id, "transaction_id": transaction_id},
            ).scalar_one()
            == 1
        )
        _insert_posting(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            account_id=credit_account_id,
            posting_position=1,
            side="credit",
        )

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                  from journal_postings
                 where book_id = :book_id and transaction_id = :transaction_id
                """
                ),
                {"book_id": book_id, "transaction_id": transaction_id},
            ).scalar_one()
            == 2
        )


def test_a_cross_asset_zero_sum_is_rejected_per_asset_at_commit(pg_engine) -> None:
    book_id, usd_account_id, _ = _insert_catalog(pg_engine)
    _insert_asset(pg_engine, "EUR")
    eur_account_id = uuid4()
    _insert_account(
        pg_engine,
        book_id=book_id,
        account_id=eur_account_id,
        asset_code="EUR",
    )

    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        transaction_id = uuid4()
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        _insert_posting(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            account_id=usd_account_id,
            posting_position=0,
            side="debit",
            asset_code="USD",
        )
        _insert_posting(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            account_id=eur_account_id,
            posting_position=1,
            side="credit",
            asset_code="EUR",
        )

        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_each_asset_can_balance_independently_in_one_transaction(pg_engine) -> None:
    book_id, usd_debit_id, usd_credit_id = _insert_catalog(pg_engine)
    _insert_asset(pg_engine, "EUR")
    eur_debit_id = uuid4()
    eur_credit_id = uuid4()
    for account_id in (eur_debit_id, eur_credit_id):
        _insert_account(
            pg_engine,
            book_id=book_id,
            account_id=account_id,
            asset_code="EUR",
        )

    transaction_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=usd_debit_id,
            credit_account_id=usd_credit_id,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=eur_debit_id,
            credit_account_id=eur_credit_id,
            asset_code="EUR",
            position_offset=2,
        )

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                  from journal_postings
                 where book_id = :book_id and transaction_id = :transaction_id
                """
                ),
                {"book_id": book_id, "transaction_id": transaction_id},
            ).scalar_one()
            == 4
        )


def test_posting_update_checks_the_old_transaction_final_state(
    pg_engine, migrated_postgres_database
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    first_transaction_id = uuid4()
    second_transaction_id = uuid4()
    with pg_engine.begin() as connection:
        for book_position, transaction_id in enumerate(
            (first_transaction_id, second_transaction_id), start=1
        ):
            _insert_event_and_transaction(
                connection,
                book_id=book_id,
                transaction_id=transaction_id,
                event_id=uuid4(),
                book_position=book_position,
            )
            _insert_balanced_pair(
                connection,
                book_id=book_id,
                transaction_id=transaction_id,
                debit_account_id=debit_account_id,
                credit_account_id=credit_account_id,
            )

    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    connection = owner_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(f'SET ROLE "{migrated_postgres_database.owner_role}"'))
        connection.execute(
            text(
                """
                update journal_postings
                   set transaction_id = :second_transaction_id,
                       posting_position = posting_position + 2
                 where book_id = :book_id
                   and transaction_id = :first_transaction_id
                """
            ),
            {
                "book_id": book_id,
                "first_transaction_id": first_transaction_id,
                "second_transaction_id": second_transaction_id,
            },
        )
        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        owner_engine.dispose()


def test_posting_delete_cannot_leave_a_zero_posting_transaction(
    pg_engine, migrated_postgres_database
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
        )

    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    connection = owner_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(f'SET ROLE "{migrated_postgres_database.owner_role}"'))
        connection.execute(
            text(
                """
                delete from journal_postings
                 where book_id = :book_id and transaction_id = :transaction_id
                """
            ),
            {"book_id": book_id, "transaction_id": transaction_id},
        )
        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        owner_engine.dispose()
