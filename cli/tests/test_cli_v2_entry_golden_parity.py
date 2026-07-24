from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import pytest
from click.testing import CliRunner
from pydantic import TypeAdapter

from backend.tests.v2.fixtures.everyday_entries import (
    BOOK_ID,
    OCCURRED_AT,
    GoldenEntryScenario,
    golden_scenarios,
)
from track_anywhere.application.entries.contracts import EverydayEntryInput
from track_anywhere_cli.click_app import cli


def _ready(scenario: GoldenEntryScenario) -> dict[str, Any]:
    return {
        "intent_id": "20000000-0000-4000-8000-000000000001",
        "status": "ready",
        "commit_token": "golden-cli-token-" + "x" * 32,
        "expires_at": "2026-07-24T13:00:00Z",
        "preview": {
            "kind": scenario.entry.kind,
            "summary": f"Golden {scenario.name}",
            "amount": {
                "value": scenario.expected_value,
                "asset_code": "CNY",
                "display": f"{scenario.expected_value} CNY",
            },
            "occurred_at": OCCURRED_AT.isoformat().replace("+00:00", "Z"),
            "accounts": [],
            "category_paths": [],
        },
        "resolved": {
            "source_account_id": None,
            "destination_account_id": None,
            "funding_account_id": None,
            "card_account_id": None,
            "adjusted_account_id": None,
            "category_ids": [],
            "category_version_ids": [],
            "original_transaction_id": None,
        },
        "warnings": [],
        "clarifications": [],
    }


@pytest.mark.parametrize(
    "scenario",
    tuple(item for item in golden_scenarios() if item.cli_argv),
    ids=lambda scenario: scenario.name,
)
def test_cli_prepare_and_commit_use_shared_entry_and_receipt_contracts(
    monkeypatch: pytest.MonkeyPatch,
    scenario: GoldenEntryScenario,
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    prepared = _ready(scenario)
    committed = {
        "status": "committed",
        "intent_id": prepared["intent_id"],
        "request_id": "20000000-0000-4000-8000-000000000002",
        "transaction_id": "20000000-0000-4000-8000-000000000003",
        "committed_at": "2026-07-24T12:31:00Z",
        "replayed": False,
        "preview": copy.deepcopy(prepared["preview"]),
    }

    def requester(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return (200, prepared) if len(calls) == 1 else (201, committed)

    monkeypatch.setattr(
        "track_anywhere_cli.click_entries.new_request_id",
        lambda: committed["request_id"],
    )
    result = CliRunner().invoke(
        cli,
        [
            "--token",
            "golden-token",
            *scenario.cli_argv,
            "--asset-code",
            "CNY",
            "--at",
            OCCURRED_AT.isoformat().replace("+00:00", "Z"),
            "--book-id",
            str(BOOK_ID),
            "--yes",
            "--json",
        ],
        obj={"requester": requester},
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    prepare_call, commit_call = calls
    assert prepare_call[:2] == (
        "POST",
        f"/api/v2/books/{BOOK_ID}/entries/prepare",
    )
    parsed = TypeAdapter(EverydayEntryInput).validate_python(prepare_call[2])
    assert parsed == scenario.entry
    assert commit_call[:2] == (
        "POST",
        f"/api/v2/books/{BOOK_ID}/entries/commit",
    )
    assert commit_call[2] == {
        "intent_id": prepared["intent_id"],
        "commit_token": prepared["commit_token"],
        "request_id": committed["request_id"],
    }
    assert commit_call[3] == committed["request_id"]
