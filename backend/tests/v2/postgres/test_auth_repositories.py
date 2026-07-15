from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.repositories import RowLock
from track_anywhere.infrastructure.db.repositories.auth import (
    AuthRepository,
    AuthorizationGrantUnavailable,
    BookMembershipRepository,
    CredentialUnavailable,
    DeviceGrantUnavailable,
)


def _seed_auth(pg_engine):
    now = datetime.now(UTC)
    book_id = uuid4()
    credential_id = uuid4()
    token_hash = b"t" * 32
    client_secret_hash = b"s" * 32
    code_hash = b"c" * 32
    device_hash = b"d" * 32
    user_code_hash = b"u" * 32
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
            """)
        )
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Auth Book', 'USD', 'active')
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            insert into users (user_id, subject_type, current_display_name, status)
            values ('human:test', 'human', 'Test User', 'active')
            """)
        )
        connection.execute(
            text("""
            insert into book_members (book_id, user_id, role, status, scopes)
            values (:book_id, 'human:test', 'owner', 'active', '["ledger:write"]')
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            insert into auth_identities (
                identity_id, provider, subject, user_id, email,
                email_verified, status
            ) values (
                :identity_id, 'oidc', 'subject-1', 'human:test',
                'test@example.com', true, 'active'
            )
            """),
            {"identity_id": uuid4()},
        )
        connection.execute(
            text("""
            insert into credentials (
                credential_id, token_hash, jti, actor_subject_id, actor_type,
                auth_kind, book_id, scopes, issued_at, expires_at
            ) values (
                :credential_id, :token_hash, :jti, 'human:test', 'human',
                'browser_session', :book_id, '["ledger:write"]', :now, :expires
            )
            """),
            {
                "credential_id": credential_id,
                "token_hash": token_hash,
                "jti": uuid4(),
                "book_id": book_id,
                "now": now,
                "expires": now + timedelta(hours=1),
            },
        )
        connection.execute(
            text("""
            insert into oauth_clients (
                client_id, client_name, client_type, client_secret_hash,
                scopes, status
            ) values (
                'client-1', 'Test Client', 'confidential', :secret,
                '["ledger:write"]', 'active'
            )
            """),
            {"secret": client_secret_hash},
        )
        connection.execute(
            text("""
            insert into oauth_client_redirect_uris (client_id, redirect_uri, status)
            values ('client-1', 'https://client.example/callback', 'active')
            """)
        )
        connection.execute(
            text("""
            insert into oauth_authorization_grants (
                code_hash, client_id, redirect_uri, registered_redirect_uri,
                actor_subject_id, scopes,
                code_challenge, challenge_method, created_at, expires_at
            ) values (
                :code_hash, 'client-1', 'https://client.example/callback',
                'https://client.example/callback',
                'human:test', '["ledger:write"]', :challenge, 'S256', :now, :expires
            )
            """),
            {
                "code_hash": code_hash,
                "challenge": "a" * 43,
                "now": now,
                "expires": now + timedelta(minutes=10),
            },
        )
        connection.execute(
            text("""
            insert into oauth_device_grants (
                device_code_hash, user_code_hash, client_id, scopes, status,
                created_at, expires_at, interval_seconds, poll_count
            ) values (
                :device_hash, :user_code_hash, 'client-1', '["ledger:write"]',
                'pending', :now, :expires, 5, 0
            )
            """),
            {
                "device_hash": device_hash,
                "user_code_hash": user_code_hash,
                "now": now,
                "expires": now + timedelta(minutes=10),
            },
        )
    return {
        "book_id": book_id,
        "token_hash": token_hash,
        "client_secret_hash": client_secret_hash,
        "code_hash": code_hash,
        "device_hash": device_hash,
        "user_code_hash": user_code_hash,
        "now": now,
        "expires": now + timedelta(minutes=10),
    }


