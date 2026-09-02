from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError

from track_anywhere.application.entries.contracts import (
    Clarification,
    ClarificationCode,
    CommitEntryInput,
    CommittedEntry,
    EverydayEntryInput,
    PreparedEntry,
    PreparedEntryStatus,
    PreviewMoney,
    EntryPreview,
    ResolvedEntryReferences,
)
from track_anywhere.application.entries.errors import (
    EntryErrorCode,
    EntryGatewayError,
)
from track_anywhere.mcp import entry_tools
from track_anywhere.mcp.entry_tools import (
    create_runtime_entry_service_provider,
    register_entry_tools,
)
from track_anywhere.mcp.server import ChatGptFastMCP


BOOK_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_BOOK_ID = UUID("00000000-0000-4000-8000-000000000002")
ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000010")
OTHER_ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000011")
ORIGINAL_ID = UUID("00000000-0000-4000-8000-000000000030")
INTENT_ID = UUID("00000000-0000-4000-8000-000000000040")
REQUEST_ID = UUID("00000000-0000-4000-8000-000000000041")
TRANSACTION_ID = UUID("00000000-0000-4000-8000-000000000042")
OCCURRED_AT = "2026-07-24T12:00:00+00:00"
COMMIT_TOKEN = "ready-token-" + "x" * 48

PREPARE_TOOL_NAMES = {
    "ledger_prepare_expense",
    "ledger_prepare_income",
    "ledger_prepare_transfer",
    "ledger_prepare_credit_card_payment",
    "ledger_prepare_fx_credit_card_payment",
    "ledger_prepare_refund",
    "ledger_prepare_adjustment",
}
ENTRY_TOOL_NAMES = PREPARE_TOOL_NAMES | {"ledger_commit_entry"}
FORBIDDEN_INPUT_FIELDS = {
    "debit",
    "credit",
    "posting",
    "posting_id",
    "internal_account",
    "internal_account_id",
    "category_version",
    "category_version_id",
    "ledger_units",
    "units",
    "request_id",
    "commit_token",
}


@dataclass
class FakeEntryService:
    status: PreparedEntryStatus = PreparedEntryStatus.READY
    calls: list[tuple[UUID, EverydayEntryInput]] = field(default_factory=list)
    commit_calls: list[tuple[UUID, CommitEntryInput]] = field(default_factory=list)
    unexpected_error: Exception | None = None
    commit_error: Exception | None = None

    def prepare(
        self,
        *,
        book_id: UUID,
        entry: EverydayEntryInput,
    ) -> PreparedEntry:
        self.calls.append((book_id, entry))
        if self.unexpected_error is not None:
            raise self.unexpected_error
        clarification = (
            (
                Clarification(
                    code=(
                        ClarificationCode.DUPLICATE_CONFIRMATION
                        if self.status is PreparedEntryStatus.DUPLICATE_SUSPECTED
                        else ClarificationCode.ACCOUNT_SELECTION
                    ),
                    field="source_account",
                    prompt="Choose how to continue.",
                ),
            )
            if self.status
            in {
                PreparedEntryStatus.NEEDS_CLARIFICATION,
                PreparedEntryStatus.DUPLICATE_SUSPECTED,
            }
            else ()
        )
        return PreparedEntry(
            intent_id=INTENT_ID,
            status=self.status,
            commit_token=(
                COMMIT_TOKEN if self.status is PreparedEntryStatus.READY else None
            ),
            expires_at=datetime(2026, 7, 24, 12, 10, tzinfo=UTC),
            preview=EntryPreview(
                kind=entry.kind,
                summary=f"Preview {entry.kind}",
                amount=PreviewMoney(
                    value="660",
                    asset_code="CNY",
                    display="CNY 660.00",
                ),
                occurred_at=entry.occurred_at,
            ),
            resolved=ResolvedEntryReferences(),
            clarifications=clarification,
        )

    def commit(
        self,
        *,
        book_id: UUID,
        command: CommitEntryInput,
    ) -> CommittedEntry:
        self.commit_calls.append((book_id, command))
        if self.commit_error is not None:
            raise self.commit_error
        return CommittedEntry(
            intent_id=command.intent_id,
            request_id=command.request_id,
            transaction_id=TRANSACTION_ID,
            committed_at=datetime(2026, 7, 24, 12, 1, tzinfo=UTC),
            preview=EntryPreview(
                kind="expense",
                summary="Preview expense",
                amount=PreviewMoney(
                    value="660",
                    asset_code="CNY",
                    display="CNY 660.00",
                ),
                occurred_at=datetime.fromisoformat(OCCURRED_AT),
            ),
        )


