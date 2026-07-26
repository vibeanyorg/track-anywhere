from __future__ import annotations

import copy
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from track_anywhere_cli.click_app import cli as root_cli
from track_anywhere_cli.click_common import ClickState
from track_anywhere_cli.click_entries import register
from track_anywhere_cli.command_entries import ENTRY_COMMAND_PATHS
from track_anywhere_cli.commands import command_definitions, infer_command_path
from track_anywhere_cli.protocol import command_schema, supports_payload


BOOK = "11111111-1111-4111-8111-111111111111"
REQUEST = "22222222-2222-4222-8222-222222222222"
CONTRACTS = json.loads(
    Path("backend/tests/v2/fixtures/everyday_entry_contracts.json").read_text(
        encoding="utf-8"
    )
)


def _cli() -> click.Group:
    @click.group()
    def root() -> None:
        pass

    register(root)
    return root


def _state(requester, *, no_input: bool = False) -> ClickState:
    return ClickState(
        base_url="http://testserver",
        token="token",
        insecure_automation=False,
        json_mode=False,
        no_color=True,
        requester=requester,
        no_input=no_input,
    )


def _prepared(
    *,
    status: str = "ready",
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(CONTRACTS["ready_prepared_entry"])
    payload["status"] = status
    payload["warnings"] = warnings or []
    if status != "ready":
        payload["commit_token"] = None
        payload["clarifications"] = [
            {
                "code": (
                    "duplicate_confirmation"
                    if status == "duplicate_suspected"
                    else "unsupported_detail"
                    if status == "unsupported"
                    else "account_selection"
                ),
                "field": "source_account",
                "prompt": "Choose how to continue.",
                "choices": [],
            }
        ]
    return payload


def _recorder(
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]],
    *,
    prepared: dict[str, Any] | None = None,
    commit_status: int = 201,
    committed: dict[str, Any] | None = None,
):
    responses = [
        (200, prepared or _prepared()),
        (
            commit_status,
            committed
            or {
                "status": "committed",
                "transaction_id": "33333333-3333-4333-8333-333333333333",
            },
        ),
    ]

    def request(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return responses[len(calls) - 1]

    return request


def test_root_cli_registers_friendly_entries_without_removing_raw_commands():
    assert set(ENTRY_COMMAND_PATHS) == {
        "expense",
        "income",
        "transfer",
        "card_pay",
        "refund",
        "reconcile",
    }
    assert {
        "expense",
        "income",
        "transfer",
        "card-pay",
        "refund",
        "reconcile",
        "tx",
        "card",
    } <= set(root_cli.commands)

    definitions = command_definitions()
    for path in ENTRY_COMMAND_PATHS:
        definition = definitions[path]
        assert definition.requires_auth is True
        assert definition.mutating is True
        assert definition.idempotent is False
        assert infer_command_path(Namespace(command=path)) == path
    assert {"tx.record", "card.charge"} <= definitions.keys()


def test_capability_and_schema_publish_all_friendly_entry_commands():
    supports = supports_payload()
    assert set(ENTRY_COMMAND_PATHS) <= set(supports["dry_run_commands"])

    for path in ENTRY_COMMAND_PATHS:
        schema = command_schema(root_cli, path)
        assert schema["command"] == [
            "ta",
            path.replace("_", "-"),
        ]
        assert schema["requires_auth"] is True
        assert schema["supports_dry_run"] is True
        assert schema["side_effects"] == [f"mutates:{path}"]


def test_registered_root_command_emits_json_without_prompting():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        root_cli,
        [
            "--token",
            "token",
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj={"requester": _recorder(calls)},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["status"] == "ready"
    assert len(calls) == 1
    assert "Commit this entry?" not in result.output


def test_friendly_cli_accepts_payment_instruments_without_account_details():
    expense_calls: list[
        tuple[str, str, dict[str, Any] | None, str | None]
    ] = []
    expense = CliRunner().invoke(
        root_cli,
        [
            "--token",
            "token",
            "expense",
            "8",
            "--instrument",
            "SafePal USD24",
            "--instrument-provider",
            "safepal",
            "--category",
            "软件订阅/X订阅",
            "--asset-code",
            "USD24",
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj={"requester": _recorder(expense_calls)},
    )
    assert expense.exit_code == 0, expense.output
    payload = expense_calls[0][2]
    assert payload is not None
    assert "source_account" not in payload
    assert payload["payment_instrument"] == {
        "query": "SafePal USD24",
        "provider_code": "safepal",
    }

    payment_calls: list[
        tuple[str, str, dict[str, Any] | None, str | None]
    ] = []
    payment = CliRunner().invoke(
        root_cli,
        [
            "--token",
            "token",
            "card-pay",
            "80",
            "--from",
            "USD24 wallet",
            "--instrument",
            "Provider-neutral statement card",
            "--asset-code",
            "USD24",
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj={"requester": _recorder(payment_calls)},
    )
    assert payment.exit_code == 0, payment.output
    payment_payload = payment_calls[0][2]
    assert payment_payload is not None
    assert "card_account" not in payment_payload
    assert payment_payload["payment_instrument"] == {
        "query": "Provider-neutral statement card"
    }


def test_single_active_book_is_selected_without_requiring_uuid():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    def request(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        if path == "/api/v2/books":
            return 200, {
                "items": [
                    {
                        "book_id": BOOK,
                        "current_name": "我的账本",
                        "base_asset_code": "CNY",
                        "write_state": "active",
                    }
                ]
            }
        return 200, _prepared()

    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--dry-run",
            "--json",
        ],
        obj=_state(request),
    )

    assert result.exit_code == 0, result.output
    assert [call[:2] for call in calls] == [
        ("GET", "/api/v2/books"),
        ("POST", f"/api/v2/books/{BOOK}/entries/prepare"),
    ]


def test_multiple_books_prompt_human_and_require_explicit_agent_selection():
    second = "99999999-9999-4999-8999-999999999999"

    def request(_config, method, path, payload=None, key=None):
        if path == "/api/v2/books":
            return 200, {
                "items": [
                    {
                        "book_id": BOOK,
                        "current_name": "个人",
                        "base_asset_code": "CNY",
                        "write_state": "active",
                    },
                    {
                        "book_id": second,
                        "current_name": "家庭",
                        "base_asset_code": "CNY",
                        "write_state": "active",
                    },
                ]
            }
        assert path == f"/api/v2/books/{second}/entries/prepare"
        return 200, _prepared()

    human = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--dry-run",
        ],
        input="2\n",
        obj=_state(request),
    )
    assert human.exit_code == 0, human.output
    assert "2. 家庭" in human.output
    assert "Category: 食品/外卖" in human.output

    agent = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--dry-run",
            "--json",
            "--no-input",
        ],
        obj=_state(request),
    )
    assert agent.return_value != 0
    assert (
        json.loads(agent.output)["data"]["error"]["code"]
        == "book_selection_required"
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            [
                "expense",
                "53",
                "--from",
                "微信零钱通",
                "--category",
                "食品/外卖",
                "--merchant",
                "外卖",
                "--at",
                "2026-07-24T12:00:00Z",
            ],
            CONTRACTS["entries"][0],
        ),
        (
            [
                "income",
                "12000",
                "--to",
                "工商银行",
                "--to-last4",
                "6184",
                "--to-subtype",
                "debit_card",
                "--category",
                "工资/heyrevia",
                "--at",
                "2026-07-24T12:01:00Z",
            ],
            CONTRACTS["entries"][1],
        ),
        (
            [
                "transfer",
                "1000",
                "--from",
                "中国银行",
                "--from-last4",
                "2950",
                "--to",
                "微信零钱通",
                "--at",
                "2026-07-24T12:02:00Z",
            ],
            CONTRACTS["entries"][2],
        ),
        (
            [
                "card-pay",
                "2000",
                "--from",
                "工商银行",
                "--from-last4",
                "6184",
                "--card",
                "工商银行信用卡",
                "--card-last4",
                "1242",
                "--at",
                "2026-07-24T12:03:00Z",
            ],
            CONTRACTS["entries"][3],
        ),
        (
            [
                "refund",
                "10",
                "--original",
                "00000000-0000-4000-8000-000000000041",
                "--source-text",
                "退款10元",
                "--at",
                "2026-07-24T12:04:00Z",
            ],
            CONTRACTS["entries"][4],
        ),
        (
            [
                "reconcile",
                "微信零钱通",
                "--actual",
                "1250.60",
                "--at",
                "2026-07-24T12:05:00Z",
            ],
            CONTRACTS["entries"][5],
        ),
    ],
)
def test_friendly_commands_prepare_frozen_entry_contracts(argv, expected):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            *argv,
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            f"/api/v2/books/{BOOK}/entries/prepare",
            expected,
            None,
        )
    ]


