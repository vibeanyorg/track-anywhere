from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from track_anywhere.api.v2.query_routes.journal import serialize_journal_item
from track_anywhere.auth.security import redirect_uri_matches, validate_redirect_uri
from track_anywhere.mcp.server import ChatGptFastMCP, create_mcp_runtime
from track_anywhere.queries.journal import JournalItem
from track_anywhere.server import create_server


_PROTECTED_OUTPUT_FIELDS = {
    "description",
    "description_ref",
    "memo",
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


def _payload_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value)
        for child in value.values():
            names.update(_payload_keys(child))
        return names
    if isinstance(value, (list, tuple)):
        names: set[str] = set()
        for child in value:
            names.update(_payload_keys(child))
        return names
    return set()


def test_sync_mcp_tools_run_outside_the_event_loop_thread() -> None:
    server = ChatGptFastMCP("Threaded MCP")

    @server.tool()
    def worker_thread_id() -> int:
        return get_ident()

    async def invoke() -> tuple[int, object]:
        event_loop_thread_id = get_ident()
        _, structured_content = await server.call_tool("worker_thread_id", {})
        return event_loop_thread_id, structured_content

    event_loop_thread_id, content = asyncio.run(invoke())

    assert isinstance(content, dict)
    assert set(content) == {"result"}
    assert content["result"] != event_loop_thread_id


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
    assert "Read responses expose exact integer `units`" in runtime.server.instructions
    assert "CNY amount `660` means CNY 660.00, not CNY 6.60" in (
        runtime.server.instructions
    )

    read_tools = {
        "ledger_get_account",
        "ledger_get_balances",
        "ledger_get_entry",
        "ledger_get_transaction",
        "ledger_list_accounts",
        "ledger_list_assets",
        "ledger_list_books",
        "ledger_list_categories",
        "ledger_list_entries",
        "ledger_list_transactions",
        "ledger_list_payment_instruments",
    }
    write_tools = {
        "ledger_clear_transaction_category",
        "ledger_record_adjustment",
        "ledger_record_credit_card_payment",
        "ledger_record_fx",
        "ledger_record_fx_credit_card_payment",
        "ledger_record_transfer",
        "ledger_reverse_transaction",
        "ledger_set_transaction_category",
    }
    entry_prepare_tools = {
        "ledger_prepare_adjustment",
        "ledger_prepare_credit_card_payment",
        "ledger_prepare_fx_credit_card_payment",
        "ledger_prepare_expense",
        "ledger_prepare_income",
        "ledger_prepare_refund",
        "ledger_prepare_transfer",
    }
    entry_commit_tools = {"ledger_commit_entry"}
    catalog_write_tools = {
        "ledger_close_account",
        "ledger_create_account",
        "ledger_create_asset",
        "ledger_create_book",
        "ledger_create_category",
        "ledger_create_payment_card",
        "ledger_reopen_account",
    }
    assert {tool.name for tool in tools} == (
        read_tools
        | write_tools
        | entry_prepare_tools
        | entry_commit_tools
        | catalog_write_tools
    )
    for tool in tools:
        if tool.name in write_tools | entry_prepare_tools | entry_commit_tools:
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
        assert tool.description.startswith(
            "Use this only after"
            if tool.name in entry_commit_tools
            else "Use this when"
        )
        assert tool.annotations.readOnlyHint is (tool.name in read_tools)
        assert tool.annotations.destructiveHint is (
            tool.name in entry_commit_tools | {"ledger_reverse_transaction"}
        )
        assert tool.annotations.idempotentHint is (tool.name not in entry_prepare_tools)
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema is not None
        if tool.name in write_tools | catalog_write_tools:
            assert "request_id" in tool.inputSchema["properties"]
            assert "request_id" in tool.inputSchema["required"]
        if tool.name in entry_prepare_tools:
            assert tool.meta["track_anywhere/mode"] == "entry_prepare"
            assert "request_id" not in tool.inputSchema["properties"]
            assert "commit_token" in _schema_property_names(tool.outputSchema)
        if tool.name in entry_commit_tools:
            assert tool.meta["track_anywhere/mode"] == "entry_commit"
            assert set(tool.inputSchema["properties"]) == {
                "book_id",
                "intent_id",
                "commit_token",
                "request_id",
            }
    account_tool = next(tool for tool in tools if tool.name == "ledger_create_account")
    assert "system_role" not in account_tool.inputSchema["properties"]
    assert set(account_tool.inputSchema["properties"]["account_type"]["enum"]) == {
        "asset",
        "liability",
    }
    assert "investment" in account_tool.description
    category_tool = next(
        tool for tool in tools if tool.name == "ledger_create_category"
    )
    assert "category_id" not in category_tool.inputSchema["properties"]
    assert "parent_category_id" in category_tool.inputSchema["properties"]
    assert "expense" in category_tool.description
    assert "internal account" in category_tool.description
    assert {
        "ledger_record_expense",
        "ledger_record_credit_card_charge",
    }.isdisjoint({tool.name for tool in tools})
    for tool_name in (
        "ledger_record_transfer",
        "ledger_record_credit_card_payment",
    ):
        description = next(tool for tool in tools if tool.name == tool_name).description
        assert "major unit" in description
        assert "`660` means 660.00" in description
    adjustment_tool = next(
        tool for tool in tools if tool.name == "ledger_record_adjustment"
    )
    assert {
        "account_id",
        "actual_balance",
        "asset_code",
        "book_id",
        "effective_at",
        "expected_balance",
        "request_id",
    } == set(adjustment_tool.inputSchema["required"])
    assert "adjustment_account_id" not in adjustment_tool.inputSchema["properties"]
    fx_tool = next(tool for tool in tools if tool.name == "ledger_record_fx")
    assert set(fx_tool.inputSchema["required"]) == {
        "book_id",
        "effective_at",
        "request_id",
        "source_account_id",
        "source_amount",
        "source_asset_code",
        "source_trading_account_id",
        "target_account_id",
        "target_amount",
        "target_asset_code",
        "target_trading_account_id",
    }
    fx_card_tool = next(
        tool
        for tool in tools
        if tool.name == "ledger_record_fx_credit_card_payment"
    )
    assert {
        "fee_amount",
        "fee_category_id",
        "fee_category_version_id",
    } <= set(fx_card_tool.inputSchema["required"])
    assert "source_amount excludes fee_amount" in fx_card_tool.description
    assert "Never infer or round" in fx_tool.description