@dataclass
class FakeEntryServiceProvider:
    service: FakeEntryService
    calls: list[tuple[AccessToken, UUID]] = field(default_factory=list)

    def __call__(
        self,
        token: AccessToken,
        book_id: UUID,
    ) -> FakeEntryService:
        self.calls.append((token, book_id))
        return self.service


def _token(
    *,
    scopes: list[str] | None = None,
    restricted_book_id: UUID | None = None,
) -> AccessToken:
    return AccessToken(
        token="opaque-test-token",
        client_id="mcp-entry-contract",
        scopes=scopes or ["ledger:read", "ledger:write"],
        subject="human:mcp-entry",
        claims={
            "actor_type": "human",
            "book_id": (
                None if restricted_book_id is None else str(restricted_book_id)
            ),
        },
    )


def _server(
    service: FakeEntryService | None = None,
) -> tuple[ChatGptFastMCP, FakeEntryService, FakeEntryServiceProvider]:
    selected = service or FakeEntryService()
    provider = FakeEntryServiceProvider(selected)
    server = ChatGptFastMCP("Everyday Entry")
    server.scope_resource_metadata_url = (
        "http://testserver/.well-known/oauth-protected-resource/mcp"
    )
    register_entry_tools(server, provider)
    return server, selected, provider


def _call(
    server: ChatGptFastMCP,
    name: str,
    arguments: dict[str, object],
):
    return asyncio.run(server.call_tool(name, arguments))


def _property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        for child in value.values():
            names.update(_property_names(child))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for child in value:
            names.update(_property_names(child))
        return names
    return set()


def _money(value: str = "660", source_text: str = "660") -> dict[str, str]:
    return {
        "value": value,
        "denomination": "asset_unit",
        "asset_code": "CNY",
        "source_text": source_text,
    }


def _account(account_id: UUID = ACCOUNT_ID) -> dict[str, str]:
    return {"account_id": str(account_id)}


def _category() -> dict[str, list[str]]:
    return {"path": ["食品", "饮料"]}


def _arguments_by_tool() -> dict[str, dict[str, object]]:
    common = {"book_id": str(BOOK_ID), "occurred_at": OCCURRED_AT}
    return {
        "ledger_prepare_expense": {
            **common,
            "amount": _money(),
            "source_account": _account(),
            "category": _category(),
        },
        "ledger_prepare_income": {
            **common,
            "amount": _money("12000", "12000"),
            "destination_account": _account(),
            "category": {"query": "工资"},
        },
        "ledger_prepare_transfer": {
            **common,
            "amount": _money("1000", "1000"),
            "source_account": _account(),
            "destination_account": _account(OTHER_ACCOUNT_ID),
        },
        "ledger_prepare_credit_card_payment": {
            **common,
            "amount": _money("2000", "2000"),
            "funding_account": _account(),
            "card_account": _account(OTHER_ACCOUNT_ID),
        },
        "ledger_prepare_fx_credit_card_payment": {
            **common,
            "target_amount": {
                "value": "2.06",
                "denomination": "asset_unit",
                "asset_code": "USD",
                "source_text": "$2.06",
            },
            "source_amount": _money("13.88", "¥13.88"),
            "fee_amount": _money("0.10", "¥0.10"),
            "funding_account": _account(),
            "card_account": _account(OTHER_ACCOUNT_ID),
            "fee_category": {"path": ["金融", "手续费"]},
        },
        "ledger_prepare_refund": {
            **common,
            "original_transaction_id": str(ORIGINAL_ID),
        },
        "ledger_prepare_adjustment": {
            **common,
            "account": _account(),
            "actual_balance": _money("1250.60", "1250.60"),
        },
    }