def test_auth_snapshots_redact_hashes_and_lifecycle_updates(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        membership = repository.get_membership(
            seeded["book_id"],
            "human:test",
            lock=RowLock.SHARE,
        )
        identity = repository.get_identity("oidc", "subject-1")
        credential = repository.get_credential(seeded["token_hash"])
        client = repository.get_oauth_client("client-1")
        repository.mark_credential_used(seeded["token_hash"], seeded["now"])
        revoked = repository.revoke_credential(
            seeded["token_hash"], seeded["now"] + timedelta(seconds=1)
        )

    assert membership.book_id == seeded["book_id"]
    assert identity.user_id == "human:test"
    assert credential.actor_subject_id == "human:test"
    assert client.client_id == "client-1"
    assert revoked.revoked_at == seeded["now"] + timedelta(seconds=1)
    forbidden_fields = {
        "password_hash",
        "token",
        "token_hash",
        "client_secret",
        "client_secret_hash",
        "code_hash",
        "device_code_hash",
        "user_code_hash",
        "session_hash",
        "csrf_token_hash",
    }
    for snapshot in (credential, client):
        assert forbidden_fields.isdisjoint(snapshot.__dataclass_fields__)
        for secret in (seeded["token_hash"], seeded["client_secret_hash"]):
            assert repr(secret) not in repr(snapshot)
            assert secret.hex() not in repr(snapshot)

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "select last_used_at from credentials where token_hash = :token_hash"
                ),
                {"token_hash": seeded["token_hash"]},
            ).scalar_one()
            == seeded["now"]
        )