def test_bare_660_remains_660_asset_units():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "660",
            "--from",
            "微信零钱通",
            "--category",
            "饮料",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:06:00Z",
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    amount = calls[0][2]["amount"]
    assert amount == {
        "value": "660",
        "denomination": "asset_unit",
        "asset_code": "CNY",
        "source_text": "660",
    }


def test_explicit_minor_units_match_frozen_contract():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "660",
            "--from",
            "微信零钱通",
            "--category",
            "饮料",
            "--denomination",
            "minor_unit",
            "--source-text",
            "660分",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:06:00Z",
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert calls[0][2] == CONTRACTS["minor_unit_expense"]


def test_full_refund_does_not_invent_an_amount_or_source_text():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "refund",
            "--original",
            "00000000-0000-4000-8000-000000000041",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:04:00Z",
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    payload = calls[0][2]
    assert payload is not None
    assert "amount" not in payload
    assert "source_text" not in json.dumps(payload)


def test_full_refund_rejects_source_text_that_has_no_amount_path():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "refund",
            "--original",
            "00000000-0000-4000-8000-000000000041",
            "--source-text",
            "full refund screenshot",
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.return_value != 0
    assert len(calls) == 0
    payload = json.loads(result.output)
    assert payload["data"]["error"]["code"] == "invalid_v2_cli_input"


