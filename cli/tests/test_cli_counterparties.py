from __future__ import annotations

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_counterparty_commands_and_expense_options_use_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "POST" and path == "/api/v1/counterparties/ensure":
            return 200, {"counterparty": {"counterparty_id": "cp_meituan", **payload}}
        if method == "POST" and path == "/api/v1/counterparties":
            return 200, {"counterparty": {"counterparty_id": "cp_starbucks", **payload}}
        if method == "POST":
            return 200, {"transaction": {"transaction_id": "txn_1", "lines": []}}
        if path.endswith("/meituan"):
            return 200, {"counterparty": {"counterparty_id": "cp_meituan", "slug": "meituan"}}
        return 200, {"counterparties": []} if path.startswith("/api/v1/counterparties") else {"transactions": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "counterparty",
                "ensure",
                "美团",
                "--kind",
                "merchant",
                "--idempotency-key",
                "counterparty-ensure",
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
                "counterparty",
                "create",
                "星巴克",
                "--idempotency-key",
                "counterparty-create",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "counterparty", "list", "--kind", "merchant", "--json"]) == 0
    assert main(["--token", "token-1", "counterparty", "show", "meituan", "--json"]) == 0
    assert (
        main(
            [
                "--token",
                "token-1",
                "expense",
                "record",
                "--amount",
                "12.30",
                "--from",
                "acc_cash",
                "--category-id",
                "cat_food",
                "--purpose",
                "午餐",
                "--counterparty",
                "meituan",
                "--idempotency-key",
                "expense-counterparty",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "tx", "list", "--counterparty", "meituan", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/counterparties/ensure",
            "payload": {"name": "美团", "kind": "merchant"},
            "key": "counterparty-ensure",
        },
        {
            "method": "POST",
            "path": "/api/v1/counterparties",
            "payload": {"name": "星巴克", "kind": "merchant"},
            "key": "counterparty-create",
        },
        {
            "method": "GET",
            "path": "/api/v1/counterparties?kind=merchant&status=active",
            "payload": None,
            "key": None,
        },
        {"method": "GET", "path": "/api/v1/counterparties/meituan", "payload": None, "key": None},
        {
            "method": "POST",
            "path": "/api/v1/expenses",
            "payload": {
                "amount": "12.30",
                "currency": "CNY",
                "category_id": "cat_food",
                "purpose": "午餐",
                "counterparty": "meituan",
                "from_account_id": "acc_cash",
            },
            "key": "expense-counterparty",
        },
        {
            "method": "GET",
            "path": "/api/v1/ledger/transactions?counterparty=meituan&limit=20",
            "payload": None,
            "key": None,
        },
    ]