def test_entry_descriptors_keep_prepare_friendly_and_commit_narrow() -> None:
    server, _, _ = _server()

    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == ENTRY_TOOL_NAMES
    for tool_name in PREPARE_TOOL_NAMES:
        tool = by_name[tool_name]
        assert tool.title
        assert tool.description.startswith("Use this when")
        assert "explicit confirmation" in tool.description
        assert "ledger_commit_entry" in tool.description
        assert "`660` with asset_unit means 660.00, never 6.60" in tool.description
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is False
        assert tool.annotations.openWorldHint is False
        assert tool.meta["securitySchemes"] == [
            {"type": "oauth2", "scopes": ["ledger:read", "ledger:write"]}
        ]
        assert tool.model_extra["securitySchemes"] == tool.meta["securitySchemes"]
        assert tool.meta["track_anywhere/mode"] == "entry_prepare"
        assert tool.meta["track_anywhere/requires_write"] is True
        assert _property_names(tool.inputSchema).isdisjoint(FORBIDDEN_INPUT_FIELDS)
        assert "book_id" in tool.inputSchema["required"]
        assert "occurred_at" in tool.inputSchema["required"]
        assert tool.outputSchema is not None
        assert "commit_token" in _property_names(tool.outputSchema)

    commit = by_name["ledger_commit_entry"]
    assert commit.title
    assert commit.description.startswith("Use this only after")
    assert "explicitly confirmed" in commit.description
    assert "same request_id" in commit.description
    assert set(commit.inputSchema["properties"]) == {
        "book_id",
        "intent_id",
        "commit_token",
        "request_id",
    }
    assert set(commit.inputSchema["required"]) == {
        "book_id",
        "intent_id",
        "commit_token",
        "request_id",
    }
    assert commit.annotations.readOnlyHint is False
    assert commit.annotations.destructiveHint is True
    assert commit.annotations.idempotentHint is True
    assert commit.annotations.openWorldHint is False
    assert commit.meta["track_anywhere/mode"] == "entry_commit"
    assert commit.meta["track_anywhere/requires_write"] is True
    assert commit.meta["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["ledger:read", "ledger:write"]}
    ]

    assert (
        "category" not in by_name["ledger_prepare_transfer"].inputSchema["properties"]
    )
    assert (
        "category"
        not in by_name["ledger_prepare_credit_card_payment"].inputSchema["properties"]
    )
    fx_payment = by_name["ledger_prepare_fx_credit_card_payment"].inputSchema
    assert {"target_amount", "source_amount", "fee_amount", "fee_category"} <= set(
        fx_payment["required"]
    )
    assert "amount" not in fx_payment["properties"]
    assert (
        "actual_balance" in by_name["ledger_prepare_adjustment"].inputSchema["required"]
    )


def test_runtime_provider_reuses_book_guard_and_injects_actor_scoped_dependencies(
    monkeypatch,
) -> None:
    session = object()
    uow_factory = object()
    ledger_committer = object()
    calls: list[tuple[object, AccessToken, UUID]] = []
    token = _token()
    dependencies = SimpleNamespace(
        session_factory=lambda: nullcontext(session),
        uow_factory=uow_factory,
        ledger_committer=ledger_committer,
        protected_content_cipher=None,
        duplicate_detection_key_provider=None,
    )
    monkeypatch.setattr(
        entry_tools,
        "require_book_write_access",
        lambda actual_session, actual_token, book_id: calls.append(
            (actual_session, actual_token, book_id)
        ),
    )

    provider = create_runtime_entry_service_provider(dependencies)
    service = provider(token, BOOK_ID)

    assert calls == [(session, token, BOOK_ID)]
    assert service.actor.subject_id == token.subject
    assert service.uow_factory is uow_factory
    assert service.ledger_committer is ledger_committer
    assert service.protected_content_service is None
    assert service.duplicate_key_provider is None


def test_runtime_provider_construction_does_not_require_entry_secrets() -> None:
    provider = create_runtime_entry_service_provider(
        SimpleNamespace(session_factory=lambda: None)
    )

    assert callable(provider)