def test_yes_commits_ready_warning_free_intent_with_exact_body_and_key(monkeypatch):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    monkeypatch.setattr(
        "track_anywhere_cli.click_entries.new_request_id",
        lambda: REQUEST,
    )
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:00:00Z",
            "--yes",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert calls[1] == (
        "POST",
        f"/api/v2/books/{BOOK}/entries/commit",
        {
            "intent_id": CONTRACTS["ready_prepared_entry"]["intent_id"],
            "commit_token": CONTRACTS["ready_prepared_entry"]["commit_token"],
            "request_id": REQUEST,
        },
        REQUEST,
    )


def test_interactive_confirmation_displays_preview_before_commit(monkeypatch):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    monkeypatch.setattr(
        "track_anywhere_cli.click_entries.new_request_id",
        lambda: REQUEST,
    )
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:00:00Z",
        ],
        input="y\n",
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert "Preview: 从微信零钱通支出 CNY 53.00，分类为食品/外卖" in result.output
    assert result.output.index("Preview:") < result.output.index("Commit this entry?")
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["--json", "--agent", "--no-input"])
def test_machine_and_no_input_modes_never_implicitly_commit(mode):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--at",
            "2026-07-24T12:00:00Z",
            mode,
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert "Commit this entry?" not in result.output


