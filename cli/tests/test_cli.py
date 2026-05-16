from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import EXIT_AUTH, build_parser, exit_for_status, main


def test_cli_rejects_env_token_without_insecure_opt_in(monkeypatch):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")
    assert main(["capture", "spent 38", "--idempotency-key", "k"]) == EXIT_AUTH


def test_parser_keeps_json_mode_and_idempotency():
    parser = build_parser()
    args = parser.parse_args(["capture", "spent 38", "--idempotency-key", "cap-1", "--json"])
    assert args.command == "capture"
    assert args.idempotency_key == "cap-1"
    assert args.json is True


def test_exit_code_mapping_for_conflicts():
    assert exit_for_status(409, {"detail": "idempotency key reused"}) == 4
    assert exit_for_status(409, {"detail": "draft version conflict"}) == 5


def test_capture_dry_run_json_contract(capsys):
    exit_code = main(["capture", "spent 38", "--idempotency-key", "snap-1", "--dry-run", "--json"])
    expected = json.loads((Path(__file__).parent / "snapshots" / "capture-dry-run.json").read_text())

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_auth_dev_token_saves_local_token(monkeypatch, capsys):
    saved = {}

    def fake_request(config, method, path, payload=None, key=None):
        assert method == "POST"
        assert path == "/api/v1/auth/dev-token"
        return 200, {"token": "owner-token", "actor": {"actor_id": "owner"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)
    monkeypatch.setattr(cli_main.TokenStore, "save", lambda self, token: saved.setdefault("token", token))

    assert main(["auth", "dev-token", "--json"]) == 0
    assert saved["token"] == "owner-token"
    assert json.loads(capsys.readouterr().out)["token"] == "owner-token"


def test_data_backup_creates_readable_sqlite_copy(tmp_path, capsys):
    database_path = tmp_path / "track-anywhere.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("create table accounts (account_id text primary key, name text)")
        connection.execute("insert into accounts values ('acc_1', 'Cash')")

    exit_code = main(
        [
            "data",
            "backup",
            "--database-url",
            f"sqlite:///{database_path}",
            "--output-dir",
            str(tmp_path / "backups"),
            "--label",
            "before-real-write",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    backup_path = Path(payload["backup"]["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"
    assert "before-real-write" in backup_path.name
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("select name from accounts where account_id = 'acc_1'").fetchone() == ("Cash",)


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
                "occurred_at": "2026-05-16T12:30:00+08:00",
            },
            "key": calls[0]["key"],
            "token": "token-1",
        }
    ]
    assert calls[0]["key"].startswith("tx-record-")
    assert json.loads(capsys.readouterr().out)["transaction"]["purpose"] == "lunch"


def test_category_commands_and_category_summary_use_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "POST":
            return 200, {"category": {"category_id": "cat_1", **payload}}
        return 200, {"categories": [], "groups": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "category",
                "create",
                "--kind",
                "expense",
                "--primary",
                "餐饮",
                "--secondary",
                "外卖",
                "--idempotency-key",
                "cat-food-delivery",
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
                "category",
                "find",
                "--kind",
                "expense",
                "--primary",
                "餐饮",
                "--secondary",
                "外卖",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "summary", "categories", "--kind", "expense", "--currency", "CNY", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/categories",
            "payload": {"kind": "expense", "primary": "餐饮", "secondary": "外卖"},
            "key": "cat-food-delivery",
        },
        {
            "method": "GET",
            "path": "/api/v1/categories?kind=expense&primary=%E9%A4%90%E9%A5%AE&secondary=%E5%A4%96%E5%8D%96",
            "payload": None,
            "key": None,
        },
        {
            "method": "GET",
            "path": "/api/v1/summary/categories?kind=expense&currency=CNY",
            "payload": None,
            "key": None,
        },
    ]


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


def test_user_create_posts_payload(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"user": {"user_id": "user_1", "username": payload["username"], "display_name": payload["display_name"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "user", "create", "xyy", "--display-name", "XYY", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/users",
            "payload": {"username": "xyy", "display_name": "XYY"},
            "key": calls[0]["key"],
            "token": "token-1",
        }
    ]
    assert calls[0]["key"].startswith("user-create-")
    assert json.loads(capsys.readouterr().out)["user"]["username"] == "xyy"


def test_account_read_commands_use_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"accounts": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "list",
                "--name",
                "Visa",
                "--currency",
                "USD",
                "--institution-type",
                "bank",
                "--subtype",
                "credit_card",
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
                "account",
                "find",
                "--name",
                "Visa",
                "--type",
                "liability",
                "--institution",
                "广发",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/accounts?name=Visa&currency=USD&institution_type=bank&subtype=credit_card",
            "payload": None,
            "key": None,
        },
        {
            "method": "GET",
            "path": "/api/v1/accounts?name=Visa&type=liability&institution=%E5%B9%BF%E5%8F%91",
            "payload": None,
            "key": None,
        },
    ]


def test_account_create_and_update_metadata_payloads(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"account": {"account_id": "acc_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "create",
                "Wise USD",
                "--currency",
                "USD",
                "--institution-type",
                "fintech",
                "--subtype",
                "multicurrency_wallet",
                "--institution",
                "Wise",
                "--idempotency-key",
                "acct-wise",
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
                "account",
                "update",
                "acc_1",
                "--institution-type",
                "bank",
                "--subtype",
                "savings",
                "--institution",
                "Example Bank",
                "--idempotency-key",
                "acct-update",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/accounts",
            "payload": {
                "name": "Wise USD",
                "type": "asset",
                "currency": "USD",
                "opening_balance": "0",
                "institution_type": "fintech",
                "subtype": "multicurrency_wallet",
                "institution": "Wise",
            },
            "key": "acct-wise",
        },
        {
            "method": "PATCH",
            "path": "/api/v1/accounts/acc_1",
            "payload": {"institution_type": "bank", "subtype": "savings", "institution": "Example Bank"},
            "key": "acct-update",
        },
    ]


def test_summary_accounts_uses_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"groups": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "summary",
                "accounts",
                "--group-by",
                "institution_type",
                "--currency",
                "CNY",
                "--institution-type",
                "e_wallet",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/summary/accounts?group_by=institution_type&currency=CNY&institution_type=e_wallet",
            "payload": None,
            "key": None,
        }
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


def test_investment_event_cli_posts_dated_cash_flow(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"event": {"event_id": "inv_1", "event_type": payload["event_type"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "investment",
                "event",
                "acc_wealth",
                "--type",
                "buy",
                "--amount",
                "35000",
                "--currency",
                "CNY",
                "--occurred-at",
                "2026-04-24T00:00:00+08:00",
                "--memo",
                "initial purchase",
                "--idempotency-key",
                "inv-buy",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/investments/events",
            "payload": {
                "account_id": "acc_wealth",
                "event_type": "buy",
                "amount": "35000",
                "currency": "CNY",
                "occurred_at": "2026-04-24T00:00:00+08:00",
                "memo": "initial purchase",
            },
            "key": "inv-buy",
        }
    ]
    assert json.loads(capsys.readouterr().out)["event"]["event_type"] == "buy"


def test_investment_performance_cli_uses_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"account_id": "acc_wealth", "holding_days": 21}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "investment",
                "performance",
                "acc_wealth",
                "--as-of",
                "2026-05-15T00:00:00+08:00",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/investments/accounts/acc_wealth/performance?as_of=2026-05-15T00%3A00%3A00%2B08%3A00",
            "payload": None,
            "key": None,
        }
    ]