def test_entry_tools_are_hidden_without_write_scope_and_still_guard_calls() -> None:
    runtime = create_mcp_runtime(
        SimpleNamespace(session_factory=lambda: None),
        "http://testserver",
    )
    token = AccessToken(
        token="read-only",
        client_id="mcp-shadow-read-contract",
        scopes=["ledger:read"],
        subject="human:mcp-shadow-read",
        claims={"actor_type": "human", "book_id": None},
    )
    context = auth_context_var.set(AuthenticatedUser(token))
    try:
        names = {tool.name for tool in asyncio.run(runtime.server.list_tools())}
        result = asyncio.run(
            runtime.server.call_tool(
                "ledger_prepare_expense",
                {
                    "book_id": str(uuid4()),
                    "amount": {
                        "value": "660",
                        "denomination": "asset_unit",
                        "asset_code": "CNY",
                        "source_text": "660",
                    },
                    "source_account": {"query": "微信零钱通"},
                    "occurred_at": "2026-07-24T12:00:00Z",
                    "category": {"path": ["食品", "饮料"]},
                },
            )
        )
    finally:
        auth_context_var.reset(context)

    assert not any(name.startswith("ledger_prepare_") for name in names)
    assert "ledger_commit_entry" not in names
    assert result.isError is True
    assert "ledger:write" in result.content[0].text
    assert (
        'scope="ledger:read ledger:write"' in (result.meta["mcp/www_authenticate"][0])
    )


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


def test_mcp_journal_serializer_drops_internal_protected_content_reference() -> None:
    item = JournalItem(
        transaction_id=uuid4(),
        effective_at=datetime(2026, 7, 17, tzinfo=UTC),
        book_position=1,
        transaction_kind="standard",
        postings=(),
        reversed_by_transaction_id=None,
        reverses_transaction_id=None,
        description_ref=uuid4(),
    )

    payload = serialize_journal_item(item).model_dump(mode="json")

    assert _payload_keys(payload).isdisjoint(_PROTECTED_OUTPUT_FIELDS)


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
