"""Harden protected content and add sealed import archive manifests."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0012_protected_content"
down_revision = "v2_0011_oauth_resource_binding"
branch_labels = None
depends_on = None


_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_ALGORITHM = "AES-256-GCM+HKDF-SHA256"


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise RuntimeError(
            "TRACK_ANYWHERE_DB_RUNTIME_ROLE is required and must be safe"
        )
    return f'"{value}"'


def _replace_sidecar_guard(runtime: str, *, hardened: bool) -> None:
    op.execute(
        "drop trigger if exists trg_description_sidecars_guard_identity "
        "on public.protected_description_sidecars"
    )
    if hardened:
        body = """
            if tg_op = 'DELETE' then
                raise exception using errcode = '23514',
                    message = 'protected content cannot be deleted';
            end if;
            if new is not distinct from old then
                return new;
            end if;
            if old.status = 'active'
               and new.status = 'erased'
               and new.book_id is not distinct from old.book_id
               and new.sidecar_id is not distinct from old.sidecar_id
               and new.kind is not distinct from old.kind
               and new.algorithm is not distinct from old.algorithm
               and new.content_hash is not distinct from old.content_hash
               and new.created_at is not distinct from old.created_at
               and new.ciphertext is null
               and new.key_ref is null
               and new.nonce is null
               and new.erased_at is not null
               and not exists (
                   select 1
                     from public.import_archive_manifests manifest
                    where manifest.book_id = old.book_id
                      and manifest.archive_id = old.sidecar_id
               ) then
                return new;
            end if;
            raise exception using errcode = '23514',
                message = 'protected content is immutable';
        """
        timing = "before update or delete"
    else:
        body = """
            if new.book_id is distinct from old.book_id
               or new.sidecar_id is distinct from old.sidecar_id then
                raise exception using errcode = '23514',
                    message = 'description sidecar identity is immutable';
            end if;
            if old.status = 'erased' and new is distinct from old then
                raise exception using errcode = '23514',
                    message = 'erased description sidecars are immutable';
            end if;
            return new;
        """
        timing = "before update"
    op.execute(
        f"""
        create or replace function public.v2_guard_description_sidecar_identity()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            {body}
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        "public.v2_guard_description_sidecar_identity() "
        f"from public, {runtime}"
    )
    op.execute(
        "create trigger trg_description_sidecars_guard_identity "
        f"{timing} on public.protected_description_sidecars for each row "
        "execute function public.v2_guard_description_sidecar_identity()"
    )


