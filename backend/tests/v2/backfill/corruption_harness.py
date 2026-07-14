from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Iterator
from uuid import UUID, uuid4

from sqlalchemy import Connection, create_engine, text

from backend.tests.v2.postgres_factory import ProvisionedDatabase


_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_ALLOWED_TRIGGERS = {
    ("journal_postings", "trg_journal_postings_balanced_commit"),
    ("ledger_events", "trg_ledger_events_immutable"),
    ("ledger_events", "trg_ledger_events_sync_projection_commit"),
}


class CorruptionHarness:
    """Owner-only mutation surface for disposable factory databases.

    This module lives under tests deliberately. Production and backfill code must
    never import it.
    """

    def __init__(
        self,
        factory: object,
        database: ProvisionedDatabase,
        *,
        connection_url: str | None = None,
    ) -> None:
        created = getattr(factory, "_created", None)
        test_uuid = getattr(factory, "test_uuid", None)
        if (
            type(created) is not dict
            or created.get(database.database_name) is not database
            or type(test_uuid) is not str
            or f"_{test_uuid}_" not in database.database_name
        ):
            raise ValueError(
                "corruption database was not created by the current test factory"
            )
        selected_url = (
            database.migrator_url if connection_url is None else connection_url
        )
        if selected_url != database.migrator_url:
            raise ValueError("corruption harness accepts only the factory migrator URL")
        if not _IDENTIFIER.fullmatch(database.owner_role):
            raise ValueError("corruption harness owner role is unsafe")
        self._database = database
        self._url = selected_url

    @contextmanager
    def _owner_connection(self) -> Iterator[Connection]:
        engine = create_engine(self._url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                session_user = str(
                    connection.exec_driver_sql("select session_user").scalar_one()
                )
                if session_user != self._database.migrator_role:
                    raise RuntimeError(
                        "corruption harness did not use the migrator login"
                    )
                connection.exec_driver_sql(f'SET ROLE "{self._database.owner_role}"')
                current_user = str(
                    connection.exec_driver_sql("select current_user").scalar_one()
                )
                if current_user != self._database.owner_role:
                    raise RuntimeError(
                        "corruption harness did not assume the table owner"
                    )
                try:
                    yield connection
                finally:
                    connection.exec_driver_sql("RESET ROLE")
        finally:
            engine.dispose()

    @staticmethod
    def _assert_trigger(
        connection: Connection, table_name: str, trigger_name: str
    ) -> None:
        if (table_name, trigger_name) not in _ALLOWED_TRIGGERS:
            raise ValueError("corruption harness trigger is not allowlisted")
        exists = connection.execute(
            text(
                "select exists (select 1 from pg_catalog.pg_trigger trigger "
                "join pg_catalog.pg_class relation on relation.oid=trigger.tgrelid "
                "join pg_catalog.pg_namespace namespace on namespace.oid=relation.relnamespace "
                "where namespace.nspname='public' and relation.relname=:table_name "
                "and trigger.tgname=:trigger_name and not trigger.tgisinternal)"
            ),
            {"table_name": table_name, "trigger_name": trigger_name},
        ).scalar_one()
        if not exists:
            raise RuntimeError("named integrity trigger does not exist")

    @contextmanager
    def _disabled_trigger(
        self, connection: Connection, table_name: str, trigger_name: str
    ) -> Iterator[None]:
        self._assert_trigger(connection, table_name, trigger_name)
        connection.exec_driver_sql(
            f"ALTER TABLE public.{table_name} DISABLE TRIGGER {trigger_name}"
        )
        try:
            yield
        finally:
            connection.exec_driver_sql(
                f"ALTER TABLE public.{table_name} ENABLE TRIGGER {trigger_name}"
            )
        enabled = connection.execute(
            text(
                "select trigger.tgenabled from pg_catalog.pg_trigger trigger "
                "join pg_catalog.pg_class relation on relation.oid=trigger.tgrelid "
                "where relation.relname=:table_name and trigger.tgname=:trigger_name"
            ),
            {"table_name": table_name, "trigger_name": trigger_name},
        ).scalar_one()
        if enabled != "O":
            raise RuntimeError("corruption harness failed to restore integrity trigger")

    def delete_posting(self, *, book_id: UUID, transaction_id: UUID) -> None:
        with self._owner_connection() as connection:
            with self._disabled_trigger(
                connection,
                "journal_postings",
                "trg_journal_postings_balanced_commit",
            ):
                result = connection.execute(
                    text(
                        "delete from public.journal_postings where posting_id=("
                        "select posting_id from public.journal_postings "
                        "where book_id=:book_id and transaction_id=:transaction_id "
                        "order by posting_position limit 1)"
                    ),
                    {"book_id": book_id, "transaction_id": transaction_id},
                )
                if result.rowcount != 1:
                    raise RuntimeError("lost-posting mutation did not affect one row")

    def swap_posting_side(self, *, book_id: UUID, transaction_id: UUID) -> None:
        with self._owner_connection() as connection:
            with self._disabled_trigger(
                connection,
                "journal_postings",
                "trg_journal_postings_balanced_commit",
            ):
                result = connection.execute(
                    text(
                        "update public.journal_postings set side=(case side::text "
                        "when 'debit' then 'credit'::posting_side "
                        "else 'debit'::posting_side end) where posting_id=("
                        "select posting_id from public.journal_postings "
                        "where book_id=:book_id and transaction_id=:transaction_id "
                        "order by posting_position limit 1)"
                    ),
                    {"book_id": book_id, "transaction_id": transaction_id},
                )
                if result.rowcount != 1:
                    raise RuntimeError("side mutation did not affect one row")

    def shift_transaction_effective_time(
        self, *, book_id: UUID, transaction_id: UUID
    ) -> None:
        with self._owner_connection() as connection:
            result = connection.execute(
                text(
                    "update public.journal_transactions "
                    "set effective_at=effective_at+:delta "
                    "where book_id=:book_id and transaction_id=:transaction_id"
                ),
                {
                    "book_id": book_id,
                    "transaction_id": transaction_id,
                    "delta": timedelta(days=1),
                },
            )
            if result.rowcount != 1:
                raise RuntimeError("effective-time mutation did not affect one row")

    def replace_reporting_dimension(
        self, *, book_id: UUID, transaction_id: UUID, dimension_id: UUID
    ) -> None:
        with self._owner_connection() as connection:
            result = connection.execute(
                text(
                    "update public.reporting_lines set dimension_id=:dimension_id "
                    "where book_id=:book_id and transaction_id=:transaction_id"
                ),
                {
                    "book_id": book_id,
                    "transaction_id": transaction_id,
                    "dimension_id": dimension_id,
                },
            )
            if result.rowcount < 1:
                raise RuntimeError("classification mutation did not affect any row")

    def duplicate_reversal_event(
        self, *, book_id: UUID, original_transaction_id: UUID
    ) -> None:
        with self._owner_connection() as connection:
            with self._disabled_trigger(
                connection, "ledger_events", "trg_ledger_events_immutable"
            ):
                result = connection.execute(
                    text(
                        "update public.ledger_events target set "
                        "event_type='JournalTransactionReversed', payload=(select payload "
                        "from public.ledger_events source where source.book_id=:book_id "
                        "and source.event_type='JournalTransactionReversed' and "
                        "source.payload->>'reverses_transaction_id'=:original_id limit 1) "
                        "where event_id=(select event_id from public.ledger_events "
                        "where book_id=:book_id and event_type='ReportingLinesAssigned' "
                        "order by book_position limit 1)"
                    ),
                    {"book_id": book_id, "original_id": str(original_transaction_id)},
                )
                if result.rowcount != 1:
                    raise RuntimeError(
                        "duplicate-reversal mutation did not affect one row"
                    )

    def break_previous_hash(self, *, book_id: UUID) -> None:
        with self._owner_connection() as connection:
            with self._disabled_trigger(
                connection, "ledger_events", "trg_ledger_events_immutable"
            ):
                result = connection.execute(
                    text(
                        "update public.ledger_events set previous_hash=:broken "
                        "where event_id=(select event_id from public.ledger_events "
                        "where book_id=:book_id order by book_position limit 1)"
                    ),
                    {"book_id": book_id, "broken": b"x" * 32},
                )
                if result.rowcount != 1:
                    raise RuntimeError("hash mutation did not affect one row")

    def append_noncontiguous_event(self, *, book_id: UUID) -> None:
        with self._owner_connection() as connection:
            head = (
                connection.execute(
                    text(
                        "select last_position, last_hash from public.book_event_heads "
                        "where book_id=:book_id for update"
                    ),
                    {"book_id": book_id},
                )
                .mappings()
                .one()
            )
            event_id = uuid4()
            command_id = uuid4()
            event_hash = sha256(event_id.bytes + bytes(head["last_hash"])).digest()
            position = int(head["last_position"]) + 2
            with self._disabled_trigger(
                connection,
                "ledger_events",
                "trg_ledger_events_sync_projection_commit",
            ):
                connection.execute(
                    text(
                        "insert into public.ledger_events (event_id, book_id, "
                        "book_position, stream_type, stream_id, stream_version, "
                        "event_type, event_schema_version, command_id, actor_subject_id, "
                        "correlation_id, causation_event_id, effective_at, payload, "
                        "previous_hash, event_hash) values (:event_id, :book_id, "
                        ":position, 'harness_gap', :stream_id, 2, 'HarnessGap', 1, "
                        ":command_id, 'test:corruption-harness', :correlation_id, null, "
                        ":effective_at, '{}'::jsonb, :previous_hash, :event_hash)"
                    ),
                    {
                        "event_id": event_id,
                        "book_id": book_id,
                        "position": position,
                        "stream_id": uuid4(),
                        "command_id": command_id,
                        "correlation_id": command_id,
                        "effective_at": datetime(2026, 7, 14, tzinfo=UTC),
                        "previous_hash": head["last_hash"],
                        "event_hash": event_hash,
                    },
                )
            connection.execute(
                text(
                    "update public.book_event_heads set last_position=:position, "
                    "last_hash=:event_hash where book_id=:book_id"
                ),
                {"position": position, "event_hash": event_hash, "book_id": book_id},
            )

    def increment_posting_units(self, *, book_id: UUID, posting_id: UUID) -> None:
        with self._owner_connection() as connection:
            with self._disabled_trigger(
                connection,
                "journal_postings",
                "trg_journal_postings_balanced_commit",
            ):
                result = connection.execute(
                    text(
                        "update public.journal_postings set units=units+1 "
                        "where book_id=:book_id and posting_id=:posting_id"
                    ),
                    {"book_id": book_id, "posting_id": posting_id},
                )
                if result.rowcount != 1:
                    raise RuntimeError("unit mutation did not affect one row")


__all__ = ["CorruptionHarness"]