@pytest.mark.parametrize(
    ("tool_name", "expected_kind"),
    [
        ("ledger_prepare_expense", "expense"),
        ("ledger_prepare_income", "income"),
        ("ledger_prepare_transfer", "transfer"),
        ("ledger_prepare_credit_card_payment", "credit_card_payment"),
        ("ledger_prepare_fx_credit_card_payment", "credit_card_payment"),
        ("ledger_prepare_refund", "refund"),
        ("ledger_prepare_adjustment", "adjustment"),
    ],
)
def test_each_prepare_tool_delegates_only_to_the_shared_prepare_service(
    monkeypatch,
    tool_name: str,
    expected_kind: str,
) -> None:
    server, service, provider = _server()
    token = _token()
    monkeypatch.setattr(entry_tools, "require_write_access_token", lambda: token)

    _, structured = _call(server, tool_name, _arguments_by_tool()[tool_name])

    assert structured["status"] == "ready"
    assert structured["mode"] == "prepare"
    assert structured["commit_token"] == COMMIT_TOKEN
    assert structured["preview"]["kind"] == expected_kind
    assert structured["preview"]["amount"]["display"] == "CNY 660.00"
    assert len(service.calls) == 1
    assert service.calls[0][0] == BOOK_ID
    assert service.calls[0][1].kind == expected_kind
    assert provider.calls == [(token, BOOK_ID)]
    assert service.commit_calls == []


@pytest.mark.parametrize(
    "status",
    [
        PreparedEntryStatus.READY,
        PreparedEntryStatus.NEEDS_CLARIFICATION,
        PreparedEntryStatus.DUPLICATE_SUSPECTED,
        PreparedEntryStatus.UNSUPPORTED,
    ],
)
def test_prepare_tools_return_every_status_and_only_ready_exposes_token(
    monkeypatch,
    status: PreparedEntryStatus,
) -> None:
    server, service, _ = _server(FakeEntryService(status=status))
    monkeypatch.setattr(
        entry_tools,
        "require_write_access_token",
        lambda: _token(),
    )

    _, structured = _call(
        server,
        "ledger_prepare_expense",
        _arguments_by_tool()["ledger_prepare_expense"],
    )

    assert structured["status"] == status.value
    assert structured["preview"]["kind"] == "expense"
    assert structured.get("commit_token") == (
        COMMIT_TOKEN if status is PreparedEntryStatus.READY else None
    )
    assert service.commit_calls == []


def test_commit_delegates_exact_capability_and_idempotency_key(
    monkeypatch,
) -> None:
    server, service, provider = _server()
    token = _token()
    monkeypatch.setattr(entry_tools, "require_write_access_token", lambda: token)

    _, structured = _call(
        server,
        "ledger_commit_entry",
        {
            "book_id": str(BOOK_ID),
            "intent_id": str(INTENT_ID),
            "commit_token": COMMIT_TOKEN,
            "request_id": str(REQUEST_ID),
        },
    )

    assert structured["status"] == "committed"
    assert structured["intent_id"] == str(INTENT_ID)
    assert structured["request_id"] == str(REQUEST_ID)
    assert structured["transaction_id"] == str(TRANSACTION_ID)
    assert provider.calls == [(token, BOOK_ID)]
    assert service.commit_calls == [
        (
            BOOK_ID,
            CommitEntryInput(
                intent_id=INTENT_ID,
                commit_token=COMMIT_TOKEN,
                request_id=REQUEST_ID,
            ),
        )
    ]
    assert service.calls == []


