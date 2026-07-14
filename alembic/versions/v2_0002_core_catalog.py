"""Add immutable V2 catalog, privacy, identity, and OAuth tables."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0002_core_catalog"
down_revision = "v2_0001_schema_guard"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_MUTABLE_TABLES = (
    "accounts",
    "assets",
    "auth_identities",
    "book_members",
    "books",
    "browser_sessions",
    "categories",
    "credentials",
    "oauth_authorization_grants",
    "oauth_client_redirect_uris",
    "oauth_clients",
    "oauth_device_grants",
    "password_accounts",
    "protected_description_sidecars",
    "users",
)
_APPEND_ONLY_TABLES = ("category_versions",)
_SCOPE_ARRAY_CHECK = (
    "jsonb_typeof(scopes) = 'array' and "
    "scopes = jsonb_path_query_array(scopes, "
    '\'$[*] ? (@.type() == "string" && @ like_regex "\\\\S")\')'
)
_PASSWORD_HASH_CHECK = (
    "password_hash ~ '^pbkdf2_sha256[$]390000[$][A-Za-z0-9_-]{24}[$][0-9a-f]{64}$'"
)


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier) or len(identifier.encode("ascii")) > 63:
        raise RuntimeError(
            "database runtime role must be a safe lowercase PostgreSQL identifier"
        )
    return f'"{identifier}"'


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not value:
        raise RuntimeError("TRACK_ANYWHERE_DB_RUNTIME_ROLE is required")
    _quote_identifier(value)
    return value


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )


def _create_catalog_tables() -> None:
    op.create_table(
        "assets",
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ledger_scale", sa.SmallInteger(), nullable=False),
        sa.Column("input_scale", sa.SmallInteger(), nullable=False),
        sa.Column("display_scale", sa.SmallInteger(), nullable=False),
        sa.Column("current_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("btrim(kind) <> ''", name=op.f("ck_assets_kind_nonblank")),
        sa.CheckConstraint(
            "ledger_scale between 0 and 30",
            name=op.f("ck_assets_ledger_scale_range"),
        ),
        sa.CheckConstraint(
            "input_scale between 0 and ledger_scale",
            name=op.f("ck_assets_input_scale_range"),
        ),
        sa.CheckConstraint(
            "display_scale between 0 and ledger_scale",
            name=op.f("ck_assets_display_scale_range"),
        ),
        sa.CheckConstraint(
            "btrim(current_name) <> ''",
            name=op.f("ck_assets_current_name_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_assets_status_valid"),
        ),
        sa.PrimaryKeyConstraint("asset_code", name=op.f("pk_assets")),
    )
    op.create_table(
        "books",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("current_name", sa.Text(), nullable=False),
        sa.Column("base_asset_code", sa.String(length=16), nullable=True),
        sa.Column("write_state", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(current_name) <> ''",
            name=op.f("ck_books_current_name_nonblank"),
        ),
        sa.CheckConstraint(
            "write_state in ('active', 'paused_integrity')",
            name=op.f("ck_books_write_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["base_asset_code"],
            ["assets.asset_code"],
            name=op.f("fk_books_base_asset_code_assets"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", name=op.f("pk_books")),
    )
    op.create_table(
        "accounts",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("system_role", sa.String(length=32), nullable=True),
        sa.Column("current_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(account_type) <> ''",
            name=op.f("ck_accounts_account_type_nonblank"),
        ),
        sa.CheckConstraint(
            "system_role is null or btrim(system_role) <> ''",
            name=op.f("ck_accounts_system_role_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(current_name) <> ''",
            name=op.f("ck_accounts_current_name_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'closed')",
            name=op.f("ck_accounts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_code"],
            ["assets.asset_code"],
            name=op.f("fk_accounts_asset_code_assets"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_accounts_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", "account_id", name=op.f("pk_accounts")),
        sa.UniqueConstraint(
            "book_id",
            "account_id",
            "asset_code",
            name=op.f("uq_accounts_book_account_asset"),
        ),
    )
    op.create_index(
        "ux_accounts_system_role",
        "accounts",
        ["book_id", "asset_code", "system_role"],
        unique=True,
        postgresql_where=sa.text("system_role IS NOT NULL"),
    )
    op.create_table(
        "categories",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("parent_category_id", sa.Uuid(), nullable=True),
        sa.Column("current_name", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(current_name) <> ''",
            name=op.f("ck_categories_current_name_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'archived')",
            name=op.f("ck_categories_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name="fk_categories_book",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "parent_category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_categories_parent_category",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", "category_id", name=op.f("pk_categories")),
    )
    op.create_table(
        "category_versions",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("category_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_category_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("change_reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(name) <> ''", name=op.f("ck_category_versions_name_nonblank")
        ),
        sa.CheckConstraint(
            "status in ('active', 'archived')",
            name=op.f("ck_category_versions_status_valid"),
        ),
        sa.CheckConstraint(
            "btrim(change_reason_code) <> ''",
            name=op.f("ck_category_versions_change_reason_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_category_versions_category",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "parent_category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_category_versions_parent_category",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "category_id",
            "category_version_id",
            name=op.f("pk_category_versions"),
        ),
    )
    op.create_foreign_key(
        "fk_categories_current_version",
        "categories",
        "category_versions",
        ["book_id", "category_id", "current_version_id"],
        ["book_id", "category_id", "category_version_id"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )
    op.create_table(
        "protected_description_sidecars",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("sidecar_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("key_ref", sa.Text(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(), nullable=True),
        sa.Column("algorithm", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(kind) <> ''",
            name=op.f("ck_protected_description_sidecars_kind_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(algorithm) <> ''",
            name=op.f("ck_protected_description_sidecars_algorithm_nonblank"),
        ),
        sa.CheckConstraint(
            "octet_length(content_hash) = 32",
            name=op.f("ck_protected_description_sidecars_content_hash_length"),
        ),
        sa.CheckConstraint(
            "(status = 'active' and erased_at is null "
            "and ciphertext is not null and key_ref is not null "
            "and nonce is not null) "
            "or (status = 'erased' and erased_at is not null "
            "and ciphertext is null and key_ref is null and nonce is null)",
            name=op.f("ck_protected_description_sidecars_lifecycle_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_protected_description_sidecars_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id", "sidecar_id", name=op.f("pk_protected_description_sidecars")
        ),
    )


def _create_auth_tables() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("current_display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "subject_type in ('human', 'machine')",
            name=op.f("ck_users_subject_type_valid"),
        ),
        sa.CheckConstraint(
            "btrim(current_display_name) <> ''",
            name=op.f("ck_users_display_name_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_users_status_valid"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint(
            "user_id", "subject_type", name=op.f("uq_users_id_subject_type")
        ),
    )
    op.create_table(
        "book_members",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role in ('owner', 'admin', 'editor', 'viewer', 'auditor')",
            name=op.f("ck_book_members_role_valid"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'revoked')",
            name=op.f("ck_book_members_status_valid"),
        ),
        sa.CheckConstraint(
            _SCOPE_ARRAY_CHECK, name=op.f("ck_book_members_scopes_array")
        ),
        sa.CheckConstraint(
            "(status = 'active' and revoked_at is null) "
            "or (status = 'revoked' and revoked_at is not null)",
            name=op.f("ck_book_members_revocation_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_book_members_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_book_members_user_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", "user_id", name=op.f("pk_book_members")),
    )
    op.create_table(
        "auth_identities",
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(provider) <> ''",
            name=op.f("ck_auth_identities_provider_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(subject) <> ''",
            name=op.f("ck_auth_identities_subject_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_auth_identities_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_auth_identities_user_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("identity_id", name=op.f("pk_auth_identities")),
        sa.UniqueConstraint(
            "provider",
            "subject",
            name=op.f("uq_auth_identities_provider_subject"),
        ),
    )
    op.create_table(
        "password_accounts",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("normalized_email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            _PASSWORD_HASH_CHECK,
            name=op.f("ck_password_accounts_password_hash_pbkdf2"),
        ),
        sa.CheckConstraint(
            "normalized_email = lower(btrim(normalized_email)) "
            "and normalized_email <> ''",
            name=op.f("ck_password_accounts_email_normalized"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_password_accounts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_password_accounts_user_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_password_accounts")),
        sa.UniqueConstraint(
            "normalized_email", name=op.f("uq_password_accounts_normalized_email")
        ),
    )
    op.create_table(
        "credentials",
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("jti", sa.Uuid(), nullable=False),
        sa.Column("actor_subject_id", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("auth_kind", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name=op.f("ck_credentials_token_hash_length"),
        ),
        sa.CheckConstraint(
            "actor_type in ('human', 'machine')",
            name=op.f("ck_credentials_actor_type_valid"),
        ),
        sa.CheckConstraint(
            "auth_kind in ('api_key', 'pkce', 'device', 'browser_session')",
            name=op.f("ck_credentials_auth_kind_valid"),
        ),
        sa.CheckConstraint(
            _SCOPE_ARRAY_CHECK, name=op.f("ck_credentials_scopes_array")
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_credentials_expiry_after_issue"),
        ),
        sa.CheckConstraint(
            "revoked_at is null or revoked_at >= issued_at",
            name=op.f("ck_credentials_revoked_after_issue"),
        ),
        sa.CheckConstraint(
            "last_used_at is null or last_used_at >= issued_at",
            name=op.f("ck_credentials_last_used_after_issue"),
        ),
        sa.CheckConstraint(
            "actor_type <> 'machine' "
            "or (auth_kind = 'api_key' and book_id is not null)",
            name=op.f("ck_credentials_machine_book_required"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_subject_id", "actor_type"],
            ["users.user_id", "users.subject_type"],
            name="fk_credentials_actor_subject_type",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_credentials_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("credential_id", name=op.f("pk_credentials")),
        sa.UniqueConstraint("jti", name=op.f("uq_credentials_jti")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_credentials_token_hash")),
        sa.UniqueConstraint(
            "token_hash",
            "actor_subject_id",
            name=op.f("uq_credentials_token_actor"),
        ),
    )
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=False),
        sa.Column("client_type", sa.String(length=16), nullable=False),
        sa.Column("client_secret_hash", sa.LargeBinary(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(client_name) <> ''",
            name=op.f("ck_oauth_clients_client_name_nonblank"),
        ),
        sa.CheckConstraint(
            "client_type in ('public', 'confidential')",
            name=op.f("ck_oauth_clients_client_type_valid"),
        ),
        sa.CheckConstraint(
            "(client_type = 'public' and client_secret_hash is null) "
            "or (client_type = 'confidential' "
            "and octet_length(client_secret_hash) = 32)",
            name=op.f("ck_oauth_clients_client_secret_shape"),
        ),
        sa.CheckConstraint(
            _SCOPE_ARRAY_CHECK, name=op.f("ck_oauth_clients_scopes_array")
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_oauth_clients_status_valid"),
        ),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_clients")),
    )
    op.create_table(
        "oauth_client_redirect_uris",
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(redirect_uri) <> ''",
            name=op.f("ck_oauth_client_redirect_uris_redirect_uri_nonblank"),
        ),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_oauth_client_redirect_uris_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            name=op.f("fk_oauth_client_redirect_uris_client_id_oauth_clients"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "client_id", "redirect_uri", name=op.f("pk_oauth_client_redirect_uris")
        ),
    )
    op.create_table(
        "oauth_authorization_grants",
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("actor_subject_id", sa.String(length=128), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("challenge_method", sa.String(length=8), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(code_hash) = 32",
            name=op.f("ck_oauth_authorization_grants_code_hash_length"),
        ),
        sa.CheckConstraint(
            _SCOPE_ARRAY_CHECK,
            name=op.f("ck_oauth_authorization_grants_scopes_array"),
        ),
        sa.CheckConstraint(
            "length(code_challenge) between 43 and 128",
            name=op.f("ck_oauth_authorization_grants_code_challenge_length"),
        ),
        sa.CheckConstraint(
            "challenge_method = 'S256'",
            name=op.f("ck_oauth_authorization_grants_challenge_method_s256"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_oauth_authorization_grants_expiry_after_create"),
        ),
        sa.CheckConstraint(
            "used_at is null or used_at >= created_at",
            name=op.f("ck_oauth_authorization_grants_used_after_create"),
        ),
        sa.CheckConstraint(
            "revoked_at is null or revoked_at >= created_at",
            name=op.f("ck_oauth_authorization_grants_revoked_after_create"),
        ),
        sa.CheckConstraint(
            "used_at is null or revoked_at is null",
            name=op.f("ck_oauth_authorization_grants_terminal_state_exclusive"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_subject_id"],
            ["users.user_id"],
            name=op.f("fk_oauth_authorization_grants_actor_subject_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "redirect_uri"],
            [
                "oauth_client_redirect_uris.client_id",
                "oauth_client_redirect_uris.redirect_uri",
            ],
            name="fk_oauth_authorization_grants_registered_redirect",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "code_hash", name=op.f("pk_oauth_authorization_grants")
        ),
    )
    op.create_table(
        "oauth_device_grants",
        sa.Column("device_code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("approved_actor_subject_id", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(device_code_hash) = 32",
            name=op.f("ck_oauth_device_grants_device_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(user_code_hash) = 32",
            name=op.f("ck_oauth_device_grants_user_hash_length"),
        ),
        sa.CheckConstraint(
            _SCOPE_ARRAY_CHECK, name=op.f("ck_oauth_device_grants_scopes_array")
        ),
        sa.CheckConstraint(
            "status in ('pending', 'approved', 'denied', 'consumed', 'expired')",
            name=op.f("ck_oauth_device_grants_status_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_oauth_device_grants_expiry_after_create"),
        ),
        sa.CheckConstraint(
            "interval_seconds > 0",
            name=op.f("ck_oauth_device_grants_interval_positive"),
        ),
        sa.CheckConstraint(
            "poll_count >= 0",
            name=op.f("ck_oauth_device_grants_poll_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "last_poll_at is null or last_poll_at >= created_at",
            name=op.f("ck_oauth_device_grants_last_poll_after_create"),
        ),
        sa.CheckConstraint(
            "(approved_actor_subject_id is null and approved_at is null) "
            "or (approved_actor_subject_id is not null and approved_at is not null)",
            name=op.f("ck_oauth_device_grants_approval_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' and approved_at is null and consumed_at is null) "
            "or (status = 'approved' and approved_at is not null "
            "and consumed_at is null) "
            "or (status = 'consumed' and approved_at is not null "
            "and consumed_at is not null and consumed_at >= approved_at) "
            "or (status = 'denied' and approved_at is null "
            "and consumed_at is null) "
            "or (status = 'expired' and consumed_at is null)",
            name=op.f("ck_oauth_device_grants_lifecycle_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_actor_subject_id"],
            ["users.user_id"],
            name=op.f("fk_oauth_device_grants_approved_actor_subject_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            name=op.f("fk_oauth_device_grants_client_id_oauth_clients"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "device_code_hash", name=op.f("pk_oauth_device_grants")
        ),
        sa.UniqueConstraint(
            "user_code_hash", name=op.f("uq_oauth_device_user_code_hash")
        ),
    )
    op.create_table(
        "browser_sessions",
        sa.Column("session_hash", sa.LargeBinary(), nullable=False),
        sa.Column("csrf_token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("credential_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(session_hash) = 32",
            name=op.f("ck_browser_sessions_session_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(csrf_token_hash) = 32",
            name=op.f("ck_browser_sessions_csrf_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(credential_hash) = 32",
            name=op.f("ck_browser_sessions_credential_hash_length"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_browser_sessions_expiry_after_issue"),
        ),
        sa.CheckConstraint(
            "revoked_at is null or revoked_at >= issued_at",
            name=op.f("ck_browser_sessions_revoked_after_issue"),
        ),
        sa.CheckConstraint(
            "last_seen_at is null or last_seen_at >= issued_at",
            name=op.f("ck_browser_sessions_last_seen_after_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_hash", "user_id"],
            ["credentials.token_hash", "credentials.actor_subject_id"],
            name="fk_browser_sessions_credential_subject",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_browser_sessions_user_id_users"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_hash", name=op.f("pk_browser_sessions")),
    )


def _create_immutability_triggers(runtime_role: str) -> None:
    connection = op.get_bind()
    function_definitions = {
        "v2_guard_asset_identity": """
            if new.asset_code is distinct from old.asset_code
               or new.ledger_scale is distinct from old.ledger_scale then
                raise exception using
                    errcode = '23514',
                    message = 'asset identity and ledger scale are immutable';
            end if;
            return new;
        """,
        "v2_guard_book_identity": """
            if new.book_id is distinct from old.book_id then
                raise exception using
                    errcode = '23514',
                    message = 'book identity is immutable';
            end if;
            return new;
        """,
        "v2_guard_user_principal": """
            if new.user_id is distinct from old.user_id
               or new.subject_type is distinct from old.subject_type
               or new.created_at is distinct from old.created_at then
                raise exception using
                    errcode = '23514',
                    message = 'user principal identity is immutable';
            end if;
            return new;
        """,
        "v2_guard_book_member_binding": """
            if new.book_id is distinct from old.book_id
               or new.user_id is distinct from old.user_id
               or new.created_at is distinct from old.created_at then
                raise exception using
                    errcode = '23514',
                    message = 'book membership binding is immutable';
            end if;
            return new;
        """,
        "v2_guard_auth_identity_principal": """
            if tg_op = 'UPDATE'
               and (new.identity_id is distinct from old.identity_id
                    or new.provider is distinct from old.provider
                    or new.subject is distinct from old.subject
                    or new.user_id is distinct from old.user_id
                    or new.created_at is distinct from old.created_at) then
                raise exception using
                    errcode = '23514',
                    message = 'interactive auth identity binding is immutable';
            end if;
            perform 1
              from public.users
             where user_id = new.user_id
               and subject_type = 'human';
            if not found then
                raise exception using
                    errcode = '23514',
                    message = 'interactive auth identity requires a human user';
            end if;
            return new;
        """,
        "v2_guard_password_account_principal": """
            if tg_op = 'UPDATE'
               and (new.user_id is distinct from old.user_id
                    or new.created_at is distinct from old.created_at) then
                raise exception using
                    errcode = '23514',
                    message = 'password account principal binding is immutable';
            end if;
            perform 1
              from public.users
             where user_id = new.user_id
               and subject_type = 'human';
            if not found then
                raise exception using
                    errcode = '23514',
                    message = 'password account requires a human user';
            end if;
            return new;
        """,
        "v2_guard_account_identity": """
            if new.book_id is distinct from old.book_id
               or new.account_id is distinct from old.account_id
               or new.asset_code is distinct from old.asset_code
               or new.system_role is distinct from old.system_role then
                raise exception using
                    errcode = '23514',
                    message = 'account accounting identity is immutable';
            end if;
            return new;
        """,
        "v2_guard_category_identity": """
            if new.book_id is distinct from old.book_id
               or new.category_id is distinct from old.category_id then
                raise exception using
                    errcode = '23514',
                    message = 'category identity is immutable';
            end if;
            return new;
        """,
        "v2_reject_category_version_mutation": """
            raise exception using
                errcode = '23514',
                message = 'category versions are append-only';
        """,
        "v2_guard_description_sidecar_identity": """
            if new.book_id is distinct from old.book_id
               or new.sidecar_id is distinct from old.sidecar_id then
                raise exception using
                    errcode = '23514',
                    message = 'description sidecar identity is immutable';
            end if;
            if old.status = 'erased' and new is distinct from old then
                raise exception using
                    errcode = '23514',
                    message = 'erased description sidecars are immutable';
            end if;
            return new;
        """,
        "v2_guard_credential_lifecycle": """
            if new.credential_id is distinct from old.credential_id
               or new.token_hash is distinct from old.token_hash
               or new.jti is distinct from old.jti
               or new.actor_subject_id is distinct from old.actor_subject_id
               or new.actor_type is distinct from old.actor_type
               or new.auth_kind is distinct from old.auth_kind
               or new.book_id is distinct from old.book_id
               or new.scopes is distinct from old.scopes
               or new.issued_at is distinct from old.issued_at
               or new.expires_at is distinct from old.expires_at then
                raise exception using
                    errcode = '23514',
                    message = 'credential issuance bindings are immutable';
            end if;
            if old.revoked_at is not null
               and new.revoked_at is distinct from old.revoked_at then
                raise exception using
                    errcode = '23514',
                    message = 'credential revocation is irreversible';
            end if;
            if old.last_used_at is not null
               and (new.last_used_at is null
                    or new.last_used_at < old.last_used_at) then
                raise exception using
                    errcode = '23514',
                    message = 'credential last-used time must be monotonic';
            end if;
            return new;
        """,
        "v2_guard_authorization_grant_lifecycle": """
            if new.code_hash is distinct from old.code_hash
               or new.client_id is distinct from old.client_id
               or new.redirect_uri is distinct from old.redirect_uri
               or new.actor_subject_id is distinct from old.actor_subject_id
               or new.scopes is distinct from old.scopes
               or new.code_challenge is distinct from old.code_challenge
               or new.challenge_method is distinct from old.challenge_method
               or new.resource is distinct from old.resource
               or new.created_at is distinct from old.created_at
               or new.expires_at is distinct from old.expires_at then
                raise exception using
                    errcode = '23514',
                    message = 'authorization grant issuance bindings are immutable';
            end if;
            if old.used_at is not null
               and new.used_at is distinct from old.used_at then
                raise exception using
                    errcode = '23514',
                    message = 'authorization grant use is irreversible';
            end if;
            if old.revoked_at is not null
               and new.revoked_at is distinct from old.revoked_at then
                raise exception using
                    errcode = '23514',
                    message = 'authorization grant revocation is irreversible';
            end if;
            return new;
        """,
        "v2_guard_browser_session_lifecycle": """
            if tg_op = 'UPDATE' then
                if new.session_hash is distinct from old.session_hash
                   or new.csrf_token_hash is distinct from old.csrf_token_hash
                   or new.credential_hash is distinct from old.credential_hash
                   or new.user_id is distinct from old.user_id
                   or new.issued_at is distinct from old.issued_at
                   or new.expires_at is distinct from old.expires_at then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session issuance bindings are immutable';
                end if;
                if old.revoked_at is not null
                   and new.revoked_at is distinct from old.revoked_at then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session revocation is irreversible';
                end if;
                if old.last_seen_at is not null
                   and (new.last_seen_at is null
                        or new.last_seen_at < old.last_seen_at) then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session last-seen time must be monotonic';
                end if;
                return new;
            end if;

            declare
                bound_subject_id varchar(128);
                bound_actor_type varchar(16);
                bound_auth_kind varchar(32);
                credential_issued_at timestamptz;
                credential_expires_at timestamptz;
                credential_revoked_at timestamptz;
            begin
                select actor_subject_id, actor_type, auth_kind,
                       issued_at, expires_at, revoked_at
                  into bound_subject_id, bound_actor_type, bound_auth_kind,
                       credential_issued_at, credential_expires_at,
                       credential_revoked_at
                  from public.credentials
                 where token_hash = new.credential_hash;
                if not found
                   or bound_subject_id is distinct from new.user_id
                   or bound_actor_type <> 'human'
                   or bound_auth_kind <> 'browser_session' then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session must bind a human browser credential';
                end if;
                if credential_revoked_at is not null then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session credential is revoked';
                end if;
                if new.issued_at < credential_issued_at
                   or new.expires_at > credential_expires_at then
                    raise exception using
                        errcode = '23514',
                        message = 'browser session must stay within credential lifetime';
                end if;
            end;
            return new;
        """,
        "v2_guard_device_grant_transition": """
            if old.status in ('denied', 'consumed', 'expired')
               and new is distinct from old then
                raise exception using
                    errcode = '23514',
                    message = 'terminal device grants are immutable';
            end if;
            if new.device_code_hash is distinct from old.device_code_hash
               or new.user_code_hash is distinct from old.user_code_hash
               or new.client_id is distinct from old.client_id
               or new.resource is distinct from old.resource
               or new.created_at is distinct from old.created_at
               or new.expires_at is distinct from old.expires_at then
                raise exception using
                    errcode = '23514',
                    message = 'device grant issuance bindings are immutable';
            end if;
            if new.scopes is distinct from old.scopes
               and not (old.status = 'pending'
                        and new.status = 'approved'
                        and new.scopes <@ old.scopes) then
                raise exception using
                    errcode = '23514',
                    message = 'device grant scopes may only narrow on approval';
            end if;
            if new.poll_count = old.poll_count
               and new.last_poll_at is not distinct from old.last_poll_at then
                if new.interval_seconds is distinct from old.interval_seconds then
                    raise exception using
                        errcode = '23514',
                        message = 'device grant interval may only change during a poll';
                end if;
            elsif new.poll_count = old.poll_count + 1
                  and new.last_poll_at is not null
                  and (old.last_poll_at is null
                       or new.last_poll_at > old.last_poll_at) then
                if new.interval_seconds <> old.interval_seconds
                   and new.interval_seconds <> old.interval_seconds + 5 then
                    raise exception using
                        errcode = '23514',
                        message = 'device grant poll interval may only increase by five';
                end if;
            else
                raise exception using
                    errcode = '23514',
                    message = 'device grant polling state must advance one poll at a time';
            end if;
            if old.approved_at is not null
               and (new.approved_actor_subject_id
                        is distinct from old.approved_actor_subject_id
                    or new.approved_at is distinct from old.approved_at) then
                raise exception using
                    errcode = '23514',
                    message = 'device grant approval binding is immutable';
            end if;
            if old.status = 'pending'
               and new.status not in ('pending', 'approved', 'denied', 'expired') then
                raise exception using
                    errcode = '23514',
                    message = 'invalid pending device grant transition';
            end if;
            if old.status = 'pending'
               and new.status = 'expired'
               and (new.approved_actor_subject_id is not null
                    or new.approved_at is not null) then
                raise exception using
                    errcode = '23514',
                    message = 'pending device grants cannot expire as approved';
            end if;
            if old.status = 'approved'
               and new.status not in ('approved', 'consumed', 'expired') then
                raise exception using
                    errcode = '23514',
                    message = 'invalid approved device grant transition';
            end if;
            return new;
        """,
    }
    quoted_runtime = _quote_identifier(runtime_role)
    for function_name, body in function_definitions.items():
        connection.exec_driver_sql(
            f"""
            create function public.{function_name}()
            returns trigger
            language plpgsql
            security invoker
            set search_path = pg_catalog, public
            as $function$
            begin
                {body}
            end;
            $function$
            """
        )
        connection.exec_driver_sql(
            f"revoke all privileges on function public.{function_name}() "
            f"from public, {quoted_runtime}"
        )

    triggers = (
        (
            "trg_assets_guard_identity",
            "assets",
            "before update",
            "v2_guard_asset_identity",
        ),
        (
            "trg_books_guard_identity",
            "books",
            "before update",
            "v2_guard_book_identity",
        ),
        (
            "trg_users_guard_principal",
            "users",
            "before update",
            "v2_guard_user_principal",
        ),
        (
            "trg_book_members_guard_binding",
            "book_members",
            "before update",
            "v2_guard_book_member_binding",
        ),
        (
            "trg_auth_identities_guard_principal",
            "auth_identities",
            "before insert or update",
            "v2_guard_auth_identity_principal",
        ),
        (
            "trg_password_accounts_guard_principal",
            "password_accounts",
            "before insert or update",
            "v2_guard_password_account_principal",
        ),
        (
            "trg_accounts_guard_identity",
            "accounts",
            "before update",
            "v2_guard_account_identity",
        ),
        (
            "trg_categories_guard_identity",
            "categories",
            "before update",
            "v2_guard_category_identity",
        ),
        (
            "trg_category_versions_append_only",
            "category_versions",
            "before update or delete",
            "v2_reject_category_version_mutation",
        ),
        (
            "trg_description_sidecars_guard_identity",
            "protected_description_sidecars",
            "before update",
            "v2_guard_description_sidecar_identity",
        ),
        (
            "trg_credentials_guard_lifecycle",
            "credentials",
            "before update",
            "v2_guard_credential_lifecycle",
        ),
        (
            "trg_oauth_authorization_grants_guard_lifecycle",
            "oauth_authorization_grants",
            "before update",
            "v2_guard_authorization_grant_lifecycle",
        ),
        (
            "trg_browser_sessions_guard_lifecycle",
            "browser_sessions",
            "before insert or update",
            "v2_guard_browser_session_lifecycle",
        ),
        (
            "trg_oauth_device_grants_guard_transition",
            "oauth_device_grants",
            "before update",
            "v2_guard_device_grant_transition",
        ),
    )
    for trigger_name, table_name, timing, function_name in triggers:
        connection.exec_driver_sql(
            f"create trigger {trigger_name} {timing} on public.{table_name} "
            f"for each row execute function public.{function_name}()"
        )


def _apply_runtime_acl(runtime_role: str) -> None:
    connection = op.get_bind()
    quoted_runtime = _quote_identifier(runtime_role)
    for table_name in (*_MUTABLE_TABLES, *_APPEND_ONLY_TABLES):
        connection.exec_driver_sql(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {quoted_runtime}"
        )
    for table_name in _MUTABLE_TABLES:
        connection.exec_driver_sql(
            f"grant select, insert, update on table public.{table_name} "
            f"to {quoted_runtime}"
        )
    for table_name in _APPEND_ONLY_TABLES:
        connection.exec_driver_sql(
            f"grant select, insert on table public.{table_name} to {quoted_runtime}"
        )


def upgrade() -> None:
    runtime_role = _runtime_role()
    _create_catalog_tables()
    _create_auth_tables()
    _create_immutability_triggers(runtime_role)
    _apply_runtime_acl(runtime_role)


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 catalog migration is irreversible")
