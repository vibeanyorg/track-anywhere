from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from track_anywhere.auth.security import redirect_uri_matches, validate_redirect_uri
from track_anywhere.mcp.server import create_mcp_runtime
from track_anywhere.server import create_server


_PROTECTED_OUTPUT_FIELDS = {
    "description",
    "description_ref",
    "purpose",
    "transaction_memo",
    "line_memos",
    "ndjson",
    "ciphertext",
    "nonce",
    "key_ref",
}


def _schema_property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        for child in value.values():
            names.update(_schema_property_names(child))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for child in value:
            names.update(_schema_property_names(child))
        return names
    return set()


def test_standard_well_known_metadata_uses_fixed_resources(monkeypatch) -> None:
    monkeypatch.delenv("TRACK_ANYWHERE_DATABASE_URL", raising=False)
    application = create_server(public_base_url="http://testserver")
    client = TestClient(application)

    authorization = client.get("/.well-known/oauth-authorization-server")
    api_resource = client.get("/.well-known/oauth-protected-resource/api/v2")

    assert authorization.status_code == 200
    assert authorization.json()["issuer"] == "http://testserver/"
    assert authorization.json()["code_challenge_methods_supported"] == ["S256"]
    assert authorization.json()["resource_parameter_supported"] is True
    assert api_resource.status_code == 200
    assert api_resource.json()["resource"] == "http://testserver/api/v2"
    assert api_resource.json()["authorization_servers"] == ["http://testserver/"]


def test_mcp_descriptors_mirror_oauth_security_and_tool_annotations() -> None:
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "http://testserver",
    )
    tools = asyncio.run(runtime.server.list_tools())

    read_tools = {
        "ledger_get_account",
        "ledger_get_balances",
        "ledger_get_transaction",
        "ledger_list_accounts",
        "ledger_list_assets",
        "ledger_list_books",
        "ledger_list_categories",
        "ledger_list_transactions",
    }
    write_tools = {
        "ledger_record_credit_card_charge",
        "ledger_record_credit_card_payment",
        "ledger_record_expense",
        "ledger_record_transfer",
    }
    catalog_write_tools = {
        "ledger_create_account",
        "ledger_create_asset",
        "ledger_create_book",
    }
    assert {tool.name for tool in tools} == (
        read_tools | write_tools | catalog_write_tools
    )
    for tool in tools:
        if tool.name in write_tools:
            expected_scopes = ["ledger:read", "ledger:write"]
        elif tool.name in catalog_write_tools:
            expected_scopes = ["book:read", "book:write", "ledger:read"]
        elif tool.name == "ledger_list_books":
            expected_scopes = ["book:read", "ledger:read"]
        else:
            expected_scopes = ["ledger:read"]
        expected = [{"type": "oauth2", "scopes": expected_scopes}]
        assert tool.model_extra["securitySchemes"] == expected
        assert tool.meta["securitySchemes"] == expected
        assert tool.description.startswith("Use this when")
        assert tool.annotations.readOnlyHint is (tool.name in read_tools)
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema is not None
        if tool.name in write_tools | catalog_write_tools:
            assert "request_id" in tool.inputSchema["properties"]
            assert "request_id" in tool.inputSchema["required"]
    account_tool = next(tool for tool in tools if tool.name == "ledger_create_account")
    assert "system_role" not in account_tool.inputSchema["properties"]


def test_mcp_transaction_reads_cannot_request_or_return_protected_content() -> None:
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "http://testserver",
    )
    tools = asyncio.run(runtime.server.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}

    for tool_name in ("ledger_list_transactions", "ledger_get_transaction"):
        tool = tools_by_name[tool_name]
        assert "include_description" not in tool.inputSchema["properties"]
        assert _schema_property_names(tool.outputSchema).isdisjoint(
            _PROTECTED_OUTPUT_FIELDS
        )

    assert not any(
        fragment in tool.name
        for tool in tools
        for fragment in ("archive", "import", "protected")
    )


def test_mcp_requires_oauth_and_advertises_resource_metadata() -> None:
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "http://testserver",
    )
    with TestClient(runtime.application) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "contract", "version": "1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
        metadata = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 401
    assert (
        'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"'
    ) in response.headers["WWW-Authenticate"]
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "http://testserver/mcp"
    assert metadata.json()["authorization_servers"] == ["http://testserver/"]
    assert metadata.json()["scopes_supported"] == [
        "book:read",
        "book:write",
        "ledger:read",
        "ledger:write",
    ]


def test_mcp_accepts_only_explicitly_configured_internal_proxy_host(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRACK_ANYWHERE_MCP_TRUSTED_PROXY_HOSTS", "api:8000")
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "https://ledger.example.com",
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "proxy-contract", "version": "1"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Origin": "https://ledger.example.com",
    }

    with TestClient(runtime.application) as client:
        accepted = client.post(
            "/mcp",
            json=payload,
            headers={**headers, "Host": "api:8000"},
        )

    allowed_hosts = runtime.server.settings.transport_security.allowed_hosts
    assert "api:8000" in allowed_hosts
    assert "untrusted.internal:8000" not in allowed_hosts
    assert accepted.status_code == 401
    assert "resource_metadata=" in accepted.headers["WWW-Authenticate"]


def test_native_loopback_redirect_allows_only_ephemeral_port_variation() -> None:
    registered = validate_redirect_uri("http://127.0.0.1/callback")

    assert redirect_uri_matches(registered, "http://127.0.0.1:49152/callback")
    assert not redirect_uri_matches(registered, "http://localhost:49152/callback")
    assert not redirect_uri_matches(registered, "http://127.0.0.1:49152/other")
    assert not redirect_uri_matches(registered, "http://127.0.0.1:49152/callback?x=1")


def test_mcp_evaluation_pack_has_ten_fixed_read_only_questions() -> None:
    evaluation_path = Path(__file__).parents[2] / "mcp" / "evaluation.xml"
    root = ElementTree.parse(evaluation_path).getroot()
    pairs = root.findall("qa_pair")

    assert root.tag == "evaluation"
    assert len(pairs) == 10
    for pair in pairs:
        question = pair.findtext("question", "")
        answer = pair.findtext("answer", "")
        assert "as_of_book_position=7" in question
        assert "写入" not in question
        assert answer
