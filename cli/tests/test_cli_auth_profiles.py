from __future__ import annotations

import json
import stat
import sys
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

import track_anywhere_cli.config as auth_config
from track_anywhere_cli.http import request_json


class _JsonResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return None

    def read(self) -> bytes:
        return b'{"ok": true}'


def _profile(base_url: str, resource: str, access_token: str):
    profile_type = getattr(auth_config, "AuthProfile")
    return profile_type(
        base_url=base_url,
        resource=resource,
        issuer=base_url,
        client_id="client-test",
        access_token=access_token,
        refresh_token=f"refresh-{access_token}",
        expires_at=2_000_000_000.0,
        scope="book:read ledger:read",
        token_endpoint=f"{base_url}/api/v2/oauth/token",
        revocation_endpoint=f"{base_url}/api/v2/oauth/revoke",
        auth_kind="pkce",
    )


def test_http_transport_sends_api_key_only_as_x_api_key(monkeypatch):
    config_type = auth_config.CliConfig
    captured = {}

    def fake_open(request, timeout):
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["timeout"] = timeout
        return _JsonResponse()

    monkeypatch.setattr("track_anywhere_cli.http._open_request", fake_open)

    status, payload = request_json(
        config_type(
            base_url="https://ledger.example", token=None, api_key="ta_machine"
        ),
        "GET",
        "/api/v2/books",
    )

    assert status == 200
    assert payload == {"ok": True}
    assert captured["headers"]["x-api-key"] == "ta_machine"
    assert "authorization" not in captured["headers"]


def test_http_transport_rejects_non_loopback_plain_http_before_network(monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("unsafe HTTP must fail before network access")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)

    status, payload = request_json(
        auth_config.CliConfig(base_url="http://ledger.example", token="oauth-token"),
        "GET",
        "/api/v2/books",
    )

    assert status == 400
    assert payload["error"]["code"] == "insecure_transport"


@pytest.mark.parametrize(
    "credential",
    [
        {"token": "oauth-secret"},
        {"api_key": "api-key-secret"},
    ],
)
def test_http_transport_does_not_follow_redirects_with_credentials(credential):
    leaked_headers = []

    class SinkHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            leaked_headers.append(dict(self.headers.items()))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, _format, *_args):
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()
    sink_url = f"http://127.0.0.1:{sink.server_address[1]}/api/v2/sink"

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", sink_url)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    redirect_thread = Thread(target=redirect.serve_forever, daemon=True)
    redirect_thread.start()
    try:
        status, _payload = request_json(
            auth_config.CliConfig(
                base_url=f"http://127.0.0.1:{redirect.server_address[1]}",
                **credential,
            ),
            "GET",
            "/api/v2/books",
        )
    finally:
        redirect.shutdown()
        sink.shutdown()
        redirect.server_close()
        sink.server_close()
        redirect_thread.join(timeout=2)
        sink_thread.join(timeout=2)

    assert status == 302
    assert leaked_headers == []


def test_http_transport_allows_only_known_oauth_metadata_routes(monkeypatch):
    requested = []

    def fake_open(request, timeout):
        requested.append(request.full_url)
        return _JsonResponse()

    monkeypatch.setattr("track_anywhere_cli.http._open_request", fake_open)
    config = auth_config.CliConfig(base_url="https://ledger.example")

    assert (
        request_json(
            config,
            "GET",
            "/.well-known/oauth-protected-resource/api/v2",
        )[0]
        == 200
    )
    assert (
        request_json(
            config,
            "GET",
            "/.well-known/oauth-authorization-server",
        )[0]
        == 200
    )
    status, payload = request_json(config, "GET", "/.well-known/not-oauth")

    assert status == 400
    assert payload["error"]["code"] == "unsupported_api_route"
    assert requested == [
        "https://ledger.example/.well-known/oauth-protected-resource/api/v2",
        "https://ledger.example/.well-known/oauth-authorization-server",
    ]


