from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac, sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


AUTH_TABLES = (
    "auth_identities",
    "book_members",
    "browser_sessions",
    "credentials",
    "oauth_authorization_grants",
    "oauth_client_redirect_uris",
    "oauth_clients",
    "oauth_device_grants",
    "password_accounts",
    "users",
)

_PASSWORD = "correct horse battery staple"
_PASSWORD_SALT = "track-anywhere-test-salt"
CANONICAL_PASSWORD_HASH = (
    f"pbkdf2_sha256$390000${_PASSWORD_SALT}$"
    f"{pbkdf2_hmac('sha256', _PASSWORD.encode(), _PASSWORD_SALT.encode(), 390_000).hex()}"
)
ROTATED_PASSWORD_HASH = (
    f"pbkdf2_sha256$390000${_PASSWORD_SALT}$"
    f"{pbkdf2_hmac('sha256', f'{_PASSWORD}-rotated'.encode(), _PASSWORD_SALT.encode(), 390_000).hex()}"
)


def _execute(engine: Engine, statement: str, parameters: dict[str, object]) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), parameters)


def _rejects_integrity(
    engine: Engine, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        _execute(engine, statement, parameters)


def _insert_user(engine: Engine, user_id: str, *, subject_type: str = "human") -> None:
    _execute(
        engine,
        """
        insert into users (user_id, subject_type, current_display_name, status)
        values (:user_id, :subject_type, 'Test user', 'active')
        """,
        {"user_id": user_id, "subject_type": subject_type},
    )


def _insert_book(engine: Engine, book_id: UUID) -> None:
    _execute(
        engine,
        """
        insert into assets (
            asset_code, kind, ledger_scale, input_scale, display_scale,
            current_name, status
        ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
        on conflict (asset_code) do nothing
        """,
        {},
    )
    _execute(
        engine,
        """
        insert into books (book_id, current_name, base_asset_code, write_state)
        values (:book_id, 'Auth book', 'USD', 'active')
        """,
        {"book_id": book_id},
    )


def _times() -> tuple[datetime, datetime]:
    issued_at = datetime.now(UTC)
    return issued_at, issued_at + timedelta(hours=1)


def _insert_credential(
    engine: Engine,
    *,
    token_hash: bytes,
    actor_subject_id: str,
    actor_type: str = "human",
    auth_kind: str = "api_key",
    book_id: UUID | None = None,
    issued_at: datetime,
    expires_at: datetime,
) -> UUID:
    credential_id = uuid4()
    _execute(
        engine,
        """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at,
            revoked_at, last_used_at
        ) values (
            :credential_id, :token_hash, :jti, :actor_subject_id, :actor_type,
            :auth_kind, :book_id, '["book:read"]'::jsonb,
            :issued_at, :expires_at, null, null
        )
        """,
        {
            "credential_id": credential_id,
            "token_hash": token_hash,
            "jti": uuid4(),
            "actor_subject_id": actor_subject_id,
            "actor_type": actor_type,
            "auth_kind": auth_kind,
            "book_id": book_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    return credential_id


def _insert_oauth_client(
    engine: Engine,
    *,
    client_id: str,
    redirect_uri: str | None = None,
) -> None:
    _execute(
        engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values (
            :client_id, 'Test client', 'public', null,
            '["book:read"]'::jsonb, 'active'
        )
        """,
        {"client_id": client_id},
    )
    if redirect_uri is not None:
        _execute(
            engine,
            """
            insert into oauth_client_redirect_uris (
                client_id, redirect_uri, status
            ) values (:client_id, :redirect_uri, 'active')
            """,
            {"client_id": client_id, "redirect_uri": redirect_uri},
        )


def _insert_authorization_grant(
    engine: Engine,
    *,
    code_hash: bytes,
    client_id: str,
    redirect_uri: str,
    actor_subject_id: str,
    created_at: datetime,
    expires_at: datetime,
    resource: str = "https://ledger.example",
) -> None:
    _execute(
        engine,
        """
        insert into oauth_authorization_grants (
            code_hash, client_id, redirect_uri, actor_subject_id, scopes,
            code_challenge, challenge_method, resource, created_at, expires_at,
            used_at, revoked_at
        ) values (
            :code_hash, :client_id, :redirect_uri, :actor_subject_id,
            '["book:read"]'::jsonb, :code_challenge, 'S256', :resource,
            :created_at, :expires_at, null, null
        )
        """,
        {
            "code_hash": code_hash,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "actor_subject_id": actor_subject_id,
            "code_challenge": "A" * 43,
            "resource": resource,
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )


def _insert_device_grant(
    engine: Engine,
    *,
    device_hash: bytes,
    user_hash: bytes,
    client_id: str,
    scopes: str,
    created_at: datetime,
    expires_at: datetime,
    interval_seconds: int = 5,
) -> None:
    _execute(
        engine,
        """
        insert into oauth_device_grants (
            device_code_hash, user_code_hash, client_id, scopes, resource,
            status, created_at, expires_at, interval_seconds, last_poll_at,
            poll_count, approved_actor_subject_id, approved_at, consumed_at
        ) values (
            :device_hash, :user_hash, :client_id, cast(:scopes as jsonb),
            'https://ledger.example', 'pending', :created_at, :expires_at,
            :interval_seconds, null, 0, null, null, null
        )
        """,
        {
            "device_hash": device_hash,
            "user_hash": user_hash,
            "client_id": client_id,
            "scopes": scopes,
            "created_at": created_at,
            "expires_at": expires_at,
            "interval_seconds": interval_seconds,
        },
    )


def _insert_browser_session(
    engine: Engine,
    *,
    session_hash: bytes,
    csrf_token_hash: bytes,
    credential_hash: bytes,
    user_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    _execute(
        engine,
        """
        insert into browser_sessions (
            session_hash, csrf_token_hash, credential_hash, user_id,
            issued_at, expires_at, revoked_at, last_seen_at
        ) values (
            :session_hash, :csrf_token_hash, :credential_hash, :user_id,
            :issued_at, :expires_at, null, null
        )
        """,
        {
            "session_hash": session_hash,
            "csrf_token_hash": csrf_token_hash,
            "credential_hash": credential_hash,
            "user_id": user_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )


def test_auth_tables_model_metadata_and_secret_column_inventory_are_exact(
    pg_engine,
) -> None:
    from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

    load_v2_models()
    assert set(AUTH_TABLES).issubset(V2Base.metadata.tables)

    with pg_engine.connect() as connection:
        relations = {
            name: connection.execute(
                text("select to_regclass(:relation)"),
                {"relation": f"public.{name}"},
            ).scalar_one()
            for name in AUTH_TABLES
        }
        critical_shapes = {
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    select table_name, column_name, data_type,
                           character_maximum_length
                      from information_schema.columns
                     where table_schema = 'public'
                       and (table_name, column_name) in (
                           ('users', 'user_id'),
                           ('auth_identities', 'subject'),
                           ('credentials', 'actor_subject_id'),
                           ('credentials', 'token_hash'),
                           ('oauth_authorization_grants', 'code_hash'),
                           ('oauth_device_grants', 'device_code_hash'),
                           ('oauth_device_grants', 'user_code_hash'),
                           ('browser_sessions', 'session_hash'),
                           ('browser_sessions', 'csrf_token_hash'),
                           ('browser_sessions', 'credential_hash')
                       )
                    """
                )
            )
        }
        columns = {
            (row.table_name, row.column_name)
            for row in connection.execute(
                text(
                    """
                    select table_name, column_name
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name = any(:tables)
                    """
                ),
                {"tables": list(AUTH_TABLES)},
            )
        }

    assert all(relations.values())
    assert critical_shapes == {
        ("auth_identities", "subject", "character varying", 128),
        ("browser_sessions", "credential_hash", "bytea", None),
        ("browser_sessions", "csrf_token_hash", "bytea", None),
        ("browser_sessions", "session_hash", "bytea", None),
        ("credentials", "actor_subject_id", "character varying", 128),
        ("credentials", "token_hash", "bytea", None),
        ("oauth_authorization_grants", "code_hash", "bytea", None),
        ("oauth_device_grants", "device_code_hash", "bytea", None),
        ("oauth_device_grants", "user_code_hash", "bytea", None),
        ("users", "user_id", "character varying", 128),
    }
    forbidden = {
        ("credentials", "token"),
        ("credentials", "api_key"),
        ("oauth_clients", "client_secret"),
        ("oauth_authorization_grants", "code"),
        ("oauth_authorization_grants", "code_verifier"),
        ("oauth_device_grants", "device_code"),
        ("oauth_device_grants", "user_code"),
        ("browser_sessions", "session_id"),
        ("browser_sessions", "csrf_token"),
        ("password_accounts", "password"),
        ("password_accounts", "role"),
    }
    assert columns.isdisjoint(forbidden)


def test_membership_identity_and_password_shapes_are_database_enforced(
    pg_engine,
) -> None:
    book_id = uuid4()
    user_id = "user:alice"
    _insert_book(pg_engine, book_id)
    _insert_user(pg_engine, user_id)
    _execute(
        pg_engine,
        """
        insert into book_members (
            book_id, user_id, role, status, scopes, revoked_at
        ) values (
            :book_id, :user_id, 'owner', 'active',
            cast(:scopes as jsonb), null
        )
        """,
        {"book_id": book_id, "user_id": user_id, "scopes": '["book:read"]'},
    )
    _execute(
        pg_engine,
        """
        insert into auth_identities (
            identity_id, provider, subject, user_id, email_verified, status
        ) values (
            :identity_id, 'oidc', 'provider-subject', :user_id, false, 'active'
        )
        """,
        {"identity_id": uuid4(), "user_id": user_id},
    )
    _execute(
        pg_engine,
        """
        insert into password_accounts (
            user_id, normalized_email, password_hash, status
        ) values (
            :user_id, 'alice@example.test', :password_hash, 'active'
        )
        """,
        {
            "user_id": user_id,
            "password_hash": CANONICAL_PASSWORD_HASH,
        },
    )

    for statement, parameters in (
        (
            """
            insert into book_members (
                book_id, user_id, role, status, scopes, revoked_at
            ) values (
                :book_id, :user_id, 'superuser', 'active', '[]'::jsonb, null
            )
            """,
            {"book_id": book_id, "user_id": user_id},
        ),
        (
            """
            update book_members set status = 'revoked', revoked_at = null
             where book_id = :book_id and user_id = :user_id
            """,
            {"book_id": book_id, "user_id": user_id},
        ),
        (
            """
            update book_members set scopes = '{}'::jsonb
             where book_id = :book_id and user_id = :user_id
            """,
            {"book_id": book_id, "user_id": user_id},
        ),
        (
            """
            insert into auth_identities (
                identity_id, provider, subject, user_id, email_verified, status
            ) values (
                :identity_id, 'oidc', 'provider-subject', :user_id, false, 'active'
            )
            """,
            {"identity_id": uuid4(), "user_id": user_id},
        ),
        (
            """
            insert into password_accounts (
                user_id, normalized_email, password_hash, status
            ) values (:user_id, 'blank@example.test', ' ', 'active')
            """,
            {"user_id": "missing-user"},
        ),
    ):
        _rejects_integrity(pg_engine, statement, parameters)

    _execute(
        pg_engine,
        """
        update book_members
           set role = 'auditor', status = 'revoked', revoked_at = :revoked_at
         where book_id = :book_id and user_id = :user_id
        """,
        {
            "book_id": book_id,
            "user_id": user_id,
            "revoked_at": datetime.now(UTC),
        },
    )


def test_book_member_binding_is_immutable_but_membership_state_is_mutable(
    pg_engine,
) -> None:
    book_id = uuid4()
    other_book_id = uuid4()
    _insert_book(pg_engine, book_id)
    _insert_book(pg_engine, other_book_id)
    _insert_user(pg_engine, "user:member")
    _insert_user(pg_engine, "user:member-other")
    _execute(
        pg_engine,
        """
        insert into book_members (
            book_id, user_id, role, status, scopes, revoked_at
        ) values (
            :book_id, 'user:member', 'viewer', 'active',
            '["book:read"]'::jsonb, null
        )
        """,
        {"book_id": book_id},
    )
    for assignment, parameters in (
        ("book_id = :value", {"value": other_book_id}),
        ("user_id = 'user:member-other'", {}),
        ("created_at = created_at + interval '1 second'", {}),
    ):
        _rejects_integrity(
            pg_engine,
            f"update book_members set {assignment} "
            "where book_id = :book_id and user_id = 'user:member'",
            {**parameters, "book_id": book_id},
        )

    _execute(
        pg_engine,
        """
        update book_members
           set role = 'auditor', scopes = '["book:read", "audit:read"]'::jsonb,
               status = 'revoked', revoked_at = :revoked_at
         where book_id = :book_id and user_id = 'user:member'
        """,
        {
            "book_id": book_id,
            "revoked_at": datetime.now(UTC),
        },
    )


def test_user_principal_identity_is_immutable_but_profile_is_mutable(pg_engine) -> None:
    user_id = "user:principal"
    _insert_user(pg_engine, user_id)
    for assignment in (
        "user_id = 'user:renamed'",
        "subject_type = 'machine'",
        "created_at = created_at + interval '1 second'",
    ):
        _rejects_integrity(
            pg_engine,
            f"update users set {assignment} where user_id = :user_id",
            {"user_id": user_id},
        )

    _execute(
        pg_engine,
        """
        update users
           set current_display_name = 'Updated principal',
               status = 'disabled', updated_at = :updated_at
         where user_id = :user_id
        """,
        {"updated_at": datetime.now(UTC), "user_id": user_id},
    )


def test_auth_identity_principal_is_human_and_immutable(pg_engine) -> None:
    _insert_user(pg_engine, "user:identity-owner")
    _insert_user(pg_engine, "user:identity-other")
    _insert_user(pg_engine, "machine:identity", subject_type="machine")
    _rejects_integrity(
        pg_engine,
        """
        insert into auth_identities (
            identity_id, provider, subject, user_id, email_verified, status
        ) values (
            :identity_id, 'oidc', 'machine-subject',
            'machine:identity', false, 'active'
        )
        """,
        {"identity_id": uuid4()},
    )

    identity_id = uuid4()
    _execute(
        pg_engine,
        """
        insert into auth_identities (
            identity_id, provider, subject, user_id, email_verified, status
        ) values (
            :identity_id, 'oidc', 'human-subject',
            'user:identity-owner', false, 'active'
        )
        """,
        {"identity_id": identity_id},
    )
    for assignment, parameters in (
        ("identity_id = :value", {"value": uuid4()}),
        ("provider = 'saml'", {}),
        ("subject = 'other-subject'", {}),
        ("user_id = 'user:identity-other'", {}),
        ("created_at = created_at + interval '1 second'", {}),
    ):
        _rejects_integrity(
            pg_engine,
            f"update auth_identities set {assignment} where identity_id = :identity_id",
            {**parameters, "identity_id": identity_id},
        )

    _execute(
        pg_engine,
        """
        update auth_identities
           set email = 'updated@example.test', email_verified = true,
               display_name = 'Updated identity',
               picture_url = 'https://example.test/picture.png',
               status = 'disabled', updated_at = :updated_at
         where identity_id = :identity_id
        """,
        {"updated_at": datetime.now(UTC), "identity_id": identity_id},
    )


def test_password_account_principal_is_human_and_immutable(pg_engine) -> None:
    _insert_user(pg_engine, "user:password-owner")
    _insert_user(pg_engine, "user:password-other")
    _insert_user(pg_engine, "machine:password", subject_type="machine")
    _rejects_integrity(
        pg_engine,
        """
        insert into password_accounts (
            user_id, normalized_email, password_hash, status
        ) values (
            'machine:password', 'machine@example.test',
            :password_hash, 'active'
        )
        """,
        {"password_hash": CANONICAL_PASSWORD_HASH},
    )

    _execute(
        pg_engine,
        """
        insert into password_accounts (
            user_id, normalized_email, password_hash, status
        ) values (
            'user:password-owner', 'owner@example.test',
            :password_hash, 'active'
        )
        """,
        {"password_hash": CANONICAL_PASSWORD_HASH},
    )
    for assignment in (
        "user_id = 'user:password-other'",
        "created_at = created_at + interval '1 second'",
    ):
        _rejects_integrity(
            pg_engine,
            f"update password_accounts set {assignment} "
            "where user_id = 'user:password-owner'",
            {},
        )

    _execute(
        pg_engine,
        """
        update password_accounts
           set normalized_email = 'updated-owner@example.test',
               password_hash = :password_hash, status = 'disabled',
               updated_at = :updated_at
         where user_id = 'user:password-owner'
        """,
        {
            "password_hash": ROTATED_PASSWORD_HASH,
            "updated_at": datetime.now(UTC),
        },
    )


def test_credentials_require_sha256_hashes_array_scopes_and_book_bound_machines(
    pg_engine,
) -> None:
    book_id = uuid4()
    user_id = "machine:worker"
    _insert_book(pg_engine, book_id)
    _insert_user(pg_engine, user_id, subject_type="machine")
    issued_at, expires_at = _times()
    valid = {
        "credential_id": uuid4(),
        "token_hash": b"t" * 32,
        "jti": uuid4(),
        "actor_subject_id": user_id,
        "book_id": book_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    statement = """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :token_hash, :jti, :actor_subject_id, 'machine',
            'api_key', :book_id, '["ledger:write"]'::jsonb,
            :issued_at, :expires_at, null
        )
    """
    _execute(pg_engine, statement, valid)

    for bad_length in (31, 33):
        _rejects_integrity(
            pg_engine,
            statement,
            {
                **valid,
                "credential_id": uuid4(),
                "jti": uuid4(),
                "token_hash": b"x" * bad_length,
            },
        )
    _rejects_integrity(
        pg_engine,
        statement.replace(":book_id", "null"),
        {**valid, "credential_id": uuid4(), "jti": uuid4()},
    )
    _rejects_integrity(
        pg_engine,
        statement.replace("'[\"ledger:write\"]'::jsonb", "'{}'::jsonb"),
        {**valid, "credential_id": uuid4(), "jti": uuid4()},
    )
    _rejects_integrity(
        pg_engine,
        statement,
        {
            **valid,
            "credential_id": uuid4(),
            "jti": uuid4(),
            "token_hash": b"z" * 32,
            "expires_at": issued_at,
        },
    )


def test_oauth_clients_redirects_and_authorization_grants_are_bound_and_hashed(
    pg_engine,
) -> None:
    user_id = "user:oauth"
    _insert_user(pg_engine, user_id)
    _execute(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values (
            'public-client', 'Public client', 'public', null,
            '["book:read"]'::jsonb, 'active'
        )
        """,
        {},
    )
    _execute(
        pg_engine,
        """
        insert into oauth_client_redirect_uris (client_id, redirect_uri, status)
        values ('public-client', 'https://client.example/callback', 'active')
        """,
        {},
    )
    _execute(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values (
            'confidential-client', 'Confidential client', 'confidential',
            :secret_hash, '[]'::jsonb, 'active'
        )
        """,
        {"secret_hash": b"s" * 32},
    )
    created_at, expires_at = _times()
    grant = {
        "code_hash": b"c" * 32,
        "actor_subject_id": user_id,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    grant_statement = """
        insert into oauth_authorization_grants (
            code_hash, client_id, redirect_uri, actor_subject_id, scopes,
            code_challenge, challenge_method, created_at, expires_at,
            used_at, revoked_at
        ) values (
            :code_hash, 'public-client', 'https://client.example/callback',
            :actor_subject_id, '["book:read"]'::jsonb,
            'abcdefghijklmnopqrstuvwxyzABCDEFGHJKLMNOPQRSTUVWXYZ0123456789_-',
            'S256', :created_at, :expires_at, null, null
        )
    """
    _execute(pg_engine, grant_statement, grant)

    for bad_hash in (b"x" * 31, b"x" * 33):
        _rejects_integrity(pg_engine, grant_statement, {**grant, "code_hash": bad_hash})
        _rejects_integrity(
            pg_engine,
            """
            insert into oauth_clients (
                client_id, client_name, client_type, client_secret_hash,
                scopes, status
            ) values (
                :client_id, 'Bad secret', 'confidential', :secret_hash,
                '[]'::jsonb, 'active'
            )
            """,
            {"client_id": f"bad-{len(bad_hash)}", "secret_hash": bad_hash},
        )
    _rejects_integrity(
        pg_engine,
        grant_statement.replace(
            "https://client.example/callback", "https://attacker.example/callback"
        ),
        {**grant, "code_hash": b"r" * 32},
    )
    _rejects_integrity(
        pg_engine,
        grant_statement.replace("'S256'", "'plain'"),
        {**grant, "code_hash": b"p" * 32},
    )
    _rejects_integrity(
        pg_engine,
        """
        update oauth_authorization_grants
           set used_at = :before_created
         where code_hash = :code_hash
        """,
        {"before_created": created_at - timedelta(seconds=1), "code_hash": b"c" * 32},
    )


def test_device_grants_and_browser_sessions_enforce_hash_and_lifecycle_shapes(
    pg_engine,
) -> None:
    user_id = "user:device"
    _insert_user(pg_engine, user_id)
    _execute(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values ('device-client', 'Device client', 'public', null, '[]'::jsonb, 'active')
        """,
        {},
    )
    issued_at, expires_at = _times()
    device_statement = """
        insert into oauth_device_grants (
            device_code_hash, user_code_hash, client_id, scopes, status,
            created_at, expires_at, interval_seconds, poll_count,
            approved_actor_subject_id, approved_at, consumed_at
        ) values (
            :device_hash, :user_hash, 'device-client', '[]'::jsonb, 'pending',
            :issued_at, :expires_at, 5, 0, null, null, null
        )
    """
    _execute(
        pg_engine,
        device_statement,
        {
            "device_hash": b"d" * 32,
            "user_hash": b"u" * 32,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    for bad_length in (31, 33):
        _rejects_integrity(
            pg_engine,
            device_statement,
            {
                "device_hash": b"d" * bad_length,
                "user_hash": b"u" * 32,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
        )
        _rejects_integrity(
            pg_engine,
            device_statement,
            {
                "device_hash": bytes([bad_length]) * 32,
                "user_hash": b"u" * bad_length,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
        )
    _rejects_integrity(
        pg_engine,
        device_statement.replace("null, null, null", ":user_id, :issued_at, null"),
        {
            "device_hash": b"a" * 32,
            "user_hash": b"b" * 32,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "user_id": user_id,
        },
    )

    # Browser sessions reference a credential only by its fixed hash.
    _execute(
        pg_engine,
        """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :credential_hash, :jti, :user_id, 'human',
            'browser_session', null, '[]'::jsonb, :issued_at, :expires_at, null
        )
        """,
        {
            "credential_id": uuid4(),
            "credential_hash": b"k" * 32,
            "jti": uuid4(),
            "user_id": user_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    session_statement = """
        insert into browser_sessions (
            session_hash, csrf_token_hash, credential_hash, user_id,
            issued_at, expires_at, revoked_at
        ) values (
            :session_hash, :csrf_hash, :credential_hash, :user_id,
            :issued_at, :expires_at, null
        )
    """
    session = {
        "session_hash": b"e" * 32,
        "csrf_hash": b"f" * 32,
        "credential_hash": b"k" * 32,
        "user_id": user_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    _execute(pg_engine, session_statement, session)
    for column in ("session_hash", "csrf_hash", "credential_hash"):
        for bad_length in (31, 33):
            _rejects_integrity(
                pg_engine,
                session_statement,
                {
                    **session,
                    "session_hash": uuid4().bytes + uuid4().bytes,
                    column: b"x" * bad_length,
                },
            )
    _rejects_integrity(
        pg_engine,
        session_statement,
        {
            **session,
            "session_hash": b"q" * 32,
            "expires_at": issued_at,
        },
    )


def test_credential_actor_type_must_match_the_subject_type(pg_engine) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id)
    _insert_user(pg_engine, "machine:bound", subject_type="machine")
    _insert_user(pg_engine, "user:bound")
    issued_at, expires_at = _times()
    statement = """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :token_hash, :jti, :actor_subject_id, :actor_type,
            'api_key', :book_id, '[]'::jsonb, :issued_at, :expires_at, null
        )
    """
    for actor_subject_id, actor_type, scoped_book in (
        ("machine:bound", "human", None),
        ("user:bound", "machine", book_id),
    ):
        _rejects_integrity(
            pg_engine,
            statement,
            {
                "credential_id": uuid4(),
                "token_hash": uuid4().bytes + uuid4().bytes,
                "jti": uuid4(),
                "actor_subject_id": actor_subject_id,
                "actor_type": actor_type,
                "book_id": scoped_book,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
        )


def test_browser_session_subject_is_bound_to_the_credential_subject(pg_engine) -> None:
    _insert_user(pg_engine, "user:credential-owner")
    _insert_user(pg_engine, "user:session-impostor")
    issued_at, expires_at = _times()
    credential_hash = b"b" * 32
    _execute(
        pg_engine,
        """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :credential_hash, :jti, 'user:credential-owner',
            'human', 'browser_session', null, '[]'::jsonb,
            :issued_at, :expires_at, null
        )
        """,
        {
            "credential_id": uuid4(),
            "credential_hash": credential_hash,
            "jti": uuid4(),
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    _rejects_integrity(
        pg_engine,
        """
        insert into browser_sessions (
            session_hash, csrf_token_hash, credential_hash, user_id,
            issued_at, expires_at, revoked_at
        ) values (
            :session_hash, :csrf_hash, :credential_hash,
            'user:session-impostor', :issued_at, :expires_at, null
        )
        """,
        {
            "session_hash": b"s" * 32,
            "csrf_hash": b"c" * 32,
            "credential_hash": credential_hash,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )


def test_scope_arrays_accept_only_nonempty_strings(pg_engine) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id)
    _insert_user(pg_engine, "machine:scope", subject_type="machine")
    issued_at, expires_at = _times()
    _rejects_integrity(
        pg_engine,
        """
        insert into book_members (
            book_id, user_id, role, status, scopes, revoked_at
        ) values (
            :book_id, 'machine:scope', 'editor', 'active', '[{}]'::jsonb, null
        )
        """,
        {"book_id": book_id},
    )
    _rejects_integrity(
        pg_engine,
        """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :token_hash, :jti, 'machine:scope', 'machine',
            'api_key', :book_id, '[1]'::jsonb, :issued_at, :expires_at, null
        )
        """,
        {
            "credential_id": uuid4(),
            "token_hash": b"h" * 32,
            "jti": uuid4(),
            "book_id": book_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    _rejects_integrity(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values ('bad-scopes', 'Bad scopes', 'public', null, '[null]'::jsonb, 'active')
        """,
        {},
    )