def test_auth_subject_and_membership_lifecycle_is_one_way(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    revoked_at = seeded["now"] + timedelta(seconds=1)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        identity = repository.disable_identity("oidc", "subject-1")
        user = repository.disable_user("human:test")
        membership = BookMembershipRepository(session).revoke(
            seeded["book_id"],
            "human:test",
            revoked_at=revoked_at,
        )

    assert identity.status == "disabled"
    assert user.status == "disabled"
    assert membership.status == "revoked"
    assert membership.revoked_at == revoked_at


def test_credential_lifecycle_rejects_pre_issue_timestamps(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    before_issue = seeded["now"] - timedelta(seconds=1)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        with pytest.raises(CredentialUnavailable):
            repository.mark_credential_used(seeded["token_hash"], before_issue)
        with pytest.raises(CredentialUnavailable):
            repository.revoke_credential(seeded["token_hash"], before_issue)


def test_authorization_grant_rejects_pre_creation_timestamps(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    before_create = seeded["now"] - timedelta(seconds=1)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        with pytest.raises(AuthorizationGrantUnavailable):
            repository.consume_authorization_grant(
                seeded["code_hash"], used_at=before_create
            )
        with pytest.raises(AuthorizationGrantUnavailable):
            repository.revoke_authorization_grant(
                seeded["code_hash"], revoked_at=before_create
            )


def test_device_grant_rejects_non_monotonic_lifecycle_timestamps(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        with pytest.raises(DeviceGrantUnavailable):
            repository.approve_device_grant(
                seeded["user_code_hash"],
                actor_subject_id="human:test",
                approved_at=seeded["now"] - timedelta(seconds=1),
            )
        approved_at = seeded["now"] + timedelta(seconds=2)
        repository.approve_device_grant(
            seeded["user_code_hash"],
            actor_subject_id="human:test",
            approved_at=approved_at,
        )
        with pytest.raises(DeviceGrantUnavailable):
            repository.consume_device_grant(
                seeded["device_hash"],
                consumed_at=approved_at - timedelta(seconds=1),
            )


def test_oauth_grants_use_locked_one_way_lifecycle(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    used_at = seeded["now"] + timedelta(seconds=1)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        grant = repository.consume_authorization_grant(
            seeded["code_hash"],
            used_at=used_at,
        )
        repository.approve_device_grant(
            seeded["user_code_hash"],
            actor_subject_id="human:test",
            approved_at=used_at,
        )
        device = repository.consume_device_grant(
            seeded["device_hash"],
            consumed_at=used_at + timedelta(seconds=1),
        )

    assert grant.client_id == "client-1"
    assert device.status == "consumed"
    forbidden_fields = {"code_hash", "device_code_hash", "user_code_hash"}
    assert forbidden_fields.isdisjoint(grant.__dataclass_fields__)
    assert forbidden_fields.isdisjoint(device.__dataclass_fields__)
    for secret in (
        seeded["code_hash"],
        seeded["device_hash"],
        seeded["user_code_hash"],
    ):
        assert repr(secret) not in repr(grant)
        assert repr(secret) not in repr(device)
    with Session(pg_engine) as session, session.begin():
        with pytest.raises(AuthorizationGrantUnavailable):
            AuthRepository(session).consume_authorization_grant(
                seeded["code_hash"],
                used_at=used_at + timedelta(seconds=2),
            )
        with pytest.raises(DeviceGrantUnavailable):
            AuthRepository(session).approve_device_grant(
                seeded["user_code_hash"],
                actor_subject_id="human:test",
                approved_at=used_at + timedelta(seconds=2),
            )


def test_oauth_revocation_poll_denial_and_expiry_are_terminal(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    observed_at = seeded["now"] + timedelta(seconds=1)
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        revoked = repository.revoke_authorization_grant(
            seeded["code_hash"],
            revoked_at=observed_at,
        )
        polled = repository.record_device_poll(
            seeded["device_hash"],
            polled_at=observed_at,
        )
        denied = repository.deny_device_grant(
            seeded["user_code_hash"],
            denied_at=observed_at,
        )

    assert revoked.revoked_at == observed_at
    assert polled.poll_count == 1
    assert polled.last_poll_at == observed_at
    assert denied.status == "denied"
    with Session(pg_engine) as session, session.begin():
        repository = AuthRepository(session)
        with pytest.raises(AuthorizationGrantUnavailable):
            repository.consume_authorization_grant(
                seeded["code_hash"],
                used_at=observed_at + timedelta(seconds=1),
            )
        with pytest.raises(DeviceGrantUnavailable):
            repository.approve_device_grant(
                seeded["user_code_hash"],
                actor_subject_id="human:test",
                approved_at=observed_at + timedelta(seconds=1),
            )


def test_expired_device_grant_is_terminal(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    with Session(pg_engine) as session, session.begin():
        expired = AuthRepository(session).expire_device_grant(
            seeded["device_hash"],
            observed_at=seeded["expires"],
        )
    assert expired.status == "expired"
    with Session(pg_engine) as session, session.begin():
        with pytest.raises(DeviceGrantUnavailable):
            AuthRepository(session).approve_device_grant(
                seeded["user_code_hash"],
                actor_subject_id="human:test",
                approved_at=seeded["expires"] + timedelta(seconds=1),
            )


def test_membership_share_lock_blocks_concurrent_revocation(pg_engine) -> None:
    seeded = _seed_auth(pg_engine)
    first = Session(pg_engine)
    second = Session(pg_engine)
    try:
        AuthRepository(first).get_membership(
            seeded["book_id"],
            "human:test",
            lock=RowLock.SHARE,
        )
        second.execute(text("set local lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError) as error_info:
            second.execute(
                text("""
                update book_members
                   set status = 'revoked', revoked_at = clock_timestamp()
                 where book_id = :book_id and user_id = 'human:test'
                """),
                {"book_id": seeded["book_id"]},
            )
        assert getattr(error_info.value.orig, "sqlstate", "") == "55P03"
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()
    with pg_engine.begin() as connection:
        updated = connection.execute(
            text("""
            update book_members
               set status = 'revoked', revoked_at = clock_timestamp()
             where book_id = :book_id and user_id = 'human:test'
            """),
            {"book_id": seeded["book_id"]},
        ).rowcount
    assert updated == 1