def test_profiles_are_isolated_by_canonical_base_and_resource_and_written_0600(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    profile_file = tmp_path / "auth-profiles.json"
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(profile_file))
    store_type = auth_config.TokenStore

    first_store = store_type(
        base_url="https://LEDGER.example:443/",
        resource="https://ledger.example/api/v2",
    )
    second_store = store_type(
        base_url="https://other.example",
        resource="https://other.example/api/v2",
    )
    first_store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "oauth-first",
        )
    )
    second_store.save_profile(
        _profile(
            "https://other.example",
            "https://other.example/api/v2",
            "oauth-second",
        )
    )

    equivalent = store_type(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    ).load_profile_with_source()
    second = second_store.load_profile_with_source()

    assert equivalent is not None
    assert equivalent.profile.access_token == "oauth-first"
    assert second is not None
    assert second.profile.access_token == "oauth-second"
    assert stat.S_IMODE(profile_file.stat().st_mode) == 0o600
    document = json.loads(profile_file.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert len(document["profiles"]) == 2


def test_atomic_profile_write_does_not_change_existing_parent_directory_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    shared_parent = tmp_path / "existing-parent"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    profile_file = shared_parent / "profiles.json"
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(profile_file))
    store = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )

    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "oauth-directory-mode",
        )
    )

    assert stat.S_IMODE(shared_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(profile_file.stat().st_mode) == 0o600


def test_scoped_store_reads_legacy_raw_token_file(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "keyring", None)
    token_file = tmp_path / "legacy-token"
    token_file.write_text("ta_legacy_raw\n", encoding="utf-8")
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(token_file))

    stored = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    ).load_profile_with_source()

    assert stored is not None
    assert stored.source == "legacy_token_file"
    assert stored.profile.access_token == "ta_legacy_raw"
    assert stored.profile.base_url == "https://ledger.example"
    assert stored.profile.resource == "https://ledger.example/api/v2"


def test_default_profile_document_never_degrades_invalid_json_into_bearer(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    profile_file = tmp_path / ".config" / "track-anywhere" / "auth-profiles.json"
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text(
        '{"version":2,"profiles":{"prod":{"refresh_token":"rt-secret"}}}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="invalid OAuth profile document"):
        auth_config.TokenStore(
            base_url="https://untrusted.example",
            resource="https://untrusted.example/api/v2",
        ).load_profile_with_source()

    from track_anywhere_cli.click_app import run

    def fail_network(*_args, **_kwargs):
        raise AssertionError("invalid profile storage must fail before network access")

    assert (
        run(
            [
                "--base-url",
                "https://untrusted.example",
                "auth",
                "logout",
                "--agent",
            ],
            requester=fail_network,
        )
        != 0
    )


def test_scoped_store_does_not_bind_global_legacy_token_to_arbitrary_host(
    monkeypatch, tmp_path
):
    class FakeKeyring:
        def __init__(self):
            self.values = {("track-anywhere", "cli-token"): "legacy-secret"}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[(service, account)] = value

        def delete_password(self, service, account):
            self.values.pop((service, account), None)

    keyring = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)

    stored = auth_config.TokenStore(
        base_url="https://untrusted.example",
        resource="https://untrusted.example/api/v2",
    ).load_profile_with_source()

    assert stored is None
    assert keyring.values[("track-anywhere", "cli-token")] == "legacy-secret"


def test_keyring_write_failure_removes_stale_profile_before_file_fallback(
    monkeypatch, tmp_path
):
    class FlakyKeyring:
        def __init__(self):
            self.values = {}
            self.fail_writes = False

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            if self.fail_writes:
                raise RuntimeError("keychain write locked")
            self.values[(service, account)] = value

        def delete_password(self, service, account):
            self.values.pop((service, account), None)

    keyring = FlakyKeyring()
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    store = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )
    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "old",
        )
    )
    keyring.fail_writes = True

    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "new",
        )
    )
    loaded = store.load_profile_with_source()

    assert loaded is not None
    assert loaded.source == "profile_file"
    assert loaded.profile.access_token == "new"