def test_prepare_requires_write_scope_and_preserves_oauth_challenge() -> None:
    server, service, provider = _server()
    context = auth_context_var.set(AuthenticatedUser(_token(scopes=["ledger:read"])))
    try:
        result = _call(
            server,
            "ledger_prepare_expense",
            _arguments_by_tool()["ledger_prepare_expense"],
        )
    finally:
        auth_context_var.reset(context)

    assert result.isError is True
    challenge = result.meta["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="ledger:read ledger:write"' in challenge
    assert service.calls == []
    assert provider.calls == []


def test_book_bound_token_cannot_prepare_for_another_book(monkeypatch) -> None:
    server, service, provider = _server()
    monkeypatch.setattr(
        entry_tools,
        "require_write_access_token",
        lambda: _token(restricted_book_id=OTHER_BOOK_ID),
    )

    with pytest.raises(ToolError, match="restricted to a different Book"):
        _call(
            server,
            "ledger_prepare_expense",
            _arguments_by_tool()["ledger_prepare_expense"],
        )

    assert service.calls == []
    assert provider.calls == []


def test_private_narrative_never_enters_repr_logs_or_unexpected_errors(
    monkeypatch,
    caplog,
) -> None:
    secret = "merchant-secret-order-9081726354"
    server, service, _ = _server(
        FakeEntryService(unexpected_error=RuntimeError(secret))
    )
    monkeypatch.setattr(
        entry_tools,
        "require_write_access_token",
        lambda: _token(),
    )
    arguments = _arguments_by_tool()["ledger_prepare_expense"]
    arguments["amount"] = _money(source_text=secret)
    arguments["narrative"] = {
        "merchant": secret,
        "note": secret,
        "external_reference": {
            "provider_code": "merchant",
            "kind": "provider_order",
            "reference": "order-secret-9081726354",
        },
    }

    with pytest.raises(ToolError) as raised:
        _call(server, "ledger_prepare_expense", arguments)

    assert "failed unexpectedly" in str(raised.value)
    assert secret not in str(raised.value)
    assert secret not in repr(service.calls)
    assert secret not in caplog.text
    assert service.commit_calls == []


def test_safe_application_errors_keep_stable_code_and_field(monkeypatch) -> None:
    server, service, _ = _server(
        FakeEntryService(
            unexpected_error=EntryGatewayError(
                EntryErrorCode.ACCOUNT_NOT_FOUND,
                "account was not found in the requested Book",
                field="source_account",
            )
        )
    )
    monkeypatch.setattr(
        entry_tools,
        "require_write_access_token",
        lambda: _token(),
    )

    with pytest.raises(ToolError) as raised:
        _call(
            server,
            "ledger_prepare_expense",
            _arguments_by_tool()["ledger_prepare_expense"],
        )

    assert "entry_account_not_found" in str(raised.value)
    assert "field=source_account" in str(raised.value)
    assert service.commit_calls == []


def test_commit_requires_write_scope_and_preserves_oauth_challenge() -> None:
    server, service, provider = _server()
    context = auth_context_var.set(AuthenticatedUser(_token(scopes=["ledger:read"])))
    try:
        result = _call(
            server,
            "ledger_commit_entry",
            {
                "book_id": str(BOOK_ID),
                "intent_id": str(INTENT_ID),
                "commit_token": COMMIT_TOKEN,
                "request_id": str(REQUEST_ID),
            },
        )
    finally:
        auth_context_var.reset(context)

    assert result.isError is True
    assert 'error="insufficient_scope"' in (result.meta["mcp/www_authenticate"][0])
    assert service.commit_calls == []
    assert provider.calls == []


def test_commit_unexpected_error_hides_token_and_preserves_retry_instruction(
    monkeypatch,
    caplog,
) -> None:
    secret = "commit-secret-token-" + "9" * 32
    server, service, _ = _server(FakeEntryService(commit_error=RuntimeError(secret)))
    monkeypatch.setattr(
        entry_tools,
        "require_write_access_token",
        lambda: _token(),
    )

    with pytest.raises(ToolError) as raised:
        _call(
            server,
            "ledger_commit_entry",
            {
                "book_id": str(BOOK_ID),
                "intent_id": str(INTENT_ID),
                "commit_token": secret,
                "request_id": str(REQUEST_ID),
            },
        )

    assert "Retry with the same request_id" in str(raised.value)
    assert secret not in str(raised.value)
    assert secret not in repr(service.commit_calls)
    assert secret not in caplog.text


@pytest.mark.parametrize(
    "tool_name",
    [
        "ledger_cancel_entry",
        "ledger_record_expense",
        "ledger_record_credit_card_charge",
    ],
)
def test_commit_cancel_and_legacy_writes_are_unknown(
    tool_name: str,
) -> None:
    server, _, _ = _server()

    with pytest.raises(ToolError, match=f"Unknown tool: {tool_name}"):
        _call(server, tool_name, {})
