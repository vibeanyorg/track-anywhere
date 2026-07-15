from __future__ import annotations

import json

import pytest

from track_anywhere_cli.click_app import run


def _recorder(calls):
    def request(config, method, path, payload=None, key=None):
        calls.append(
            {
                "token": config.token,
                "method": method,
                "path": path,
                "payload": payload,
                "key": key,
            }
        )
        return 201 if method == "POST" else 200, {"ok": True}

    return request


def test_v2_catalog_commands_use_book_scoped_contracts(capsys):
    calls = []
    request = _recorder(calls)

    assert (
        run(
            [
                "--token",
                "token",
                "book",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "--name",
                "Personal",
                "--base-asset-code",
                "CNY",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "asset",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "USD",
                "--kind",
                "fiat",
                "--ledger-scale",
                "2",
                "--input-scale",
                "2",
                "--display-scale",
                "2",
                "--name",
                "US Dollar",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "account",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "--asset-code",
                "USD",
                "--type",
                "asset",
                "--name",
                "Cash",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "category",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "33333333-3333-3333-3333-333333333333",
                "--category-version-id",
                "44444444-4444-4444-4444-444444444444",
                "--name",
                "Food",
                "--change-reason-code",
                "created",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "account",
                "close",
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "account",
                "reopen",
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        {
            "token": "token",
            "method": "POST",
            "path": "/api/v2/books",
            "payload": {
                "book_id": "11111111-1111-1111-1111-111111111111",
                "current_name": "Personal",
                "base_asset_code": "CNY",
            },
            "key": None,
        },
        {
            "token": "token",
            "method": "POST",
            "path": ("/api/v2/books/11111111-1111-1111-1111-111111111111/assets"),
            "payload": {
                "asset_code": "USD",
                "kind": "fiat",
                "ledger_scale": 2,
                "input_scale": 2,
                "display_scale": 2,
                "current_name": "US Dollar",
            },
            "key": None,
        },
        {
            "token": "token",
            "method": "POST",
            "path": ("/api/v2/books/11111111-1111-1111-1111-111111111111/accounts"),
            "payload": {
                "account_id": "22222222-2222-2222-2222-222222222222",
                "asset_code": "USD",
                "account_type": "asset",
                "current_name": "Cash",
            },
            "key": None,
        },
        {
            "token": "token",
            "method": "POST",
            "path": ("/api/v2/books/11111111-1111-1111-1111-111111111111/categories"),
            "payload": {
                "category_id": "33333333-3333-3333-3333-333333333333",
                "category_version_id": "44444444-4444-4444-4444-444444444444",
                "name": "Food",
                "change_reason_code": "created",
            },
            "key": None,
        },
        {
            "token": "token",
            "method": "POST",
            "path": (
                "/api/v2/books/11111111-1111-1111-1111-111111111111/"
                "accounts/22222222-2222-2222-2222-222222222222/close"
            ),
            "payload": None,
            "key": None,
        },
        {
            "token": "token",
            "method": "POST",
            "path": (
                "/api/v2/books/11111111-1111-1111-1111-111111111111/"
                "accounts/22222222-2222-2222-2222-222222222222/reopen"
            ),
            "payload": None,
            "key": None,
        },
    ]


def test_v2_book_queries_preserve_as_of_position(capsys):
    calls = []
    request = _recorder(calls)
    book_id = "11111111-1111-1111-1111-111111111111"

    assert (
        run(
            [
                "--token",
                "token",
                "book",
                "balances",
                book_id,
                "--as-of-book-position",
                "41",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "book",
                "reporting-lines",
                book_id,
                "--as-of-book-position",
                "41",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    capsys.readouterr()

    assert [call["path"] for call in calls] == [
        f"/api/v2/books/{book_id}/balances?as_of_book_position=41",
        f"/api/v2/books/{book_id}/reporting-lines?as_of_book_position=41",
    ]


def test_v2_catalog_read_commands_are_discoverable_and_book_scoped(capsys):
    calls = []
    request = _recorder(calls)
    book_id = "11111111-1111-1111-1111-111111111111"
    account_id = "22222222-2222-2222-2222-222222222222"

    commands = (
        ["--token", "token", "book", "list", "--json"],
        ["--token", "token", "asset", "list", book_id, "--json"],
        [
            "--token",
            "token",
            "account",
            "list",
            book_id,
            "--type",
            "liability",
            "--subtype",
            "credit_card",
            "--status",
            "active",
            "--asset-code",
            "CNY",
            "--name",
            "交通 银行",
            "--json",
        ],
        [
            "--token",
            "token",
            "account",
            "show",
            book_id,
            account_id,
            "--json",
        ],
        [
            "--token",
            "token",
            "account",
            "balance",
            book_id,
            account_id,
            "--json",
        ],
        ["--token", "token", "category", "list", book_id, "--json"],
    )
    for command in commands:
        assert run(command, requester=request) == 0
    capsys.readouterr()

    assert [call["path"] for call in calls] == [
        "/api/v2/books",
        f"/api/v2/books/{book_id}/assets",
        (
            f"/api/v2/books/{book_id}/accounts?account_type=liability"
            "&account_subtype=credit_card&status=active&asset_code=CNY"
            "&name=%E4%BA%A4%E9%80%9A+%E9%93%B6%E8%A1%8C"
        ),
        f"/api/v2/books/{book_id}/accounts/{account_id}",
        f"/api/v2/books/{book_id}/accounts/{account_id}/balance",
        f"/api/v2/books/{book_id}/categories",
    ]
    assert all(call["method"] == "GET" for call in calls)
    assert all(call["payload"] is None and call["key"] is None for call in calls)


def test_book_balances_json_preserves_account_lifecycle_status(capsys):
    book_id = "11111111-1111-1111-1111-111111111111"
    account_id = "22222222-2222-2222-2222-222222222222"

    def request(_config, method, path, payload=None, key=None):
        assert (method, path, payload, key) == (
            "GET",
            f"/api/v2/books/{book_id}/balances",
            None,
            None,
        )
        return 200, {
            "items": [
                {
                    "account_id": account_id,
                    "account_status": "closed",
                }
            ],
            "as_of_book_position": 7,
            "projection_matches_reference": True,
        }

    assert (
        run(
            [
                "--token",
                "token",
                "book",
                "balances",
                book_id,
                "--json",
            ],
            requester=request,
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["data"]["items"][0]["account_status"] == "closed"


def test_book_reporting_lines_json_preserves_historical_catalog_id(capsys):
    book_id = "11111111-1111-1111-1111-111111111111"
    catalog_id = "99999999-9999-4999-8999-999999999999"

    def request(_config, method, path, payload=None, key=None):
        assert (method, path, payload, key) == (
            "GET",
            f"/api/v2/books/{book_id}/reporting-lines?as_of_book_position=2",
            None,
            None,
        )
        return 200, {
            "items": [{"catalog_id": catalog_id}],
            "as_of_book_position": 2,
        }

    assert (
        run(
            [
                "--token",
                "token",
                "book",
                "reporting-lines",
                book_id,
                "--as-of-book-position",
                "2",
                "--json",
            ],
            requester=request,
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["data"]["items"][0]["catalog_id"] == catalog_id


def test_account_create_can_send_a_credit_card_subtype(capsys):
    calls = []

    assert (
        run(
            [
                "--token",
                "token",
                "account",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "--asset-code",
                "CNY",
                "--type",
                "liability",
                "--account-subtype",
                "credit_card",
                "--name",
                "Primary Card",
                "--json",
            ],
            requester=_recorder(calls),
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        {
            "token": "token",
            "method": "POST",
            "path": ("/api/v2/books/11111111-1111-1111-1111-111111111111/accounts"),
            "payload": {
                "account_id": "22222222-2222-2222-2222-222222222222",
                "asset_code": "CNY",
                "account_type": "liability",
                "account_subtype": "credit_card",
                "current_name": "Primary Card",
            },
            "key": None,
        }
    ]


@pytest.mark.parametrize(
    ("account_type", "account_subtype"),
    [
        ("asset", "credit_card"),
        ("liability", "Credit_Card"),
        ("receivable", "credit_card"),
    ],
)
def test_account_create_rejects_invalid_type_and_subtype_before_network(
    account_type,
    account_subtype,
    capsys,
):
    def no_request(*_args, **_kwargs):
        raise AssertionError("invalid account semantics must fail before network I/O")

    assert (
        run(
            [
                "--token",
                "token",
                "account",
                "create",
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
                "--asset-code",
                "CNY",
                "--type",
                account_type,
                "--account-subtype",
                account_subtype,
                "--name",
                "Invalid Card",
                "--json",
            ],
            requester=no_request,
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == 422
    assert payload["diagnostics"][0]["code"] == "invalid_v2_cli_input"
