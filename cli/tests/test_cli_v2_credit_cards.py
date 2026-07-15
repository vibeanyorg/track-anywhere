from __future__ import annotations

import json
from argparse import Namespace

import pytest

from track_anywhere_cli.click_app import cli, run
from track_anywhere_cli.commands import command_paths, infer_command_path
from track_anywhere_cli.protocol import command_schema


BOOK = "11111111-1111-1111-1111-111111111111"
TX = "22222222-2222-2222-2222-222222222222"
COMMAND = "33333333-3333-3333-3333-333333333333"
CARD = "44444444-4444-4444-4444-444444444444"
EXPENSE = "55555555-5555-5555-5555-555555555555"
SOURCE = "66666666-6666-6666-6666-666666666666"
ORIGINAL = "77777777-7777-7777-7777-777777777777"
EFFECTIVE_AT = "2026-07-15T12:00:00+08:00"


def _recorder(calls):
    def request(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return 201, {"ok": True}

    return request


@pytest.mark.parametrize(
    ("subcommand", "counter_option", "counter_value", "route", "amount"),
    [
        ("charge", "--expense-account-id", EXPENSE, "charges", "00012.3400"),
        ("payment", "--source-account-id", SOURCE, "payments", "00100.00"),
        (
            "refund",
            "--original-transaction-id",
            ORIGINAL,
            "refunds",
            "00009.8760",
        ),
        ("fee", "--expense-account-id", EXPENSE, "fees", "00001.2500"),
    ],
)
def test_card_commands_use_semantic_routes_and_preserve_amount_strings(
    subcommand,
    counter_option,
    counter_value,
    route,
    amount,
    capsys,
):
    calls = []

    assert (
        run(
            [
                "--token",
                "token",
                "card",
                subcommand,
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--card-account-id",
                CARD,
                counter_option,
                counter_value,
                "--asset-code",
                "CNY",
                "--amount",
                amount,
                "--effective-at",
                EFFECTIVE_AT,
                "--description-ref",
                "merchant:cafe/42",
                "--external-reference",
                "bank:card_transaction:provider:reference/42",
                "--idempotency-key",
                f"card:{subcommand}:42",
                "--json",
            ],
            requester=_recorder(calls),
        )
        == 0
    )
    capsys.readouterr()

    expected_payload = {
        "command_id": COMMAND,
        "transaction_id": TX,
        "expected_stream_version": 0,
        "card_account_id": CARD,
        "asset_code": "CNY",
        "amount": amount,
        "effective_at": EFFECTIVE_AT,
        "description_ref": "merchant:cafe/42",
        "external_references": [
            {
                "provider_code": "bank",
                "kind": "card_transaction",
                "reference": "provider:reference/42",
            }
        ],
    }
    expected_payload[counter_option.removeprefix("--").replace("-", "_")] = (
        counter_value
    )
    assert calls == [
        (
            "POST",
            f"/api/v2/books/{BOOK}/credit-cards/{route}",
            expected_payload,
            f"card:{subcommand}:42",
        )
    ]


@pytest.mark.parametrize("subcommand", ["charge", "payment", "refund", "fee"])
def test_card_command_schema_exposes_no_posting_side_or_sign_inputs(subcommand):
    command_path = f"card.{subcommand}"
    schema = command_schema(cli, command_path)
    flags = {flag["name"]: flag for flag in schema["flags"]}

    assert command_path in command_paths()
    assert schema["idempotent"] is True
    assert [argument["name"] for argument in schema["arguments"]] == [
        "book_id",
        "transaction_id",
    ]
    assert flags["command_id"]["required"] is True
    assert flags["card_account_id"]["required"] is True
    assert flags["asset_code"]["required"] is True
    assert flags["amount"]["required"] is True
    assert flags["amount"]["type"] == "text"
    assert flags["effective_at"]["required"] is True
    assert flags["expected_stream_version"]["default"] == 0
    assert not ({"posting", "side", "sign", "debit", "credit"} & flags.keys())


@pytest.mark.parametrize("subcommand", ["charge", "payment", "refund", "fee"])
def test_card_command_path_inference(subcommand):
    assert (
        infer_command_path(Namespace(command="card", card_command=subcommand))
        == f"card.{subcommand}"
    )


def test_invalid_card_external_reference_fails_before_network(capsys):
    def no_request(*_args, **_kwargs):
        raise AssertionError("invalid input must fail before network I/O")

    assert (
        run(
            [
                "--token",
                "token",
                "card",
                "charge",
                BOOK,
                TX,
                "--command-id",
                COMMAND,
                "--card-account-id",
                CARD,
                "--expense-account-id",
                EXPENSE,
                "--asset-code",
                "CNY",
                "--amount",
                "12.34",
                "--effective-at",
                EFFECTIVE_AT,
                "--external-reference",
                "not-a-reference",
                "--json",
            ],
            requester=no_request,
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == 422
    assert payload["diagnostics"][0]["code"] == "invalid_v2_cli_input"
