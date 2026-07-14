from __future__ import annotations

from argparse import Namespace
from typing import Any
from uuid import uuid4

from track_anywhere_cli.commands import dispatch_api_command
from track_anywhere_cli.config import CliConfig

from .api_clients import BackendApiClient
from .cli_clients import requester_for_backend
from .helpers import issue_contract_token, unique


def cli_config(client: BackendApiClient) -> CliConfig:
    return CliConfig(
        base_url=f"contract://{client.name}",
        token=issue_contract_token(client),
    )


def run_cli_command(
    client: BackendApiClient,
    args: Namespace,
) -> tuple[int, Any]:
    result = dispatch_api_command(
        args,
        cli_config(client),
        requester_for_backend(client),
    )
    assert result is not None
    return result


def test_cli_v2_catalog_post_and_query_contract(
    backend_client: BackendApiClient,
) -> None:
    book_id = str(uuid4())
    debit_account_id = str(uuid4())
    credit_account_id = str(uuid4())
    transaction_id = str(uuid4())

    assert (
        run_cli_command(
            backend_client,
            Namespace(
                command="book",
                book_command="create",
                book_id=book_id,
                name="CLI Contract",
                base_asset_code=None,
            ),
        )[0]
        == 201
    )
    assert (
        run_cli_command(
            backend_client,
            Namespace(
                command="asset",
                asset_command="create",
                book_id=book_id,
                asset_code="USD",
                kind="fiat",
                ledger_scale=2,
                input_scale=2,
                display_scale=2,
                name="US Dollar",
            ),
        )[0]
        == 201
    )
    for account_id, account_type, name in (
        (debit_account_id, "expense", "Food"),
        (credit_account_id, "asset", "Cash"),
    ):
        assert (
            run_cli_command(
                backend_client,
                Namespace(
                    command="account",
                    account_command="create",
                    book_id=book_id,
                    account_id=account_id,
                    asset_code="USD",
                    account_type=account_type,
                    name=name,
                    system_role=None,
                ),
            )[0]
            == 201
        )

    status, posted = run_cli_command(
        backend_client,
        Namespace(
            command="tx",
            tx_command="record",
            book_id=book_id,
            command_id=str(uuid4()),
            transaction_id=transaction_id,
            expected_stream_version=0,
            kind="standard",
            effective_at="2026-07-14T12:30:00Z",
            description_ref=None,
            external_reference=[],
            posting=[
                f"{uuid4()}:{debit_account_id}:USD:debit:25.00",
                f"{uuid4()}:{credit_account_id}:USD:credit:25.00",
            ],
            idempotency_key=unique("cli-post"),
        ),
    )
    assert status == 201
    assert posted == {
        "transaction_id": transaction_id,
        "as_of_book_position": 1,
    }

    status, journal = run_cli_command(
        backend_client,
        Namespace(
            command="tx",
            tx_command="list",
            book_id=book_id,
            limit=10,
            cursor=None,
            as_of_book_position=1,
        ),
    )
    assert status == 200
    assert journal["items"][0]["transaction_id"] == transaction_id
    assert [posting["units"] for posting in journal["items"][0]["postings"]] == [
        "2500",
        "2500",
    ]


def test_cli_preserves_amount_strings_and_explicit_idempotency_keys() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    def requester(
        _config: CliConfig,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        key: str | None,
    ) -> tuple[int, Any]:
        calls.append((method, path, payload, key))
        return 201, {"ok": True}

    amount = "00012.3400"
    key = "caller-owned-idempotency-key"
    book_id = str(uuid4())
    result = dispatch_api_command(
        Namespace(
            command="tx",
            tx_command="record",
            book_id=book_id,
            command_id=str(uuid4()),
            transaction_id=str(uuid4()),
            expected_stream_version=0,
            kind="standard",
            effective_at="2026-07-14T12:30:00Z",
            description_ref=None,
            external_reference=[],
            posting=[
                f"{uuid4()}:{uuid4()}:USD:debit:{amount}",
                f"{uuid4()}:{uuid4()}:USD:credit:{amount}",
            ],
            idempotency_key=key,
        ),
        CliConfig(base_url="http://testserver", token="credential"),
        requester,
    )

    assert result == (201, {"ok": True})
    assert calls[0][0:2] == (
        "POST",
        f"/api/v2/books/{book_id}/journal/transactions",
    )
    assert calls[0][2] is not None
    assert [posting["amount"] for posting in calls[0][2]["postings"]] == [
        amount,
        amount,
    ]
    assert calls[0][3] == key


def test_cli_requester_auth_contract(
    backend_client: BackendApiClient,
) -> None:
    result = dispatch_api_command(
        Namespace(
            command="book",
            book_command="create",
            book_id=str(uuid4()),
            name="Unauthenticated",
            base_asset_code=None,
        ),
        CliConfig(base_url=f"contract://{backend_client.name}", token=None),
        requester_for_backend(backend_client),
    )

    assert result is not None
    status, data = result
    assert status == 401
    assert data == {"detail": "authentication is required"}
