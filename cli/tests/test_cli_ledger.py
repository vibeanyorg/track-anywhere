from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import EXIT_NOT_FOUND, main


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def test_tx_record_posts_agent_friendly_payload(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"transaction": {"transaction_id": "txn_1", "purpose": payload["purpose"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(
        [
            "--token",
            "token-1",
            "tx",
            "record",
            "--amount",
            "38",
            "--from",
            "acc_cash",
            "--to",
            "acc_food",
            "--purpose",
            "lunch",
            "--memo",
            "Lunch with Alice, card ending 1234",
            "--occurred-at",
            "2026-05-16T12:30:00+08:00",
            "--json",
        ]
    )

    assert exit_code == 0
    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/ledger/transactions",
            "payload": {
                "amount": "38",
                "currency": "CNY",
                "from_account_id": "acc_cash",
                "to_account_id": "acc_food",
                "purpose": "lunch",
                "memo": "Lunch with Alice, card ending 1234",
                "occurred_at": "2026-05-16T12:30:00+08:00",
            },
            "key": calls[0]["key"],
            "token": "token-1",
        }
    ]
    assert calls[0]["key"].startswith("tx-record-")
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is True
    assert payload["command"] == "tx.record"
    assert payload["data"]["transaction"]["purpose"] == "lunch"


def test_tx_show_not_found_emits_error_outcome_with_404(monkeypatch, capsys):
    def fake_request(config, method, path, payload=None, key=None):
        assert method == "GET"
        assert path == "/api/v1/ledger/transactions/txn_missing"
        return 404, {"detail": "missing"}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(["--token", "token-1", "tx", "show", "txn_missing", "--json"])

    payload = _json_from_output(capsys.readouterr())
    assert exit_code == EXIT_NOT_FOUND
    assert payload["ok"] is False
    assert payload["status"] == 404
    assert payload["command"] == "tx.show"
    assert payload["data"]["detail"] == "missing"
    assert payload["diagnostics"][0]["code"] == "not_found"


def test_expense_and_income_record_commands_post_category_transactions(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transaction": {"transaction_id": "txn_1", "category_id": payload["category_id"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "expense",
                "record",
                "--amount",
                "38",
                "--from",
                "acc_cash",
                "--category-id",
                "cat_food",
                "--purpose",
                "lunch",
                "--occurred-at",
                "2026-05-16T12:30:00+08:00",
                "--idempotency-key",
                "expense-lunch",
                "--json",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--token",
                "token-1",
                "income",
                "record",
                "--amount",
                "100",
                "--to",
                "acc_cash",
                "--category-id",
                "cat_salary",
                "--purpose",
                "salary",
                "--idempotency-key",
                "income-salary",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/expenses",
            "payload": {
                "amount": "38",
                "currency": "CNY",
                "from_account_id": "acc_cash",
                "category_id": "cat_food",
                "purpose": "lunch",
                "occurred_at": "2026-05-16T12:30:00+08:00",
            },
            "key": "expense-lunch",
        },
        {
            "method": "POST",
            "path": "/api/v1/incomes",
            "payload": {
                "amount": "100",
                "currency": "CNY",
                "to_account_id": "acc_cash",
                "category_id": "cat_salary",
                "purpose": "salary",
            },
            "key": "income-salary",
        },
    ]


def test_transaction_read_commands_use_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transactions": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "tx", "list", "--account-id", "acc_1", "--limit", "5", "--json"]) == 0
    assert main(["--token", "token-1", "tx", "show", "txn_1", "--json"]) == 0

    assert calls == [
        {"method": "GET", "path": "/api/v1/ledger/transactions?account_id=acc_1&limit=5", "payload": None, "key": None},
        {"method": "GET", "path": "/api/v1/ledger/transactions/txn_1", "payload": None, "key": None},
    ]


def test_tx_reverse_posts_reversal_command(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transaction": {"transaction_id": "txn_reversal"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "tx",
                "reverse",
                "txn_1",
                "--memo",
                "duplicate transaction",
                "--idempotency-key",
                "reverse-1",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/ledger/reverse",
            "payload": {"transaction_id": "txn_1", "memo": "duplicate transaction"},
            "key": "reverse-1",
        }
    ]


def test_account_adjust_posts_balance_delta(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transaction": {"transaction_id": "txn_adjust"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "adjust",
                "acc_cash",
                "--amount",
                "-10",
                "--purpose",
                "cash correction",
                "--idempotency-key",
                "adj-1",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/ledger/adjustments",
            "payload": {
                "account_id": "acc_cash",
                "amount": "-10",
                "currency": "CNY",
                "purpose": "cash correction",
            },
            "key": "adj-1",
        }
    ]


def test_capture_dry_run_uses_capture_presenter(capsys):
    exit_code = main(["capture", "spent 38", "--dry-run"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Capture response" in output
    assert "No human presenter registered" not in output
    assert not output.lstrip().startswith("{")