@pytest.mark.parametrize("mode", ["--agent", "--no-input"])
def test_explicit_yes_commits_without_prompt_in_noninteractive_modes(
    mode,
    monkeypatch,
):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    monkeypatch.setattr(
        "track_anywhere_cli.click_entries.new_request_id",
        lambda: REQUEST,
    )
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--yes",
            mode,
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert calls[1][2] == {
        "intent_id": CONTRACTS["ready_prepared_entry"]["intent_id"],
        "commit_token": CONTRACTS["ready_prepared_entry"]["commit_token"],
        "request_id": REQUEST,
    }
    assert calls[1][3] == REQUEST
    assert "Commit this entry?" not in result.output


def test_dry_run_takes_precedence_over_yes():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--dry-run",
            "--yes",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_human_dry_run_displays_preview_without_prompting():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--dry-run",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    assert "Preview: 从微信零钱通支出 CNY 53.00，分类为食品/外卖" in result.output
    assert "Commit this entry?" not in result.output
    assert len(calls) == 1


def test_yes_does_not_bypass_ready_warnings():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    warning = {
        "code": "unusual_amount",
        "message": "Amount is unusual.",
    }
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--yes",
            "--json",
        ],
        obj=_state(_recorder(calls, prepared=_prepared(warnings=[warning]))),
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    payload = json.loads(result.output)
    assert payload["diagnostics"][-1]["code"] == "automatic_commit_blocked"


@pytest.mark.parametrize(
    "status",
    ["needs_clarification", "duplicate_suspected", "unsupported"],
)
def test_non_ready_prepare_statuses_never_commit(status):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--yes",
            "--json",
        ],
        obj=_state(_recorder(calls, prepared=_prepared(status=status))),
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert json.loads(result.output)["data"]["status"] == status


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (410, "entry_intent_expired"),
        (409, "entry_intent_stale"),
        (503, "entry_commit_outcome_unknown"),
    ],
)
def test_commit_errors_are_returned_without_a_third_request(
    monkeypatch,
    status,
    code,
):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    monkeypatch.setattr(
        "track_anywhere_cli.click_entries.new_request_id",
        lambda: REQUEST,
    )
    error = {
        "detail": "Commit failed.",
        "error": {
            "code": code,
            "category": "conflict",
            "message": "Commit failed.",
            "retryable": status == 503,
        },
    }
    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--yes",
            "--json",
        ],
        obj=_state(
            _recorder(
                calls,
                commit_status=status,
                committed=error,
            )
        ),
    )

    assert result.return_value != 0
    assert len(calls) == 2
    assert json.loads(result.output)["error"]["code"] == code


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (422, "entry_invalid_input"),
        (409, "entry_duplicate_suspected"),
        (503, "entry_commit_outcome_unknown"),
    ],
)
def test_prepare_errors_stop_before_commit(status, code):
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    def requester(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return status, {
            "detail": "Prepare failed.",
            "error": {
                "code": code,
                "category": "usage",
                "message": "Prepare failed.",
                "retryable": status == 503,
            },
        }

    result = CliRunner().invoke(
        _cli(),
        [
            "expense",
            "53",
            "--from",
            "微信零钱通",
            "--category",
            "食品/外卖",
            "--book-id",
            BOOK,
            "--yes",
            "--json",
        ],
        obj=_state(requester),
    )

    assert result.return_value != 0
    assert len(calls) == 1
    assert json.loads(result.output)["error"]["code"] == code


def test_account_ids_and_query_hints_stay_in_account_refs():
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    account_id = "44444444-4444-4444-8444-444444444444"
    result = CliRunner().invoke(
        _cli(),
        [
            "transfer",
            "100",
            "--from",
            f"id:{account_id}",
            "--to",
            "工商银行",
            "--to-last4",
            "6184",
            "--to-subtype",
            "debit_card",
            "--book-id",
            BOOK,
            "--dry-run",
            "--json",
        ],
        obj=_state(_recorder(calls)),
    )

    assert result.exit_code == 0, result.output
    payload = calls[0][2]
    assert payload["source_account"] == {"account_id": account_id}
    assert payload["destination_account"] == {
        "query": "工商银行",
        "last4": "6184",
        "subtype": "debit_card",
    }
