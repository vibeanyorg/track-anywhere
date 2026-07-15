from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from track_anywhere_cli.click_app import run
from track_anywhere_cli.config import AuthProfile, TokenStore
from track_anywhere_cli.oauth_login import OAuthForm, refresh_token_resolution


BASE_URL = "https://ledger.example"
RESOURCE = "https://ledger.example/api/v2"
ISSUER = "https://auth.example"
TOKEN_ENDPOINT = "https://auth.example/api/v2/oauth/token"
REVOCATION_ENDPOINT = "https://auth.example/api/v2/oauth/revoke"


def _saved_profile(*, expires_at: float, access: str = "access-old") -> AuthProfile:
    return AuthProfile(
        base_url=BASE_URL,
        resource=RESOURCE,
        issuer=ISSUER,
        client_id="client-lifecycle",
        access_token=access,
        refresh_token="refresh-old",
        expires_at=expires_at,
        scope="book:read ledger:read",
        token_endpoint=TOKEN_ENDPOINT,
        revocation_endpoint=REVOCATION_ENDPOINT,
        auth_kind="pkce",
    )


def _store(monkeypatch, tmp_path) -> TokenStore:
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN", raising=False)
    monkeypatch.delenv("TRACK_ANYWHERE_API_KEY", raising=False)
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(tmp_path / "profiles.json"))
    return TokenStore(base_url=BASE_URL, resource=RESOURCE)