def test_keyring_success_removes_stale_file_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)
    store = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )
    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "old-file",
        )
    )

    class WorkingKeyring:
        def __init__(self):
            self.values = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[(service, account)] = value

        def delete_password(self, service, account):
            self.values.pop((service, account), None)

    monkeypatch.setitem(sys.modules, "keyring", WorkingKeyring())
    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "new-keyring",
        )
    )
    monkeypatch.setitem(sys.modules, "keyring", None)

    assert store.load_profile_with_source() is None


def test_keyring_write_and_delete_failure_blocks_stale_profile(monkeypatch, tmp_path):
    class LockedKeyring:
        def __init__(self):
            self.values = {}
            self.locked = False

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            if self.locked:
                raise RuntimeError("keychain write locked")
            self.values[(service, account)] = value

        def delete_password(self, _service, _account):
            raise RuntimeError("keychain delete locked")

    keyring = LockedKeyring()
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    store = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )
    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "old",
        )
    )
    keyring.locked = True

    with pytest.raises(RuntimeError, match="stale keyring data"):
        store.save_profile(
            _profile(
                "https://ledger.example",
                "https://ledger.example/api/v2",
                "new",
            )
        )
    with pytest.raises(RuntimeError, match="inconsistent state"):
        store.load_profile_with_source()


def test_keyring_read_failure_never_falls_back_to_profile_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)
    store = auth_config.TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )
    store.save_profile(
        _profile(
            "https://ledger.example",
            "https://ledger.example/api/v2",
            "file-token",
        )
    )

    class UnreadableKeyring:
        def get_password(self, _service, _account):
            raise RuntimeError("keychain read locked")

    monkeypatch.setitem(sys.modules, "keyring", UnreadableKeyring())

    with pytest.raises(RuntimeError, match="could not read OAuth profile"):
        store.load_profile_with_source()


def test_profile_body_must_match_scoped_storage_identity(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "keyring", None)
    profile_file = tmp_path / "profiles.json"
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(profile_file))
    store = auth_config.TokenStore(
        base_url="https://untrusted.example",
        resource="https://untrusted.example/api/v2",
    )
    profile_key = auth_config._profile_key(store.base_url, store.resource)
    profile_file.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    profile_key: _profile(
                        "https://prod.example",
                        "https://prod.example/api/v2",
                        "prod-secret",
                    ).to_dict()
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        store.load_profile_with_source()


def test_api_key_file_resolves_to_machine_credential(monkeypatch, tmp_path):
    api_key_file = tmp_path / "api-key"
    api_key_file.write_text("ta_machine_file\n", encoding="utf-8")
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN", raising=False)
    monkeypatch.delenv("TRACK_ANYWHERE_API_KEY", raising=False)
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    args = Namespace(
        token=None,
        api_key_file=api_key_file,
        insecure_automation=False,
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )

    resolution = auth_config.resolve_token_with_diagnostics(args)

    assert resolution.token is None
    assert resolution.credential is not None
    assert resolution.credential.kind == "api_key"
    assert resolution.credential.secret == "ta_machine_file"
    assert resolution.source == "api_key_file"


def test_api_key_environment_requires_insecure_automation(monkeypatch):
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN", raising=False)
    monkeypatch.setenv("TRACK_ANYWHERE_API_KEY", "ta_machine_env")
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    args = Namespace(
        token=None,
        api_key_file=None,
        insecure_automation=False,
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    )

    try:
        auth_config.resolve_token_with_diagnostics(args)
    except RuntimeError as exc:
        assert "TRACK_ANYWHERE_API_KEY requires --insecure-automation" in str(exc)
    else:
        raise AssertionError(
            "environment API keys must require explicit insecure opt-in"
        )