def test_oauth_grants_retain_the_bound_resource(pg_engine) -> None:
    _insert_user(pg_engine, "user:resource")
    _execute(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values ('resource-client', 'Resource client', 'public', null, '[]'::jsonb, 'active')
        """,
        {},
    )
    _execute(
        pg_engine,
        """
        insert into oauth_client_redirect_uris (client_id, redirect_uri, status)
        values ('resource-client', 'https://client.example/resource', 'active')
        """,
        {},
    )
    created_at, expires_at = _times()
    code_hash = b"r" * 32
    _execute(
        pg_engine,
        """
        insert into oauth_authorization_grants (
            code_hash, client_id, redirect_uri, actor_subject_id, scopes,
            code_challenge, challenge_method, resource, created_at, expires_at,
            used_at, revoked_at
        ) values (
            :code_hash, 'resource-client', 'https://client.example/resource',
            'user:resource', '[]'::jsonb, :challenge, 'S256',
            'https://ledger.example', :created_at, :expires_at, null, null
        )
        """,
        {
            "code_hash": code_hash,
            "challenge": "A" * 43,
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )
    with pg_engine.connect() as connection:
        resource = connection.execute(
            text(
                "select resource from oauth_authorization_grants where code_hash = :code_hash"
            ),
            {"code_hash": code_hash},
        ).scalar_one()
    assert resource == "https://ledger.example"


def test_device_grant_terminal_states_cannot_be_reopened(pg_engine) -> None:
    _insert_user(pg_engine, "user:approver")
    _execute(
        pg_engine,
        """
        insert into oauth_clients (
            client_id, client_name, client_type, client_secret_hash,
            scopes, status
        ) values ('terminal-client', 'Terminal client', 'public', null, '[]'::jsonb, 'active')
        """,
        {},
    )
    created_at, expires_at = _times()
    _rejects_integrity(
        pg_engine,
        """
        insert into oauth_device_grants (
            device_code_hash, user_code_hash, client_id, scopes, status,
            created_at, expires_at, interval_seconds, poll_count,
            approved_actor_subject_id, approved_at, consumed_at
        ) values (
            :device_hash, :user_hash, 'terminal-client', '[]'::jsonb, 'denied',
            :created_at, :expires_at, 5, 0, 'user:approver', :created_at, null
        )
        """,
        {
            "device_hash": b"d" * 32,
            "user_hash": b"u" * 32,
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )
    terminal_hash = b"t" * 32
    _execute(
        pg_engine,
        """
        insert into oauth_device_grants (
            device_code_hash, user_code_hash, client_id, scopes, status,
            created_at, expires_at, interval_seconds, poll_count,
            approved_actor_subject_id, approved_at, consumed_at
        ) values (
            :device_hash, :user_hash, 'terminal-client', '[]'::jsonb, 'denied',
            :created_at, :expires_at, 5, 0, null, null, null
        )
        """,
        {
            "device_hash": terminal_hash,
            "user_hash": b"v" * 32,
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )
    with pytest.raises(DBAPIError):
        _execute(
            pg_engine,
            """
            update oauth_device_grants set status = 'pending'
             where device_code_hash = :device_hash
            """,
            {"device_hash": terminal_hash},
        )


def test_password_hash_must_match_the_current_pbkdf2_verifier_contract(
    pg_engine,
) -> None:
    from track_anywhere.password_auth import _verify_password

    assert _verify_password(_PASSWORD, CANONICAL_PASSWORD_HASH)
    _insert_user(pg_engine, "user:password-valid")
    _execute(
        pg_engine,
        """
        insert into password_accounts (
            user_id, normalized_email, password_hash, status
        ) values (
            'user:password-valid', 'valid@example.test', :password_hash, 'active'
        )
        """,
        {"password_hash": CANONICAL_PASSWORD_HASH},
    )

    digest = CANONICAL_PASSWORD_HASH.rsplit("$", 1)[1]
    malformed_hashes = (
        "hunter2",
        "$argon2id$v=19$m=65536,t=3,p=4$encoded",
        CANONICAL_PASSWORD_HASH.replace("$390000$", "$390001$"),
        f"pbkdf2_sha256$390000$${digest}",
        f"pbkdf2_sha256$390000$salt$with-dollar${digest}",
        f"pbkdf2_sha256$390000$test-salt${digest[:-1]}",
        f"pbkdf2_sha256$390000$test-salt${digest.upper()}",
        f"pbkdf2_sha256$390000${'x' * 23}${digest}",
        f"pbkdf2_sha256$390000${'x' * 25}${digest}",
        f"pbkdf2_sha256$390000$contains a space here!!${digest}",
        f"pbkdf2_sha256$390000${'界' * 24}${digest}",
    )
    for index, password_hash in enumerate(malformed_hashes):
        user_id = f"user:password-invalid-{index}"
        _insert_user(pg_engine, user_id)
        _rejects_integrity(
            pg_engine,
            """
            insert into password_accounts (
                user_id, normalized_email, password_hash, status
            ) values (
                :user_id, :email, :password_hash, 'active'
            )
            """,
            {
                "user_id": user_id,
                "email": f"invalid-{index}@example.test",
                "password_hash": password_hash,
            },
        )


def test_credentials_and_browser_sessions_have_immutable_bound_lifecycles(
    pg_engine,
) -> None:
    issued_at, expires_at = _times()
    book_id = uuid4()
    other_book_id = uuid4()
    _insert_book(pg_engine, book_id)
    _insert_book(pg_engine, other_book_id)
    _insert_user(pg_engine, "machine:lane", subject_type="machine")
    _insert_user(pg_engine, "user:credential")
    _insert_user(pg_engine, "user:other")

    for index, auth_kind in enumerate(("pkce", "device", "browser_session")):
        _rejects_integrity(
            pg_engine,
            """
            insert into credentials (
                credential_id, token_hash, jti, actor_subject_id, actor_type,
                auth_kind, book_id, scopes, issued_at, expires_at
            ) values (
                :credential_id, :token_hash, :jti, 'machine:lane', 'machine',
                :auth_kind, :book_id, '[]'::jsonb, :issued_at, :expires_at
            )
            """,
            {
                "credential_id": uuid4(),
                "token_hash": bytes([index + 1]) * 32,
                "jti": uuid4(),
                "auth_kind": auth_kind,
                "book_id": book_id,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
        )

    credential_hash = b"i" * 32
    credential_id = _insert_credential(
        pg_engine,
        token_hash=credential_hash,
        actor_subject_id="user:credential",
        book_id=book_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    frozen_updates = (
        ("credential_id = :value", {"value": uuid4()}),
        ("token_hash = :value", {"value": b"j" * 32}),
        ("jti = :value", {"value": uuid4()}),
        ("actor_subject_id = 'user:other'", {}),
        ("auth_kind = 'pkce'", {}),
        ("book_id = :value", {"value": other_book_id}),
        ("scopes = '[\"ledger:write\"]'::jsonb", {}),
        ("issued_at = :value", {"value": issued_at + timedelta(seconds=1)}),
        ("expires_at = :value", {"value": expires_at + timedelta(hours=1)}),
    )
    for assignment, parameters in frozen_updates:
        _rejects_integrity(
            pg_engine,
            f"update credentials set {assignment} where credential_id = :credential_id",
            {**parameters, "credential_id": credential_id},
        )

    revoked_at = issued_at + timedelta(minutes=5)
    _execute(
        pg_engine,
        "update credentials set revoked_at = :value where credential_id = :id",
        {"value": revoked_at, "id": credential_id},
    )
    for replacement in (None, revoked_at + timedelta(seconds=1)):
        _rejects_integrity(
            pg_engine,
            "update credentials set revoked_at = :value where credential_id = :id",
            {"value": replacement, "id": credential_id},
        )

    first_use = issued_at + timedelta(minutes=6)
    second_use = issued_at + timedelta(minutes=7)
    for last_used_at in (first_use, second_use):
        _execute(
            pg_engine,
            "update credentials set last_used_at = :value where credential_id = :id",
            {"value": last_used_at, "id": credential_id},
        )
    for replacement in (None, first_use):
        _rejects_integrity(
            pg_engine,
            "update credentials set last_used_at = :value where credential_id = :id",
            {"value": replacement, "id": credential_id},
        )

    # A human API key is not a browser-session credential.
    with pytest.raises(IntegrityError):
        _insert_browser_session(
            pg_engine,
            session_hash=b"a" * 32,
            csrf_token_hash=b"b" * 32,
            credential_hash=credential_hash,
            user_id="user:credential",
            issued_at=issued_at,
            expires_at=expires_at,
        )

    browser_credential_hash = b"c" * 32
    _insert_credential(
        pg_engine,
        token_hash=browser_credential_hash,
        actor_subject_id="user:credential",
        auth_kind="browser_session",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    with pytest.raises(IntegrityError):
        _insert_browser_session(
            pg_engine,
            session_hash=b"d" * 32,
            csrf_token_hash=b"e" * 32,
            credential_hash=browser_credential_hash,
            user_id="user:credential",
            issued_at=issued_at,
            expires_at=expires_at + timedelta(seconds=1),
        )

    session_hash = b"f" * 32
    _insert_browser_session(
        pg_engine,
        session_hash=session_hash,
        csrf_token_hash=b"g" * 32,
        credential_hash=browser_credential_hash,
        user_id="user:credential",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    for assignment, parameters in (
        ("session_hash = :value", {"value": b"h" * 32}),
        ("csrf_token_hash = :value", {"value": b"i" * 32}),
        ("issued_at = :value", {"value": issued_at + timedelta(seconds=1)}),
        ("expires_at = :value", {"value": expires_at - timedelta(seconds=1)}),
    ):
        _rejects_integrity(
            pg_engine,
            f"update browser_sessions set {assignment} where session_hash = :session_hash",
            {**parameters, "session_hash": session_hash},
        )

    session_revoked_at = issued_at + timedelta(minutes=10)
    _execute(
        pg_engine,
        "update browser_sessions set revoked_at = :value where session_hash = :hash",
        {"value": session_revoked_at, "hash": session_hash},
    )
    for replacement in (None, session_revoked_at + timedelta(seconds=1)):
        _rejects_integrity(
            pg_engine,
            "update browser_sessions set revoked_at = :value where session_hash = :hash",
            {"value": replacement, "hash": session_hash},
        )
    first_seen = issued_at + timedelta(minutes=11)
    second_seen = issued_at + timedelta(minutes=12)
    for last_seen_at in (first_seen, second_seen):
        _execute(
            pg_engine,
            "update browser_sessions set last_seen_at = :value where session_hash = :hash",
            {"value": last_seen_at, "hash": session_hash},
        )
    for replacement in (None, first_seen):
        _rejects_integrity(
            pg_engine,
            "update browser_sessions set last_seen_at = :value where session_hash = :hash",
            {"value": replacement, "hash": session_hash},
        )


@pytest.mark.parametrize("invalid_binding", ("rebind", "revoked", "preissued"))
def test_browser_sessions_cannot_escape_the_live_credential_window(
    pg_engine,
    invalid_binding: str,
) -> None:
    credential_issued_at, credential_expires_at = _times()
    _insert_user(pg_engine, "user:browser-primary")
    _insert_user(pg_engine, "user:browser-other")
    primary_hash = b"1" * 32
    other_hash = b"2" * 32
    primary_id = _insert_credential(
        pg_engine,
        token_hash=primary_hash,
        actor_subject_id="user:browser-primary",
        auth_kind="browser_session",
        issued_at=credential_issued_at,
        expires_at=credential_expires_at,
    )
    _insert_credential(
        pg_engine,
        token_hash=other_hash,
        actor_subject_id="user:browser-other",
        auth_kind="browser_session",
        issued_at=credential_issued_at,
        expires_at=credential_expires_at,
    )

    session_hash = b"3" * 32
    if invalid_binding == "rebind":
        _insert_browser_session(
            pg_engine,
            session_hash=session_hash,
            csrf_token_hash=b"4" * 32,
            credential_hash=primary_hash,
            user_id="user:browser-primary",
            issued_at=credential_issued_at,
            expires_at=credential_expires_at,
        )
        _rejects_integrity(
            pg_engine,
            """
            update browser_sessions
               set credential_hash = :credential_hash, user_id = :user_id
             where session_hash = :session_hash
            """,
            {
                "credential_hash": other_hash,
                "user_id": "user:browser-other",
                "session_hash": session_hash,
            },
        )
        return

    session_issued_at = credential_issued_at - timedelta(seconds=1)
    if invalid_binding == "revoked":
        _insert_browser_session(
            pg_engine,
            session_hash=b"5" * 32,
            csrf_token_hash=b"6" * 32,
            credential_hash=primary_hash,
            user_id="user:browser-primary",
            issued_at=credential_issued_at,
            expires_at=credential_expires_at,
        )
        revoked_at = credential_issued_at + timedelta(seconds=10)
        _execute(
            pg_engine,
            "update credentials set revoked_at = :revoked_at where credential_id = :id",
            {"revoked_at": revoked_at, "id": primary_id},
        )
        session_issued_at = revoked_at + timedelta(seconds=1)

    with pytest.raises(IntegrityError):
        _insert_browser_session(
            pg_engine,
            session_hash=session_hash,
            csrf_token_hash=b"4" * 32,
            credential_hash=primary_hash,
            user_id="user:browser-primary",
            issued_at=session_issued_at,
            expires_at=session_issued_at + timedelta(minutes=5),
        )
    if invalid_binding == "revoked":
        _execute(
            pg_engine,
            "update browser_sessions set revoked_at = :revoked_at "
            "where session_hash = :session_hash",
            {
                "revoked_at": session_issued_at,
                "session_hash": b"5" * 32,
            },
        )


def test_oauth_authorization_grants_freeze_issuance_and_terminal_timestamps(
    pg_engine,
) -> None:
    created_at, expires_at = _times()
    _insert_user(pg_engine, "user:grant")
    _insert_user(pg_engine, "user:grant-other")
    _insert_oauth_client(
        pg_engine,
        client_id="grant-client",
        redirect_uri="https://client.example/grant",
    )
    _insert_oauth_client(
        pg_engine,
        client_id="grant-client-other",
        redirect_uri="https://client.example/grant-other",
    )
    code_hash = b"o" * 32
    _insert_authorization_grant(
        pg_engine,
        code_hash=code_hash,
        client_id="grant-client",
        redirect_uri="https://client.example/grant",
        actor_subject_id="user:grant",
        created_at=created_at,
        expires_at=expires_at,
    )

    for assignment, parameters in (
        ("code_hash = :value", {"value": b"p" * 32}),
        (
            "client_id = 'grant-client-other', "
            "redirect_uri = 'https://client.example/grant-other'",
            {},
        ),
        ("actor_subject_id = 'user:grant-other'", {}),
        ("scopes = '[\"ledger:write\"]'::jsonb", {}),
        ("code_challenge = :value", {"value": "B" * 43}),
        ("resource = 'https://other.example'", {}),
        ("created_at = :value", {"value": created_at - timedelta(seconds=1)}),
        ("expires_at = :value", {"value": expires_at + timedelta(seconds=1)}),
    ):
        _rejects_integrity(
            pg_engine,
            f"update oauth_authorization_grants set {assignment} "
            "where code_hash = :code_hash",
            {**parameters, "code_hash": code_hash},
        )

    used_hash = b"u" * 32
    revoked_hash = b"r" * 32
    for terminal_hash in (used_hash, revoked_hash):
        _insert_authorization_grant(
            pg_engine,
            code_hash=terminal_hash,
            client_id="grant-client",
            redirect_uri="https://client.example/grant",
            actor_subject_id="user:grant",
            created_at=created_at,
            expires_at=expires_at,
        )
    used_at = created_at + timedelta(minutes=1)
    _execute(
        pg_engine,
        "update oauth_authorization_grants set used_at = :value where code_hash = :hash",
        {"value": used_at, "hash": used_hash},
    )
    revoked_at = created_at + timedelta(minutes=2)
    _execute(
        pg_engine,
        "update oauth_authorization_grants set revoked_at = :value where code_hash = :hash",
        {"value": revoked_at, "hash": revoked_hash},
    )
    for column, terminal_hash, value in (
        ("used_at", used_hash, used_at),
        ("revoked_at", revoked_hash, revoked_at),
    ):
        for replacement in (None, value + timedelta(seconds=1)):
            _rejects_integrity(
                pg_engine,
                f"update oauth_authorization_grants set {column} = :value "
                "where code_hash = :hash",
                {"value": replacement, "hash": terminal_hash},
            )


def test_device_grants_freeze_issuance_approval_and_polling_state(pg_engine) -> None:
    created_at, expires_at = _times()
    _insert_user(pg_engine, "user:device-approver")
    _insert_user(pg_engine, "user:device-other")
    _insert_oauth_client(pg_engine, client_id="device-lifecycle")
    _insert_oauth_client(pg_engine, client_id="device-lifecycle-other")
    device_hash = b"v" * 32
    _execute(
        pg_engine,
        """
        insert into oauth_device_grants (
            device_code_hash, user_code_hash, client_id, scopes, resource,
            status, created_at, expires_at, interval_seconds, last_poll_at,
            poll_count, approved_actor_subject_id, approved_at, consumed_at
        ) values (
            :device_hash, :user_hash, 'device-lifecycle',
            '["book:read"]'::jsonb, 'https://ledger.example', 'pending',
            :created_at, :expires_at, 5, null, 0, null, null, null
        )
        """,
        {
            "device_hash": device_hash,
            "user_hash": b"w" * 32,
            "created_at": created_at,
            "expires_at": expires_at,
        },
    )
    for assignment, parameters in (
        ("device_code_hash = :value", {"value": b"x" * 32}),
        ("user_code_hash = :value", {"value": b"y" * 32}),
        ("client_id = 'device-lifecycle-other'", {}),
        ("resource = 'https://other.example'", {}),
        ("created_at = :value", {"value": created_at - timedelta(seconds=1)}),
        ("expires_at = :value", {"value": expires_at + timedelta(seconds=1)}),
    ):
        _rejects_integrity(
            pg_engine,
            f"update oauth_device_grants set {assignment} "
            "where device_code_hash = :device_hash",
            {**parameters, "device_hash": device_hash},
        )

    first_poll = created_at + timedelta(seconds=5)
    second_poll = created_at + timedelta(seconds=10)
    for poll_count, last_poll_at in ((1, first_poll), (2, second_poll)):
        _execute(
            pg_engine,
            """
            update oauth_device_grants
               set poll_count = :poll_count, last_poll_at = :last_poll_at
             where device_code_hash = :device_hash
            """,
            {
                "poll_count": poll_count,
                "last_poll_at": last_poll_at,
                "device_hash": device_hash,
            },
        )
    for poll_count, last_poll_at in ((1, second_poll), (3, first_poll), (3, None)):
        _rejects_integrity(
            pg_engine,
            """
            update oauth_device_grants
               set poll_count = :poll_count, last_poll_at = :last_poll_at
             where device_code_hash = :device_hash
            """,
            {
                "poll_count": poll_count,
                "last_poll_at": last_poll_at,
                "device_hash": device_hash,
            },
        )

    _rejects_integrity(
        pg_engine,
        """
        update oauth_device_grants
           set status = 'expired',
               approved_actor_subject_id = 'user:device-approver',
               approved_at = :approved_at
         where device_code_hash = :device_hash
        """,
        {
            "approved_at": created_at + timedelta(minutes=1),
            "device_hash": device_hash,
        },
    )

    approved_at = created_at + timedelta(minutes=1)
    _execute(
        pg_engine,
        """
        update oauth_device_grants
           set status = 'approved',
               approved_actor_subject_id = 'user:device-approver',
               approved_at = :approved_at
         where device_code_hash = :device_hash
        """,
        {"approved_at": approved_at, "device_hash": device_hash},
    )
    for assignment, parameters in (
        ("approved_actor_subject_id = 'user:device-other'", {}),
        (
            "approved_at = :approved_at",
            {"approved_at": approved_at + timedelta(seconds=1)},
        ),
    ):
        _rejects_integrity(
            pg_engine,
            f"update oauth_device_grants set {assignment} "
            "where device_code_hash = :device_hash",
            {**parameters, "device_hash": device_hash},
        )
    _rejects_integrity(
        pg_engine,
        """
        update oauth_device_grants
           set status = 'denied',
               approved_actor_subject_id = null, approved_at = null
         where device_code_hash = :device_hash
        """,
        {"device_hash": device_hash},
    )
    _execute(
        pg_engine,
        """
        update oauth_device_grants
           set status = 'consumed', consumed_at = :consumed_at
         where device_code_hash = :device_hash
        """,
        {
            "consumed_at": approved_at + timedelta(seconds=1),
            "device_hash": device_hash,
        },
    )


def test_device_grant_scopes_only_narrow_during_pending_approval(pg_engine) -> None:
    created_at, expires_at = _times()
    _insert_user(pg_engine, "user:scope-approver")
    _insert_oauth_client(pg_engine, client_id="device-scope-client")
    device_hash = b"7" * 32
    _insert_device_grant(
        pg_engine,
        device_hash=device_hash,
        user_hash=b"8" * 32,
        client_id="device-scope-client",
        scopes='["book:read", "ledger:write"]',
        created_at=created_at,
        expires_at=expires_at,
    )

    _rejects_integrity(
        pg_engine,
        """
        update oauth_device_grants
           set scopes = '["book:read"]'::jsonb
         where device_code_hash = :device_hash
        """,
        {"device_hash": device_hash},
    )
    for invalid_scopes in (
        '["book:read", "ledger:write", "book:admin"]',
        '["book:read", "book:unknown"]',
    ):
        _rejects_integrity(
            pg_engine,
            """
            update oauth_device_grants
               set status = 'approved', scopes = cast(:scopes as jsonb),
                   approved_actor_subject_id = 'user:scope-approver',
                   approved_at = :approved_at
             where device_code_hash = :device_hash
            """,
            {
                "scopes": invalid_scopes,
                "approved_at": created_at + timedelta(minutes=1),
                "device_hash": device_hash,
            },
        )

    _execute(
        pg_engine,
        """
        update oauth_device_grants
           set status = 'approved', scopes = '["book:read"]'::jsonb,
               approved_actor_subject_id = 'user:scope-approver',
               approved_at = :approved_at
         where device_code_hash = :device_hash
        """,
        {
            "approved_at": created_at + timedelta(minutes=1),
            "device_hash": device_hash,
        },
    )
    _rejects_integrity(
        pg_engine,
        """
        update oauth_device_grants set scopes = '[]'::jsonb
         where device_code_hash = :device_hash
        """,
        {"device_hash": device_hash},
    )


def test_device_grant_interval_only_stays_or_adds_five_per_poll(pg_engine) -> None:
    created_at, expires_at = _times()
    _insert_oauth_client(pg_engine, client_id="device-interval-client")
    device_hash = b"9" * 32
    _insert_device_grant(
        pg_engine,
        device_hash=device_hash,
        user_hash=b"0" * 32,
        client_id="device-interval-client",
        scopes='["book:read"]',
        created_at=created_at,
        expires_at=expires_at,
    )
    first_poll = created_at + timedelta(seconds=5)
    second_poll = created_at + timedelta(seconds=6)
    for poll_count, last_poll_at, interval_seconds in (
        (1, first_poll, 5),
        (2, second_poll, 10),
    ):
        _execute(
            pg_engine,
            """
            update oauth_device_grants
               set poll_count = :poll_count, last_poll_at = :last_poll_at,
                   interval_seconds = :interval_seconds
             where device_code_hash = :device_hash
            """,
            {
                "poll_count": poll_count,
                "last_poll_at": last_poll_at,
                "interval_seconds": interval_seconds,
                "device_hash": device_hash,
            },
        )

    third_poll = created_at + timedelta(seconds=7)
    for poll_count, last_poll_at, interval_seconds in (
        (3, third_poll, 5),
        (3, third_poll, 20),
        (2, second_poll, 15),
        (4, third_poll, 10),
    ):
        _rejects_integrity(
            pg_engine,
            """
            update oauth_device_grants
               set poll_count = :poll_count, last_poll_at = :last_poll_at,
                   interval_seconds = :interval_seconds
             where device_code_hash = :device_hash
            """,
            {
                "poll_count": poll_count,
                "last_poll_at": last_poll_at,
                "interval_seconds": interval_seconds,
                "device_hash": device_hash,
            },
        )


def test_password_email_must_already_be_normalized(pg_engine) -> None:
    _insert_user(pg_engine, "user:email")
    _rejects_integrity(
        pg_engine,
        """
        insert into password_accounts (
            user_id, normalized_email, password_hash, status
        ) values (
            'user:email', ' Alice@Example.Test ',
            :password_hash, 'active'
        )
        """,
        {"password_hash": CANONICAL_PASSWORD_HASH},
    )


def test_auth_rows_never_store_raw_sentinels_and_acl_forbids_physical_delete(
    pg_engine, migrated_postgres_database
) -> None:
    sentinel = f"raw-secret-{uuid4()}"
    digest = sha256(sentinel.encode()).digest()
    user_id = "user:redaction"
    issued_at, expires_at = _times()
    _insert_user(pg_engine, user_id)
    _execute(
        pg_engine,
        """
        insert into credentials (
            credential_id, token_hash, jti, actor_subject_id, actor_type,
            auth_kind, book_id, scopes, issued_at, expires_at, revoked_at
        ) values (
            :credential_id, :token_hash, :jti, :user_id, 'human', 'api_key',
            null, '[]'::jsonb, :issued_at, :expires_at, null
        )
        """,
        {
            "credential_id": uuid4(),
            "token_hash": digest,
            "jti": uuid4(),
            "user_id": user_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    )
    with pg_engine.connect() as connection:
        serialized_rows = []
        for table_name in AUTH_TABLES:
            serialized_rows.extend(
                connection.execute(
                    text(
                        f"select to_jsonb(row_record)::text from {table_name} row_record"
                    )
                ).scalars()
            )

        table_list = ", ".join(f"'{name}'" for name in AUTH_TABLES)
        grants = {
            (row.table_name, row.grantee, row.privilege_type, row.is_grantable)
            for row in connection.execute(
                text(
                    f"""
                    select relation.relname as table_name,
                           coalesce(grantee.rolname, 'PUBLIC') as grantee,
                           acl.privilege_type,
                           acl.is_grantable
                      from pg_catalog.pg_class relation
                      join pg_catalog.pg_namespace namespace
                        on namespace.oid = relation.relnamespace
                      cross join lateral pg_catalog.aclexplode(
                          coalesce(
                              relation.relacl,
                              pg_catalog.acldefault('r', relation.relowner)
                          )
                      ) acl
                      left join pg_catalog.pg_roles grantee
                        on grantee.oid = acl.grantee
                     where namespace.nspname = 'public'
                       and relation.relname in ({table_list})
                       and (
                           acl.grantee = 0
                           or grantee.rolname = :runtime_role
                       )
                    """
                ),
                {"runtime_role": migrated_postgres_database.runtime_role},
            )
        }

    assert all(sentinel not in row for row in serialized_rows)
    assert grants == {
        (table_name, migrated_postgres_database.runtime_role, privilege, False)
        for table_name in AUTH_TABLES
        for privilege in ("SELECT", "INSERT", "UPDATE")
    }
    for table_name in AUTH_TABLES:
        with pytest.raises(DBAPIError):
            _execute(pg_engine, f"delete from {table_name} where false", {})
