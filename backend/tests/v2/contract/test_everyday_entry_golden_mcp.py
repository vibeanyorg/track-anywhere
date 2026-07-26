from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from mcp.server.auth.provider import AccessToken

from backend.tests.v2.fixtures.everyday_entries import (
    BOOK_ID,
    GoldenEntryScenario,
    golden_context,
    golden_scenarios,
)
from track_anywhere.application.entries.compiler import compile_entry
from track_anywhere.application.entries.contracts import (
    CommitEntryInput,
    CommittedEntry,
    EverydayEntryInput,
    PreparedEntry,
    PreparedEntryStatus,
)
from track_anywhere.application.entries.prepare import preview_and_resolved
from track_anywhere.mcp import entry_tools
from track_anywhere.mcp.entry_tools import register_entry_prepare_tools
from track_anywhere.mcp.server import ChatGptFastMCP


@dataclass
class CompilerPreviewService:
    calls: list[EverydayEntryInput] = field(default_factory=list)
    commit_calls: int = 0

    def prepare(
        self,
        *,
        book_id: UUID,
        entry: EverydayEntryInput,
    ) -> PreparedEntry:
        assert book_id == BOOK_ID
        self.calls.append(entry)
        context = golden_context()
        plan = compile_entry(entry, context=context)
        preview, resolved = preview_and_resolved(
            entry,
            context=context,
            plan=plan,
        )
        return PreparedEntry(
            intent_id=context.command_id,
            status=PreparedEntryStatus.READY,
            commit_token="golden-ready-token-" + "x" * 32,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            preview=preview,
            resolved=resolved,
        )

    def commit(
        self,
        *,
        book_id: UUID,
        command: CommitEntryInput,
    ) -> CommittedEntry:
        self.commit_calls += 1
        raise AssertionError("Prepare tools must never commit")


def _token() -> AccessToken:
    return AccessToken(
        token="opaque-golden-token",
        client_id="golden-mcp",
        scopes=["ledger:read", "ledger:write"],
        subject="human:everyday-golden",
        claims={"actor_type": "human", "book_id": str(BOOK_ID)},
    )


@pytest.mark.parametrize(
    "scenario",
    golden_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_mcp_prepare_matches_shared_compiler_and_json_contract(
    monkeypatch: pytest.MonkeyPatch,
    scenario: GoldenEntryScenario,
) -> None:
    service = CompilerPreviewService()
    server = ChatGptFastMCP("Everyday Golden MCP")
    server.scope_resource_metadata_url = (
        "http://testserver/.well-known/oauth-protected-resource/mcp"
    )
    register_entry_prepare_tools(server, lambda _token, _book_id: service)
    monkeypatch.setattr(entry_tools, "require_write_access_token", _token)
    arguments = scenario.entry.model_dump(mode="json")
    arguments.pop("kind")
    arguments["book_id"] = str(BOOK_ID)

    _, structured = asyncio.run(server.call_tool(scenario.mcp_tool, arguments))

    assert structured["mode"] == "prepare"
    assert structured["status"] == "ready"
    assert structured["preview"]["kind"] == scenario.entry.kind
    assert structured["preview"]["amount"] == {
        "value": scenario.expected_value,
        "asset_code": "CNY",
        "display": f"{scenario.expected_value} CNY",
    }
    assert tuple(structured["resolved"]["category_ids"]) == tuple(
        str(value) for value in scenario.expected_categories
    )
    assert structured["commit_token"] == "golden-ready-token-" + "x" * 32
    assert service.calls == [scenario.entry]
    assert service.commit_calls == 0
