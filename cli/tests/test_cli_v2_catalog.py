from __future__ import annotations

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
