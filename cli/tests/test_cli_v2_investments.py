from __future__ import annotations

from track_anywhere_cli.click_app import run


BOOK = "11111111-1111-1111-1111-111111111111"
TX = "22222222-2222-2222-2222-222222222222"
COMMAND = "33333333-3333-3333-3333-333333333333"
LOT = "44444444-4444-4444-4444-444444444444"


def _recorder(calls):
    def request(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return 201, {"ok": True}

    return request


def test_investment_lot_commands_preserve_unit_strings_and_keys(capsys):
    calls = []
    request = _recorder(calls)

    assert (
        run(
            [
                "--token",
                "token",
                "investment",
                "acquire",
                BOOK,
                TX,
                LOT,
                "--command-id",
                COMMAND,
                "--instrument-asset-code",
                "AAPL",
                "--settlement-asset-code",
                "USD",
                "--quantity-units",
                "100000000",
                "--cost-units",
                "123450",
                "--effective-at",
                "2026-07-14T00:00:00Z",
                "--idempotency-key",
                "acquire:key",
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
                "investment",
                "dispose",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--instrument-asset-code",
                "AAPL",
                "--settlement-asset-code",
                "USD",
                "--quantity-units",
                "50000000",
                "--proceeds-units",
                "65000",
                "--allocation-method",
                "specific_id",
                "--specific-lot",
                f"{LOT}:50000000",
                "--effective-at",
                "2026-07-15T00:00:00Z",
                "--idempotency-key",
                "dispose:key",
                "--json",
            ],
            requester=request,
        )
        == 0
    )
    capsys.readouterr()

    assert calls[0][1] == f"/api/v2/books/{BOOK}/investments/lots/acquire"
    assert calls[0][2]["quantity_units"] == "100000000"
    assert calls[0][2]["cost_units"] == "123450"
    assert calls[0][3] == "acquire:key"
    assert calls[1][1] == f"/api/v2/books/{BOOK}/investments/lots/dispose"
    assert calls[1][2]["specific_lots"] == [
        {"lot_id": LOT, "quantity_units": "50000000"}
    ]
    assert calls[1][3] == "dispose:key"