def _create_archive_table() -> None:
    op.create_table(
        "import_archive_manifests",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("archive_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version", sa.SmallInteger(), nullable=False),
        sa.Column("source_dump_hash", sa.LargeBinary(), nullable=False),
        sa.Column("source_manifest_hash", sa.LargeBinary(), nullable=False),
        sa.Column("card_review_hash", sa.LargeBinary(), nullable=False),
        sa.Column("plan_hash", sa.LargeBinary(), nullable=False),
        sa.Column("archive_content_commitment", sa.LargeBinary(), nullable=False),
        sa.Column("seal", sa.LargeBinary(), nullable=False),
        sa.Column("record_counts", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "contract_version = 1",
            name=op.f("ck_import_archive_manifests_contract_version_v1"),
        ),
        sa.CheckConstraint(
            "octet_length(source_dump_hash) = 32",
            name=op.f("ck_import_archive_manifests_source_dump_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(source_manifest_hash) = 32",
            name=op.f("ck_import_archive_manifests_source_manifest_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(card_review_hash) = 32",
            name=op.f("ck_import_archive_manifests_card_review_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(plan_hash) = 32",
            name=op.f("ck_import_archive_manifests_plan_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(archive_content_commitment) = 32",
            name=op.f(
                "ck_import_archive_manifests_archive_content_commitment_length"
            ),
        ),
        sa.CheckConstraint(
            "octet_length(seal) = 32",
            name=op.f("ck_import_archive_manifests_seal_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(record_counts) = 'object'",
            name=op.f("ck_import_archive_manifests_counts_object"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "archive_id"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_import_archive_manifests_sidecar",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "archive_id",
            name=op.f("pk_import_archive_manifests"),
        ),
    )


def _create_archive_guards(runtime: str) -> None:
    op.execute(
        """
        create function public.v2_guard_import_archive_manifest()
        returns trigger language plpgsql security definer
        set search_path = pg_catalog, public as $function$
        declare
            locked_kind varchar(32);
            locked_status varchar(16);
            locked_content_hash bytea;
        begin
            if tg_op <> 'INSERT' then
                raise exception using errcode = '23514',
                    message = 'import archive manifests are append-only';
            end if;
            select sidecar.kind, sidecar.status, sidecar.content_hash
              into locked_kind, locked_status, locked_content_hash
              from public.protected_description_sidecars sidecar
             where sidecar.book_id = new.book_id
               and sidecar.sidecar_id = new.archive_id
               for update;
            if not found
               or locked_kind <> 'import_archive'
               or locked_status <> 'active'
               or locked_content_hash <> new.archive_content_commitment then
                raise exception using errcode = '23514',
                    message = 'import archive manifest sidecar is invalid';
            end if;
            return new;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        f"public.v2_guard_import_archive_manifest() from public, {runtime}"
    )
    op.execute(
        "create trigger trg_import_archive_manifests_guard "
        "before insert or update or delete on public.import_archive_manifests "
        "for each row execute function public.v2_guard_import_archive_manifest()"
    )

    op.execute(
        """
        create function public.v2_erase_protected_content(
            requested_book_id uuid,
            requested_sidecar_id uuid
        ) returns boolean
        language plpgsql security definer
        set search_path = pg_catalog, public as $function$
        declare
            locked_status varchar(16);
        begin
            select sidecar.status
              into locked_status
              from public.protected_description_sidecars sidecar
             where sidecar.book_id = requested_book_id
               and sidecar.sidecar_id = requested_sidecar_id
               for update;
            if not found then
                return null;
            end if;
            if locked_status = 'erased' then
                return false;
            end if;
            if locked_status <> 'active' then
                raise exception using errcode = '23514',
                    message = 'protected content state is invalid';
            end if;
            if exists (
                select 1
                  from public.import_archive_manifests manifest
                 where manifest.book_id = requested_book_id
                   and manifest.archive_id = requested_sidecar_id
            ) then
                raise exception using errcode = '23514',
                    message = 'import archive content cannot be erased';
            end if;
            update public.protected_description_sidecars
               set ciphertext = null,
                   key_ref = null,
                   nonce = null,
                   status = 'erased',
                   erased_at = pg_catalog.clock_timestamp()
             where book_id = requested_book_id
               and sidecar_id = requested_sidecar_id
               and status = 'active';
            return true;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        "public.v2_erase_protected_content(uuid, uuid) "
        f"from public, {runtime}"
    )
    op.execute(
        "grant execute on function public.v2_erase_protected_content(uuid, uuid) "
        f"to {runtime}"
    )


def _harden_sidecar_constraints() -> None:
    op.drop_constraint(
        op.f("ck_protected_description_sidecars_algorithm_nonblank"),
        "protected_description_sidecars",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_protected_description_sidecars_lifecycle_shape"),
        "protected_description_sidecars",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_algorithm_approved"),
        "protected_description_sidecars",
        f"algorithm = '{_ALGORITHM}'",
    )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_status_valid"),
        "protected_description_sidecars",
        "status in ('active', 'erased')",
    )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_active_envelope_shape"),
        "protected_description_sidecars",
        "status <> 'active' or (octet_length(ciphertext) >= 16 "
        "and octet_length(nonce) = 12 "
        "and key_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')",
    )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_lifecycle_shape"),
        "protected_description_sidecars",
        "(status = 'active' and erased_at is null "
        "and ciphertext is not null and key_ref is not null and nonce is not null) "
        "or (status = 'erased' and erased_at is not null "
        "and ciphertext is null and key_ref is null and nonce is null)",
    )


def _restore_sidecar_constraints() -> None:
    for name in (
        "active_envelope_shape",
        "status_valid",
        "algorithm_approved",
        "lifecycle_shape",
    ):
        op.drop_constraint(
            op.f(f"ck_protected_description_sidecars_{name}"),
            "protected_description_sidecars",
            type_="check",
        )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_algorithm_nonblank"),
        "protected_description_sidecars",
        "btrim(algorithm) <> ''",
    )
    op.create_check_constraint(
        op.f("ck_protected_description_sidecars_lifecycle_shape"),
        "protected_description_sidecars",
        "(status = 'active' and erased_at is null "
        "and ciphertext is not null and key_ref is not null and nonce is not null) "
        "or (status = 'erased' and erased_at is not null "
        "and ciphertext is null and key_ref is null and nonce is null)",
    )


def upgrade() -> None:
    runtime = _runtime_role()
    _harden_sidecar_constraints()
    _create_archive_table()
    _replace_sidecar_guard(runtime, hardened=True)
    _create_archive_guards(runtime)

    op.execute(
        "revoke all privileges on table public.protected_description_sidecars "
        f"from public, {runtime}"
    )
    op.execute(
        "grant select, insert on table public.protected_description_sidecars "
        f"to {runtime}"
    )
    op.execute(
        "revoke all privileges on table public.import_archive_manifests "
        f"from public, {runtime}"
    )
    op.execute(
        "grant select, insert on table public.import_archive_manifests "
        f"to {runtime}"
    )


def downgrade() -> None:
    runtime = _runtime_role()
    op.execute(
        "revoke all privileges on function "
        "public.v2_erase_protected_content(uuid, uuid) "
        f"from public, {runtime}"
    )
    op.execute("drop function public.v2_erase_protected_content(uuid, uuid)")
    op.execute(
        "drop trigger trg_import_archive_manifests_guard "
        "on public.import_archive_manifests"
    )
    op.execute("drop function public.v2_guard_import_archive_manifest()")
    op.drop_table("import_archive_manifests")
    _replace_sidecar_guard(runtime, hardened=False)
    _restore_sidecar_constraints()
    op.execute(
        "revoke all privileges on table public.protected_description_sidecars "
        f"from public, {runtime}"
    )
    op.execute(
        "grant select, insert, update on table "
        f"public.protected_description_sidecars to {runtime}"
    )
