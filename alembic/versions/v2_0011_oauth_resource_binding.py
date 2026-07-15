"""Bind OAuth credentials to client, resource, and rotating refresh family."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "v2_0011_oauth_resource_binding"
down_revision = "v2_0010_credit_card_transactions"
branch_labels = None
depends_on = None


_OAUTH_KINDS = "'pkce', 'device', 'oauth_refresh', 'refresh_token'"


def upgrade() -> None:
    # Tokens issued by the pre-binding implementation cannot be assigned a
    # trustworthy audience after the fact. Force a one-time reauthorization.
    op.execute("delete from public.credentials where auth_kind in ('pkce', 'device')")

    op.add_column(
        "oauth_authorization_grants",
        sa.Column("registered_redirect_uri", sa.String(length=512), nullable=True),
    )
    op.execute(
        "update public.oauth_authorization_grants "
        "set registered_redirect_uri = redirect_uri"
    )
    op.alter_column(
        "oauth_authorization_grants",
        "registered_redirect_uri",
        nullable=False,
    )
    op.drop_constraint(
        "fk_oauth_authorization_grants_registered_redirect",
        "oauth_authorization_grants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_oauth_authorization_grants_registered_redirect",
        "oauth_authorization_grants",
        "oauth_client_redirect_uris",
        ["client_id", "registered_redirect_uri"],
        ["client_id", "redirect_uri"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )

    op.add_column(
        "credentials",
        sa.Column("oauth_client_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "credentials",
        sa.Column("resource", sa.Text(), nullable=True),
    )
    op.add_column(
        "credentials",
        sa.Column("refresh_family_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_credentials_refresh_family_id",
        "credentials",
        ["refresh_family_id"],
    )
    op.create_foreign_key(
        "fk_credentials_oauth_client_id_oauth_clients",
        "credentials",
        "oauth_clients",
        ["oauth_client_id"],
        ["client_id"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_credentials_auth_kind_valid"),
        "credentials",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_credentials_auth_kind_valid"),
        "credentials",
        "auth_kind in ('api_key', 'pkce', 'device', 'oauth_refresh', "
        "'refresh_token', 'browser_session')",
    )
    op.create_check_constraint(
        op.f("ck_credentials_oauth_binding_shape"),
        "credentials",
        f"(auth_kind in ({_OAUTH_KINDS}) and oauth_client_id is not null "
        "and resource is not null and refresh_family_id is not null) or "
        f"(auth_kind not in ({_OAUTH_KINDS}) and oauth_client_id is null "
        "and resource is null and refresh_family_id is null)",
    )

    op.execute(
        """
        create or replace function public.v2_guard_credential_lifecycle()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            if new.credential_id is distinct from old.credential_id
               or new.token_hash is distinct from old.token_hash
               or new.jti is distinct from old.jti
               or new.actor_subject_id is distinct from old.actor_subject_id
               or new.actor_type is distinct from old.actor_type
               or new.auth_kind is distinct from old.auth_kind
               or new.book_id is distinct from old.book_id
               or new.oauth_client_id is distinct from old.oauth_client_id
               or new.resource is distinct from old.resource
               or new.refresh_family_id is distinct from old.refresh_family_id
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
        end;
        $function$
        """
    )
    op.execute(
        """
        create or replace function public.v2_guard_authorization_grant_lifecycle()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            if new.code_hash is distinct from old.code_hash
               or new.client_id is distinct from old.client_id
               or new.redirect_uri is distinct from old.redirect_uri
               or new.registered_redirect_uri is distinct from old.registered_redirect_uri
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
            if old.used_at is not null and new.used_at is distinct from old.used_at then
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
        end;
        $function$
        """
    )


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 OAuth binding migration is irreversible")
