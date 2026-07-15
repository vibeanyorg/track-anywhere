"""Add typed credit-card transactions and their atomic projection."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0010_credit_card_transactions"
down_revision = "v2_0009_account_semantics"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise RuntimeError(
            "TRACK_ANYWHERE_DB_RUNTIME_ROLE is required and must be safe"
        )
    return f'"{value}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.drop_constraint(
        op.f("ck_journal_transactions_kind_valid"),
        "journal_transactions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_journal_transactions_kind_valid"),
        "journal_transactions",
        "transaction_kind in ('standard', 'opening', 'adjustment', "
        "'transfer', 'fx', 'investment_cash', 'credit_card_charge', "
        "'credit_card_payment', 'credit_card_refund', 'credit_card_fee')",
    )

    op.create_table(
        "credit_card_transactions",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("intent", sa.String(length=16), nullable=False),
        sa.Column("card_account_id", sa.Uuid(), nullable=False),
        sa.Column("counter_account_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("original_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "intent in ('charge', 'payment', 'refund', 'fee')",
            name=op.f("ck_credit_card_transactions_intent_valid"),
        ),
        sa.CheckConstraint(
            "(intent = 'refund' and original_transaction_id is not null) or "
            "(intent <> 'refund' and original_transaction_id is null)",
            name=op.f("ck_credit_card_transactions_original_shape_valid"),
        ),
        sa.CheckConstraint(
            "card_account_id <> counter_account_id",
            name=op.f("ck_credit_card_transactions_accounts_distinct"),
        ),
        sa.CheckConstraint(
            "units > 0",
            name=op.f("ck_credit_card_transactions_units_positive"),
        ),
        sa.CheckConstraint(
            "source_position > 0",
            name=op.f("ck_credit_card_transactions_source_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_credit_card_transactions_journal_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_credit_card_transactions_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_credit_card_transactions_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "card_account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_credit_card_transactions_card_account",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "counter_account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_credit_card_transactions_counter_account",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "original_transaction_id"],
            [
                "credit_card_transactions.book_id",
                "credit_card_transactions.transaction_id",
            ],
            name="fk_credit_card_transactions_original",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "transaction_id",
            name=op.f("pk_credit_card_transactions"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "source_event_id",
            name="uq_credit_card_transactions_book_source_event",
        ),
    )
    op.create_index(
        "ix_credit_card_transactions_active_refunds",
        "credit_card_transactions",
        ("book_id", "original_transaction_id", "source_position"),
        unique=False,
        postgresql_where=sa.text("original_transaction_id is not null"),
    )

    op.bulk_insert(
        sa.table(
            "synchronous_projection_event_types",
            sa.column("event_type", sa.String()),
            sa.column("event_schema_version", sa.SmallInteger()),
            sa.column("projection_version", sa.Integer()),
        ),
        [
            {
                "event_type": "CreditCardTransactionRecorded",
                "event_schema_version": 1,
                "projection_version": 1,
            }
        ],
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        f"revoke all privileges on table public.credit_card_transactions "
        f"from public, {runtime}"
    )
    connection.exec_driver_sql(
        f"grant select on table public.credit_card_transactions to {runtime}"
    )
    connection.exec_driver_sql(
        "grant insert (book_id, transaction_id, intent, card_account_id, "
        "counter_account_id, asset_code, units, original_transaction_id, "
        "source_event_id, source_position) on table "
        f"public.credit_card_transactions to {runtime}"
    )

    _replace_journal_source_function(runtime)
    _create_credit_card_projection_guards(runtime)


def _replace_journal_source_function(runtime: str) -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        create or replace function public.v2_validate_journal_source_projection()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            perform 1
              from public.ledger_events event_record
              join public.synchronous_projection_event_types required
                on required.event_type = event_record.event_type
               and required.event_schema_version = event_record.event_schema_version
             where event_record.book_id = new.book_id
               and event_record.event_id = new.source_event_id
               and event_record.book_position = new.source_position
               and event_record.stream_id = new.transaction_id
               and event_record.event_type in (
                   'JournalTransactionPosted',
                   'JournalTransactionReversed',
                   'CreditCardTransactionRecorded'
               );
            if not found then
                raise exception using errcode = '23514',
                    message = 'journal transaction must bind its exact source event';
            end if;
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        "revoke all privileges on function "
        f"public.v2_validate_journal_source_projection() from public, {runtime}"
    )


def _create_credit_card_projection_guards(runtime: str) -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        create function public.v2_validate_credit_card_projection()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        declare
            original_record public.credit_card_transactions%%rowtype;
            active_refund_units numeric(38, 0);
        begin
            perform 1
              from public.ledger_events event_record
              join public.journal_transactions journal_record
                on journal_record.book_id = event_record.book_id
               and journal_record.transaction_id = new.transaction_id
               and journal_record.source_event_id = event_record.event_id
               and journal_record.source_position = event_record.book_position
              join public.accounts card_account
                on card_account.book_id = new.book_id
               and card_account.account_id = new.card_account_id
               and card_account.asset_code = new.asset_code
              join public.accounts counter_account
                on counter_account.book_id = new.book_id
               and counter_account.account_id = new.counter_account_id
               and counter_account.asset_code = new.asset_code
             where event_record.book_id = new.book_id
               and event_record.event_id = new.source_event_id
               and event_record.book_position = new.source_position
               and event_record.event_type = 'CreditCardTransactionRecorded'
               and event_record.event_schema_version = 1
               and event_record.stream_type = 'journal_transaction'
               and event_record.stream_id = new.transaction_id
               and event_record.payload ->> 'intent' = new.intent
               and (event_record.payload ->> 'transaction_id')::uuid =
                   new.transaction_id
               and (event_record.payload ->> 'card_account_id')::uuid =
                   new.card_account_id
               and (event_record.payload ->> 'counter_account_id')::uuid =
                   new.counter_account_id
               and case
                       when event_record.payload ->> 'original_transaction_id' is null
                           then new.original_transaction_id is null
                       else (event_record.payload ->> 'original_transaction_id')::uuid =
                           new.original_transaction_id
                   end
               and journal_record.transaction_kind =
                   'credit_card_' || new.intent
               and card_account.account_type = 'liability'
               and card_account.account_subtype = 'credit_card'
               and counter_account.account_type = case
                       when new.intent = 'payment' then 'asset'
                       else 'expense'
                   end
               and jsonb_array_length(event_record.payload -> 'postings') = 2
               and (
                   select count(*)
                     from public.journal_postings posting
                    where posting.book_id = new.book_id
                      and posting.transaction_id = new.transaction_id
               ) = 2
               and exists (
                   select 1
                     from public.journal_postings posting
                    where posting.book_id = new.book_id
                      and posting.transaction_id = new.transaction_id
                      and posting.posting_position = 0
                      and posting.posting_id =
                          (event_record.payload -> 'postings' -> 0 ->>
                              'posting_id')::uuid
                      and posting.posting_position =
                          (event_record.payload -> 'postings' -> 0 ->>
                              'position')::smallint
                      and posting.account_id =
                          (event_record.payload -> 'postings' -> 0 ->>
                              'account_id')::uuid
                      and posting.account_id = case
                              when new.intent in ('charge', 'fee')
                                  then new.counter_account_id
                              else new.card_account_id
                          end
                      and posting.side = 'debit'::public.posting_side
                      and posting.side =
                          (event_record.payload -> 'postings' -> 0 ->>
                              'side')::public.posting_side
                      and posting.asset_code = new.asset_code
                      and posting.asset_code =
                          event_record.payload -> 'postings' -> 0 ->> 'asset_code'
                      and posting.units = new.units
                      and posting.units =
                          (event_record.payload -> 'postings' -> 0 ->>
                              'units')::numeric
               )
               and exists (
                   select 1
                     from public.journal_postings posting
                    where posting.book_id = new.book_id
                      and posting.transaction_id = new.transaction_id
                      and posting.posting_position = 1
                      and posting.posting_id =
                          (event_record.payload -> 'postings' -> 1 ->>
                              'posting_id')::uuid
                      and posting.posting_position =
                          (event_record.payload -> 'postings' -> 1 ->>
                              'position')::smallint
                      and posting.account_id =
                          (event_record.payload -> 'postings' -> 1 ->>
                              'account_id')::uuid
                      and posting.account_id = case
                              when new.intent in ('charge', 'fee')
                                  then new.card_account_id
                              else new.counter_account_id
                          end
                      and posting.side = 'credit'::public.posting_side
                      and posting.side =
                          (event_record.payload -> 'postings' -> 1 ->>
                              'side')::public.posting_side
                      and posting.asset_code = new.asset_code
                      and posting.asset_code =
                          event_record.payload -> 'postings' -> 1 ->> 'asset_code'
                      and posting.units = new.units
                      and posting.units =
                          (event_record.payload -> 'postings' -> 1 ->>
                              'units')::numeric
               );
            if not found then
                raise exception using errcode = '23514',
                    message = 'credit-card projection must bind its exact typed event';
            end if;

            if new.intent = 'refund' then
                select * into original_record
                  from public.credit_card_transactions original
                 where original.book_id = new.book_id
                   and original.transaction_id = new.original_transaction_id
                   and original.intent = 'charge';
                if not found
                   or original_record.card_account_id <> new.card_account_id
                   or original_record.counter_account_id <> new.counter_account_id
                   or original_record.asset_code <> new.asset_code
                   or exists (
                       select 1
                         from public.transaction_reversals reversal
                        where reversal.book_id = new.book_id
                          and reversal.original_transaction_id =
                              new.original_transaction_id
                   )
                   or exists (
                       select 1
                         from public.journal_transactions original_journal
                         join public.journal_transactions refund_journal
                           on refund_journal.book_id = original_journal.book_id
                        where original_journal.book_id = new.book_id
                          and original_journal.transaction_id =
                              new.original_transaction_id
                          and refund_journal.transaction_id = new.transaction_id
                          and refund_journal.effective_at <
                              original_journal.effective_at
                   ) then
                    raise exception using errcode = '23514',
                        message = 'credit-card refund source is invalid';
                end if;

                select coalesce(sum(refund.units), 0)
                  into active_refund_units
                  from public.credit_card_transactions refund
                  left join public.transaction_reversals reversal
                    on reversal.book_id = refund.book_id
                   and reversal.original_transaction_id = refund.transaction_id
                 where refund.book_id = new.book_id
                   and refund.intent = 'refund'
                   and refund.original_transaction_id = new.original_transaction_id
                   and reversal.reversal_transaction_id is null;
                if active_refund_units + new.units > original_record.units then
                    raise exception using errcode = '23514',
                        message = 'credit-card refunds exceed the original charge';
                end if;
            end if;
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        """
        create function public.v2_validate_credit_card_reversal()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        declare
            original_kind text;
            original_event_type text;
            original_intent text;
            original_effective_at timestamptz;
            reversal_effective_at timestamptz;
            original_touches_credit_card boolean;
        begin
            select original_transaction.transaction_kind,
                   original_event.event_type,
                   original_event.payload ->> 'intent',
                   original_transaction.effective_at,
                   reversal_transaction.effective_at
              into original_kind,
                   original_event_type,
                   original_intent,
                   original_effective_at,
                   reversal_effective_at
              from public.journal_transactions original_transaction
              join public.ledger_events original_event
                on original_event.book_id = original_transaction.book_id
               and original_event.event_id = original_transaction.source_event_id
              join public.journal_transactions reversal_transaction
                on reversal_transaction.book_id = new.book_id
               and reversal_transaction.transaction_id =
                   new.reversal_transaction_id
             where original_transaction.book_id = new.book_id
               and original_transaction.transaction_id =
                   new.original_transaction_id;

            select exists (
                select 1
                  from public.journal_postings original_posting
                  join public.accounts original_account
                    on original_account.book_id = original_posting.book_id
                   and original_account.account_id = original_posting.account_id
                   and original_account.asset_code = original_posting.asset_code
                 where original_posting.book_id = new.book_id
                   and original_posting.transaction_id =
                       new.original_transaction_id
                   and original_account.account_subtype = 'credit_card'
            ) into original_touches_credit_card;

            if original_kind like 'credit_card_%%'
               or original_touches_credit_card then
                if original_event_type = 'JournalTransactionReversed' then
                    raise exception using errcode = '23514',
                        message = 'credit-card reversals cannot be reversed';
                end if;
                if reversal_effective_at < original_effective_at then
                    raise exception using errcode = '23514',
                        message = 'credit-card reversal precedes its source';
                end if;
                if original_event_type = 'CreditCardTransactionRecorded'
                   and original_intent = 'charge'
                   and exists (
                       select 1
                         from public.credit_card_transactions refund
                         left join public.transaction_reversals refund_reversal
                           on refund_reversal.book_id = refund.book_id
                          and refund_reversal.original_transaction_id =
                              refund.transaction_id
                        where refund.book_id = new.book_id
                          and refund.intent = 'refund'
                          and refund.original_transaction_id =
                              new.original_transaction_id
                          and refund_reversal.reversal_transaction_id is null
                   ) then
                    raise exception using errcode = '23514',
                        message = 'credit-card charge has active refunds';
                end if;
            end if;
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        """
        create function public.v2_reject_credit_card_projection_mutation()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            raise exception using errcode = '23514',
                message = 'credit-card projections are append-only';
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        "create trigger trg_transaction_reversals_credit_card_validate "
        "before insert on public.transaction_reversals for each row "
        "execute function public.v2_validate_credit_card_reversal()"
    )
    connection.exec_driver_sql(
        "create trigger trg_credit_card_transactions_validate "
        "before insert on public.credit_card_transactions for each row "
        "execute function public.v2_validate_credit_card_projection()"
    )
    connection.exec_driver_sql(
        "create trigger trg_credit_card_transactions_immutable "
        "before update or delete on public.credit_card_transactions for each row "
        "execute function public.v2_reject_credit_card_projection_mutation()"
    )
    for function_name in (
        "v2_validate_credit_card_reversal()",
        "v2_validate_credit_card_projection()",
        "v2_reject_credit_card_projection_mutation()",
    ):
        connection.exec_driver_sql(
            f"revoke all privileges on function public.{function_name} "
            f"from public, {runtime}"
        )


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 credit-card migration is irreversible")
