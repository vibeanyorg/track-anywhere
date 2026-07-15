from __future__ import annotations

import json

from track_anywhere_cli.click_app import run


BOOK = "11111111-1111-1111-1111-111111111111"
TX = "22222222-2222-2222-2222-222222222222"
COMMAND = "33333333-3333-3333-3333-333333333333"


def _recorder(calls):
    def request(config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return 201 if method == "POST" else 200, {"ok": True}

    return request


def test_post_transaction_preserves_amount_and_idempotency_strings(capsys):
    calls = []

    assert (
        run(
            [
                "--token",
                "token",
                "tx",
                "record",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--expected-stream-version",
                "0",
                "--kind",
                "standard",
                "--effective-at",
                "2026-07-14T12:00:00+08:00",
                "--posting",
                "44444444-4444-4444-4444-444444444444:"
                "55555555-5555-5555-5555-555555555555:CNY:debit:00012.3400",
                "--posting",
                "66666666-6666-6666-6666-666666666666:"
                "77777777-7777-7777-7777-777777777777:CNY:credit:00012.3400",
                "--external-reference",
                "bank:bank_transaction:provider:reference/42",
                "--idempotency-key",
                "stable:key/42",
                "--json",
            ],
            requester=_recorder(calls),
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        (
            "POST",
            f"/api/v2/books/{BOOK}/journal/transactions",
            {
                "command_id": COMMAND,
                "transaction_id": TX,
                "expected_stream_version": 0,
                "kind": "standard",
                "effective_at": "2026-07-14T12:00:00+08:00",
                "external_references": [
                    {
                        "provider_code": "bank",
                        "kind": "bank_transaction",
                        "reference": "provider:reference/42",
                    }
                ],
                "postings": [
                    {
                        "posting_id": "44444444-4444-4444-4444-444444444444",
                        "account_id": "55555555-5555-5555-5555-555555555555",
                        "asset_code": "CNY",
                        "side": "debit",
                        "amount": "00012.3400",
                    },
                    {
                        "posting_id": "66666666-6666-6666-6666-666666666666",
                        "account_id": "77777777-7777-7777-7777-777777777777",
                        "asset_code": "CNY",
                        "side": "credit",
                        "amount": "00012.3400",
                    },
                ],
            },
            "stable:key/42",
        )
    ]


def test_journal_query_uses_v2_cursor_contract(capsys):
    calls = []
    assert (
        run(
            [
                "--token",
                "token",
                "tx",
                "list",
                BOOK,
                "--limit",
                "5",
                "--cursor",
                "opaque+/=",
                "--as-of-book-position",
                "99",
                "--json",
            ],
            requester=_recorder(calls),
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        (
            "GET",
            f"/api/v2/books/{BOOK}/journal?limit=5&cursor=opaque%2B%2F%3D&as_of_book_position=99",
            None,
            None,
        )
    ]


def test_transaction_show_uses_the_transaction_scoped_query(capsys):
    calls = []

    assert (
        run(
            [
                "--token",
                "token",
                "tx",
                "show",
                BOOK,
                TX,
                "--as-of-book-position",
                "99",
                "--json",
            ],
            requester=_recorder(calls),
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        (
            "GET",
            f"/api/v2/books/{BOOK}/journal/transactions/{TX}"
            "?as_of_book_position=99",
            None,
            None,
        )
    ]


def test_reverse_and_classify_use_transaction_scoped_v2_routes(capsys):
    calls = []
    request = _recorder(calls)
    reversal = "88888888-8888-8888-8888-888888888888"

    assert (
        run(
            [
                "--token",
                "token",
                "tx",
                "reverse",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--reversal-transaction-id",
                reversal,
                "--reason-code",
                "user_correction",
                "--effective-at",
                "2026-07-15T00:00:00Z",
                "--idempotency-key",
                "reverse-key",
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
                "tx",
                "classify",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--expected-revision",
                "2",
                "--effective-at",
                "2026-07-15T00:00:00Z",
                "--line",
                "99999999-9999-9999-9999-999999999999:"
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa:"
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb:CNY:1234:expense:category",
                "--idempotency-key",
                "classify-key",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    capsys.readouterr()

    assert calls[0][1] == f"/api/v2/books/{BOOK}/journal/transactions/{TX}/reverse"
    assert calls[0][3] == "reverse-key"
    assert calls[1][1] == (
        f"/api/v2/books/{BOOK}/journal/transactions/{TX}/reporting-lines/assign"
    )
    assert calls[1][2]["lines"][0]["units"] == "1234"
    assert calls[1][3] == "classify-key"


def test_invalid_structured_posting_fails_without_request(capsys):
    def no_request(*_args, **_kwargs):
        raise AssertionError("invalid input must fail before network I/O")

    assert (
        run(
            [
                "--token",
                "token",
                "tx",
                "record",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--expected-stream-version",
                "0",
                "--kind",
                "standard",
                "--effective-at",
                "2026-07-14T00:00:00Z",
                "--posting",
                "not-a-posting",
                "--json",
            ],
            requester=no_request,
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == 422
    assert payload["diagnostics"][0]["code"] == "invalid_v2_cli_input"
