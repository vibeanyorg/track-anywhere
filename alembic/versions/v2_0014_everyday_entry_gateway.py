"""Add durable Everyday Entry Gateway intents and duplicate evidence."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0014_everyday_entry_gateway"
down_revision = "v2_0013_frozen_import_fence"
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


def _create_prepared_intents() -> None:
    op.create_table(
        "prepared_entry_intents",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.SmallInteger(), nullable=False),
        sa.Column("prepared_status", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=16), nullable=False),
        sa.Column("commit_token_hash", sa.LargeBinary(), nullable=True),
        sa.Column("canonical_payload", postgresql.JSONB(), nullable=False),
        sa.Column("protected_content_ref", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_request_id", sa.Uuid(), nullable=True),
        sa.Column("committed_transaction_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "contract_version = 1",
            name=op.f("ck_prepared_entry_intents_contract_version_v1"),
        ),
        sa.CheckConstraint(
            "prepared_status in "
            "('ready','needs_clarification','duplicate_suspected','unsupported')",
            name=op.f("ck_prepared_entry_intents_prepared_status_valid"),
        ),
        sa.CheckConstraint(
            "lifecycle_status in ('created','consumed','cancelled')",
            name=op.f("ck_prepared_entry_intents_lifecycle_status_valid"),
        ),
        sa.CheckConstraint(
            "(prepared_status = 'ready' and commit_token_hash is not null "
            "and octet_length(commit_token_hash) = 32) "
            "or (prepared_status <> 'ready' and commit_token_hash is null)",
            name=op.f("ck_prepared_entry_intents_token_shape"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_payload) = 'object'",
            name=op.f("ck_prepared_entry_intents_payload_object"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_prepared_entry_intents_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "(lifecycle_status = 'created' and consumed_at is null "
            "and cancelled_at is null and committed_request_id is null "
            "and committed_transaction_id is null) "
            "or (lifecycle_status = 'consumed' and consumed_at is not null "
            "and cancelled_at is null and committed_request_id is not null "
            "and committed_transaction_id is not null) "
            "or (lifecycle_status = 'cancelled' and consumed_at is null "
            "and cancelled_at is not null and committed_request_id is null "
            "and committed_transaction_id is null)",
            name=op.f("ck_prepared_entry_intents_lifecycle_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.user_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "protected_content_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_prepared_entry_intents_protected_content",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "committed_transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_prepared_entry_intents_committed_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "intent_id",
            name=op.f("pk_prepared_entry_intents"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "actor_id",
            "intent_id",
            name="uq_prepared_entry_intents_actor_scope",
        ),
    )
    op.create_index(
        "ix_prepared_entry_intents_expiry",
        "prepared_entry_intents",
        ["expires_at"],
    )


def _create_duplicate_evidence() -> None:
    op.create_table(
        "everyday_entry_external_references",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("reference_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("source_intent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name=op.f("ck_everyday_entry_external_references_provider_valid"),
        ),
        sa.CheckConstraint(
            "reference_kind in "
            "('provider_transaction','provider_order','import_record')",
            name=op.f("ck_everyday_entry_external_references_kind_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(reference_hmac) = 32",
            name=op.f(
                "ck_everyday_entry_external_references_reference_hmac_length"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_intent_id"],
            ["prepared_entry_intents.book_id", "prepared_entry_intents.intent_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "provider_code",
            "reference_kind",
            "reference_hmac",
            name=op.f("pk_everyday_entry_external_references"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "transaction_id",
            "provider_code",
            "reference_kind",
            name="uq_everyday_entry_external_references_transaction_kind",
        ),
    )
    op.create_table(
        "everyday_entry_source_fingerprints",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("source_intent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(fingerprint_hmac) = 32",
            name=op.f(
                "ck_everyday_entry_source_fingerprints_fingerprint_hmac_length"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_intent_id"],
            ["prepared_entry_intents.book_id", "prepared_entry_intents.intent_id"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "transaction_id",
            "fingerprint_hmac",
            name=op.f("pk_everyday_entry_source_fingerprints"),
        ),
    )
    op.create_index(
        "ix_everyday_entry_source_fingerprints_lookup",
        "everyday_entry_source_fingerprints",
        ["book_id", "fingerprint_hmac", "created_at"],
    )


def _create_guards(runtime: str) -> None:
    op.execute(
        """
        create function public.v2_validate_prepared_entry_intent()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        declare
            narrative_kind varchar(32);
            narrative_status varchar(16);
        begin
            if new.protected_content_ref is null then
                return new;
            end if;
            select sidecar.kind, sidecar.status
              into narrative_kind, narrative_status
              from public.protected_description_sidecars sidecar
             where sidecar.book_id = new.book_id
               and sidecar.sidecar_id = new.protected_content_ref;
            if not found
               or narrative_kind <> 'transaction_narrative_v2'
               or narrative_status <> 'active' then
                raise exception using errcode = '23514',
                    message = 'prepared entry protected content is invalid';
            end if;
            return new;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        f"public.v2_validate_prepared_entry_intent() from public, {runtime}"
    )
    op.execute(
        "create trigger trg_prepared_entry_intents_validate "
        "before insert on public.prepared_entry_intents "
        "for each row execute function public.v2_validate_prepared_entry_intent()"
    )
    op.execute(
        """
        create function public.v2_guard_prepared_entry_intent()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            if tg_op = 'DELETE' then
                raise exception using errcode = '23514',
                    message = 'prepared entry intents cannot be deleted';
            end if;
            if old.lifecycle_status <> 'created' then
                raise exception using errcode = '23514',
                    message = 'terminal prepared entry intents are immutable';
            end if;
            if new.book_id is distinct from old.book_id
               or new.intent_id is distinct from old.intent_id
               or new.actor_id is distinct from old.actor_id
               or new.contract_version is distinct from old.contract_version
               or new.prepared_status is distinct from old.prepared_status
               or new.commit_token_hash is distinct from old.commit_token_hash
               or new.canonical_payload is distinct from old.canonical_payload
               or new.protected_content_ref is distinct from old.protected_content_ref
               or new.expires_at is distinct from old.expires_at
               or new.created_at is distinct from old.created_at then
                raise exception using errcode = '23514',
                    message = 'prepared entry intent bindings are immutable';
            end if;
            if new.lifecycle_status = 'consumed'
               and pg_catalog.clock_timestamp() >= old.expires_at then
                raise exception using errcode = '23514',
                    message = 'expired prepared entry intent cannot be consumed';
            end if;
            if new.lifecycle_status not in ('consumed', 'cancelled') then
                raise exception using errcode = '23514',
                    message = 'invalid prepared entry intent transition';
            end if;
            return new;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        f"public.v2_guard_prepared_entry_intent() from public, {runtime}"
    )
    op.execute(
        "create trigger trg_prepared_entry_intents_guard "
        "before update or delete on public.prepared_entry_intents "
        "for each row execute function public.v2_guard_prepared_entry_intent()"
    )
    op.execute(
        """
        create function public.v2_reject_everyday_entry_evidence_mutation()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            raise exception using errcode = '23514',
                message = 'everyday entry duplicate evidence is append-only';
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        "public.v2_reject_everyday_entry_evidence_mutation() "
        f"from public, {runtime}"
    )
    op.execute(
        """
        create function public.v2_validate_everyday_entry_evidence()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        declare
            intent_status varchar(16);
            intent_transaction_id uuid;
        begin
            select intent.lifecycle_status, intent.committed_transaction_id
              into intent_status, intent_transaction_id
              from public.prepared_entry_intents intent
             where intent.book_id = new.book_id
               and intent.intent_id = new.source_intent_id
             for key share;
            if not found
               or intent_status <> 'consumed'
               or intent_transaction_id is distinct from new.transaction_id then
                raise exception using errcode = '23514',
                    message = 'everyday entry duplicate evidence source is invalid';
            end if;
            return new;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        f"public.v2_validate_everyday_entry_evidence() from public, {runtime}"
    )
    for table_name in (
        "everyday_entry_external_references",
        "everyday_entry_source_fingerprints",
    ):
        op.execute(
            f"create trigger trg_{table_name}_validate "
            f"before insert on public.{table_name} "
            "for each row execute function "
            "public.v2_validate_everyday_entry_evidence()"
        )
        op.execute(
            f"create trigger trg_{table_name}_append_only "
            f"before update or delete on public.{table_name} "
            "for each row execute function "
            "public.v2_reject_everyday_entry_evidence_mutation()"
        )


def _apply_runtime_acl(runtime: str) -> None:
    for table_name in (
        "prepared_entry_intents",
        "everyday_entry_external_references",
        "everyday_entry_source_fingerprints",
    ):
        op.execute(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {runtime}"
        )
        op.execute(f"grant select on table public.{table_name} to {runtime}")
    op.execute(
        "grant insert (book_id, intent_id, actor_id, contract_version, "
        "prepared_status, lifecycle_status, commit_token_hash, canonical_payload, "
        "protected_content_ref, expires_at) on table public.prepared_entry_intents "
        f"to {runtime}"
    )
    op.execute(
        "grant update (lifecycle_status, consumed_at, cancelled_at, "
        "committed_request_id, committed_transaction_id) "
        f"on table public.prepared_entry_intents to {runtime}"
    )
    op.execute(
        "grant insert (book_id, provider_code, reference_kind, reference_hmac, "
        "transaction_id, source_intent_id) "
        f"on table public.everyday_entry_external_references to {runtime}"
    )
    op.execute(
        "grant insert (book_id, transaction_id, fingerprint_hmac, source_intent_id) "
        f"on table public.everyday_entry_source_fingerprints to {runtime}"
    )


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
        "'transfer', 'refund', 'fx', 'investment_cash', 'credit_card_charge', "
        "'credit_card_payment', 'credit_card_refund', 'credit_card_fee')",
    )
    op.add_column(
        "category_versions",
        sa.Column(
            "usage_kind",
            sa.String(length=16),
            server_default=sa.text("'both'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_category_versions_usage_kind_valid"),
        "category_versions",
        "usage_kind in ('expense', 'income', 'both')",
    )
    _create_prepared_intents()
    _create_duplicate_evidence()
    _create_guards(runtime)
    _apply_runtime_acl(runtime)


def downgrade() -> None:
    runtime = _runtime_role()
    for table_name in (
        "everyday_entry_external_references",
        "everyday_entry_source_fingerprints",
        "prepared_entry_intents",
    ):
        op.execute(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {runtime}"
        )
    for table_name in (
        "everyday_entry_external_references",
        "everyday_entry_source_fingerprints",
    ):
        op.execute(
            f"drop trigger trg_{table_name}_validate on public.{table_name}"
        )
        op.execute(
            f"drop trigger trg_{table_name}_append_only on public.{table_name}"
        )
    op.execute("drop function public.v2_validate_everyday_entry_evidence()")
    op.execute(
        "drop function public.v2_reject_everyday_entry_evidence_mutation()"
    )
    op.execute(
        "drop trigger trg_prepared_entry_intents_guard "
        "on public.prepared_entry_intents"
    )
    op.execute("drop function public.v2_guard_prepared_entry_intent()")
    op.execute(
        "drop trigger trg_prepared_entry_intents_validate "
        "on public.prepared_entry_intents"
    )
    op.execute("drop function public.v2_validate_prepared_entry_intent()")
    op.drop_index(
        "ix_everyday_entry_source_fingerprints_lookup",
        table_name="everyday_entry_source_fingerprints",
    )
    op.drop_table("everyday_entry_source_fingerprints")
    op.drop_table("everyday_entry_external_references")
    op.drop_index(
        "ix_prepared_entry_intents_expiry",
        table_name="prepared_entry_intents",
    )
    op.drop_table("prepared_entry_intents")
    op.drop_constraint(
        op.f("ck_category_versions_usage_kind_valid"),
        "category_versions",
        type_="check",
    )
    op.drop_column("category_versions", "usage_kind")
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