def test_expired_oauth_profile_refreshes_before_api_request_and_rotates_storage(
    monkeypatch, tmp_path, capsys
):
    store = _store(monkeypatch, tmp_path)
    store.save_profile(_saved_profile(expires_at=time.time() - 1))
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append((config, method, path, payload, key))
        if path == "/api/v2/oauth/token":
            assert config.token is None
            assert isinstance(payload, OAuthForm)
            assert dict(payload) == {
                "grant_type": "refresh_token",
                "refresh_token": "refresh-old",
                "client_id": "client-lifecycle",
                "resource": RESOURCE,
            }
            return 200, {
                "access_token": "access-new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "refresh-new",
                "scope": "book:read ledger:read",
            }
        if path == "/api/v2/books":
            assert config.token == "access-new"
            assert config.api_key is None
            return 200, {"books": []}
        raise AssertionError(path)

    assert (
        run(
            ["--base-url", BASE_URL, "book", "list", "--json"],
            requester=requester,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert [path for _, _, path, _, _ in calls] == [
        "/api/v2/oauth/token",
        "/api/v2/books",
    ]
    refreshed = store.load_profile_with_source()
    assert refreshed is not None
    assert refreshed.profile.access_token == "access-new"
    assert refreshed.profile.refresh_token == "refresh-new"
    assert refreshed.profile.expires_at is not None
    assert refreshed.profile.expires_at > time.time() + 3500


def test_auth_logout_revokes_refresh_and_access_tokens_then_deletes_profile(
    monkeypatch, tmp_path, capsys
):
    store = _store(monkeypatch, tmp_path)
    store.save_profile(_saved_profile(expires_at=time.time() + 3600))
    revoked = []

    def requester(config, method, path, payload=None, key=None):
        assert config.token is None
        assert method == "POST"
        assert path == "/api/v2/oauth/revoke"
        assert isinstance(payload, OAuthForm)
        revoked.append(dict(payload))
        return 200, {"revoked": True}

    assert (
        run(
            ["--base-url", BASE_URL, "auth", "logout", "--json"],
            requester=requester,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {
        "authenticated": False,
        "local_profile_deleted": True,
        "remote_revoked": True,
        "resource": RESOURCE,
    }
    assert revoked == [
        {
            "token": "refresh-old",
            "token_type_hint": "refresh_token",
            "client_id": "client-lifecycle",
        },
        {
            "token": "access-old",
            "token_type_hint": "access_token",
            "client_id": "client-lifecycle",
        },
    ]
    assert store.load_profile_with_source() is None


def test_auth_logout_reports_keyring_deletion_failure(monkeypatch, tmp_path, capsys):
    class LockedKeyring:
        def __init__(self):
            self.values = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[(service, account)] = value

        def delete_password(self, _service, _account):
            raise RuntimeError("keychain locked")

    keyring = LockedKeyring()
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    store = TokenStore(base_url=BASE_URL, resource=RESOURCE)
    store.save_profile(_saved_profile(expires_at=time.time() + 3600))

    def requester(_config, _method, _path, payload=None, key=None):
        return 200, {"revoked": True}

    exit_code = run(
        ["--base-url", BASE_URL, "auth", "logout", "--json"],
        requester=requester,
    )

    payload = json.loads(capsys.readouterr().err)
    assert exit_code != 0
    assert payload["data"]["local_profile_deleted"] is False
    with pytest.raises(RuntimeError, match="inconsistent state"):
        store.load_profile_with_source()


def test_concurrent_refresh_uses_latest_rotated_profile_once(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_profile(_saved_profile(expires_at=50))
    first = store.load_profile_with_source()
    second = store.load_profile_with_source()
    assert first is not None and second is not None
    request_count = 0
    count_lock = Lock()
    both_entered = Event()

    def requester(_config, _method, _path, payload=None, key=None):
        nonlocal request_count
        with count_lock:
            request_count += 1
            current = request_count
            if request_count == 2:
                both_entered.set()
        both_entered.wait(timeout=0.2)
        return 200, {
            "access_token": f"access-new-{current}",
            "refresh_token": f"refresh-new-{current}",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "book:read ledger:read",
        }

    from track_anywhere_cli.config import RequestCredential, TokenResolution

    def resolution(stored):
        return TokenResolution(
            token=stored.profile.access_token,
            credential=RequestCredential(
                kind="oauth", secret=stored.profile.access_token
            ),
            diagnostics=[],
            source=stored.source,
            profile=stored.profile,
            store=store,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: refresh_token_resolution(
                    resolution(item), requester=requester, now=100, leeway_seconds=0
                ),
                [first, second],
            )
        )

    assert request_count == 1
    assert {result.token for result in results} == {"access-new-1"}


def test_refresh_writes_fail_closed_marker_before_token_request(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_profile(_saved_profile(expires_at=50))
    stored = store.load_profile_with_source()
    assert stored is not None
    from track_anywhere_cli.config import RequestCredential, TokenResolution

    resolution = TokenResolution(
        token=stored.profile.access_token,
        credential=RequestCredential(kind="oauth", secret=stored.profile.access_token),
        diagnostics=[],
        source=stored.source,
        profile=stored.profile,
        store=store,
    )
    token_request_started = False

    def requester(_config, _method, _path, payload=None, key=None):
        nonlocal token_request_started
        token_request_started = True
        with pytest.raises(RuntimeError, match="inconsistent state"):
            store.load_profile_with_source()
        return 503, {"detail": "token endpoint unavailable"}

    with pytest.raises(RuntimeError, match="OAuth token refresh"):
        refresh_token_resolution(
            resolution,
            requester=requester,
            now=100,
            leeway_seconds=0,
        )

    assert token_request_started is True
    with pytest.raises(RuntimeError, match="inconsistent state"):
        store.load_profile_with_source()


def test_profile_lock_serializes_separate_cli_processes(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    script = """
from track_anywhere_cli.config import TokenStore
print('ready', flush=True)
with TokenStore(base_url='https://ledger.example', resource='https://ledger.example/api/v2').profile_lock():
    print('acquired', flush=True)
"""
    environment = dict(os.environ)

    with store.profile_lock():
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert process.poll() is None

    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert stdout.strip() == "acquired"


def test_profile_save_waits_for_inflight_delete_profile_lock(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_profile(_saved_profile(expires_at=200, access="old"))
    save_started = Event()

    def save_new_profile():
        save_started.set()
        return store.save_profile(_saved_profile(expires_at=300, access="new"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.profile_lock():
            deletion = store._delete_profile_locked()
            future = pool.submit(save_new_profile)
            assert save_started.wait(timeout=1)
            assert future.done() is False
        future.result(timeout=2)

    assert deletion.deleted is True
    remaining = store.load_profile_with_source()
    assert remaining is not None
    assert remaining.profile.access_token == "new"


def test_profile_document_updates_do_not_lose_concurrent_profiles(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    profile_file = tmp_path / "profiles.json"
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(profile_file))
    real_atomic_write = __import__(
        "track_anywhere_cli.config", fromlist=["_atomic_write_text"]
    )._atomic_write_text
    write_count = 0
    count_lock = Lock()
    both_writes = Event()

    def delayed_atomic_write(path, value):
        nonlocal write_count
        with count_lock:
            write_count += 1
            if write_count == 2:
                both_writes.set()
        both_writes.wait(timeout=0.2)
        real_atomic_write(path, value)

    monkeypatch.setattr(
        "track_anywhere_cli.config._atomic_write_text", delayed_atomic_write
    )
    first_store = TokenStore(
        base_url="https://one.example", resource="https://one.example/api/v2"
    )
    second_store = TokenStore(
        base_url="https://two.example", resource="https://two.example/api/v2"
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda pair: pair[0].save_profile(pair[1]),
                [
                    (
                        first_store,
                        _saved_profile_for(
                            "https://one.example", "https://one.example/api/v2", "one"
                        ),
                    ),
                    (
                        second_store,
                        _saved_profile_for(
                            "https://two.example", "https://two.example/api/v2", "two"
                        ),
                    ),
                ],
            )
        )

    assert len(json.loads(profile_file.read_text())["profiles"]) == 2


def _saved_profile_for(base_url: str, resource: str, access: str) -> AuthProfile:
    return AuthProfile(
        base_url=base_url,
        resource=resource,
        issuer=base_url,
        client_id=f"client-{access}",
        access_token=access,
        refresh_token=f"refresh-{access}",
        expires_at=50,
        token_endpoint=f"{base_url}/api/v2/oauth/token",
        revocation_endpoint=f"{base_url}/api/v2/oauth/revoke",
    )


def test_invalid_root_urls_are_structured_click_errors(capsys):
    exit_code = run(["--base-url", "not-a-url", "book", "list", "--agent"])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code != 0
    assert payload["command"] == "cli.parse"
    assert payload["data"]["error"]["code"] == "invalid_argument"


def test_api_key_file_is_used_as_machine_header_credential(
    monkeypatch, tmp_path, capsys
):
    _store(monkeypatch, tmp_path)
    api_key_file = tmp_path / "machine-api-key"
    api_key_file.write_text("ta_machine_only\n", encoding="utf-8")

    def requester(config, method, path, payload=None, key=None):
        assert config.token is None
        assert config.api_key == "ta_machine_only"
        assert method == "GET"
        assert path == "/api/v2/books"
        return 200, {"books": []}

    assert (
        run(
            [
                "--base-url",
                BASE_URL,
                "--api-key-file",
                str(api_key_file),
                "book",
                "list",
                "--json",
            ],
            requester=requester,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_auth_status_validates_api_key_live_with_x_api_key(
    monkeypatch, tmp_path, capsys
):
    _store(monkeypatch, tmp_path)
    api_key_file = tmp_path / "machine-status-key"
    api_key_file.write_text("ta_machine_status\n", encoding="utf-8")
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append((config, method, path, payload, key))
        assert config.token is None
        assert config.api_key == "ta_machine_status"
        assert method == "GET"
        assert path == "/api/v2/auth/token-status"
        return 200, {
            "credential_id": "credential-api-key",
            "actor_subject_id": "machine:test",
            "actor_type": "service",
            "auth_kind": "api_key",
            "book_id": "11111111-1111-1111-1111-111111111111",
            "scopes": ["book:read", "ledger:read"],
            "expires_at": "2030-01-01T00:00:00+00:00",
        }

    assert (
        run(
            [
                "--base-url",
                BASE_URL,
                "--api-key-file",
                str(api_key_file),
                "auth",
                "status",
                "--json",
            ],
            requester=requester,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["authenticated"] is True
    assert payload["data"]["auth_kind"] == "api_key"
    assert len(calls) == 1


def test_auth_login_rejects_positional_secret_before_network(capsys):
    def requester(*_args, **_kwargs):
        raise AssertionError("a positional secret must fail before network access")

    assert (
        run(
            ["auth", "login", "ta_secret_in_argv", "--agent"],
            requester=requester,
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["command"] == "cli.parse"
    assert payload["data"]["error"]["code"] == "usage_error"
