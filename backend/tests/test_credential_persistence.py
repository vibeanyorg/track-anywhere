from __future__ import annotations

import sqlite3

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_persistence_does_not_store_raw_bearer_tokens(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    database_url = f"sqlite:///{database_path}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    owner_token = service.owner_token

    credential, replay = service.issue_agent_credential_command(
        owner_token,
        {"scopes": ["capture:draft"], "ttl_minutes": 30},
        idempotency_key="persist-agent-credential",
    )
    assert replay is False
    agent_token = credential["token"]
    service.revoke_credential_command(
        owner_token,
        {"target_token": agent_token, "reason": "rotation"},
        idempotency_key="persist-agent-revoke",
    )

    raw_database = database_path.read_text(errors="ignore")

    assert owner_token not in raw_database
    assert agent_token not in raw_database


def test_machine_credential_issue_uses_incremental_persistence(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    owner_token = first.owner_token

    def fail_startup_maintenance(_service):
        raise AssertionError("credential issue should not use startup maintenance persistence")

    first.storage.save_startup_maintenance = fail_startup_maintenance
    credential, replay = first.issue_machine_credential_command(
        owner_token,
        {
            "name": "Stable local token",
            "scopes": ["account:read", "book:read", "ledger:read"],
            "ttl_minutes": 3650 * 24 * 60,
        },
        idempotency_key="persist-machine-credential",
    )

    assert replay is False
    machine_token = credential["token"]
    assert first.actor_for_book(machine_token, None, "account:read").actor_id == "machine"

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    assert second.actor_for_book(machine_token, None, "ledger:read").actor_id == "machine"
    replayed, replay = second.issue_machine_credential_command(
        owner_token,
        {
            "name": "Stable local token",
            "scopes": ["account:read", "book:read", "ledger:read"],
            "ttl_minutes": 3650 * 24 * 60,
        },
        idempotency_key="persist-machine-credential",
    )

    assert replay is True
    assert replayed["token"] == "[REDACTED]"


def test_persistence_removes_legacy_raw_owner_token_state(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    database_url = f"sqlite:///{database_path}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert or replace into app_state (key, value) values (?, ?)",
            ("owner_token", f'{{"token": "{token}"}}'),
        )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert second.actor_from_token(token, "account:read").actor_id == "owner"
    with sqlite3.connect(database_path) as connection:
        legacy_state = connection.execute("select value from app_state where key = 'owner_token'").fetchone()
    raw_database = database_path.read_text(errors="ignore")

    assert legacy_state is None
    assert token not in raw_database
