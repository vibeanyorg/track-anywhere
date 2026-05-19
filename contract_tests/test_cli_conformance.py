from __future__ import annotations

from argparse import Namespace
from typing import Any

from track_anywhere_cli.commands import dispatch_api_command
from track_anywhere_cli.config import CliConfig

from .api_clients import BackendApiClient
from .cli_clients import requester_for_backend
from .helpers import issue_dev_token, unique


def cli_config(client: BackendApiClient) -> CliConfig:
    return CliConfig(base_url=f"contract://{client.name}", token=issue_dev_token(client))


def run_cli_command(client: BackendApiClient, args: Namespace) -> tuple[int, Any]:
    result = dispatch_api_command(args, cli_config(client), requester_for_backend(client))
    assert result is not None
    return result


def account_create_args(
    *,
    name: str,
    account_type: str = "asset",
    currency: str = "CNY",
    opening_balance: str | None = "0",
    key: str,
) -> Namespace:
    return Namespace(
        command="account",
        account_command="create",
        name=name,
        type=account_type,
        currency=currency,
        opening_balance=opening_balance,
        institution_type=None,
        subtype=None,
        institution=None,
        idempotency_key=key,
    )


def test_cli_core_ledger_workflow_contract(backend_client: BackendApiClient):
    suffix = unique(f"{backend_client.name}-cli")
    status, cash_data = run_cli_command(
        backend_client,
        account_create_args(name=f"{suffix} Cash", opening_balance="100", key=f"{suffix}-cash"),
    )
    assert status == 200
    cash_id = cash_data["account"]["account_id"]

    status, expense_data = run_cli_command(
        backend_client,
        account_create_args(
            name=f"{suffix} Food",
            account_type="expense",
            opening_balance=None,
            key=f"{suffix}-food",
        ),
    )
    assert status == 200
    expense_id = expense_data["account"]["account_id"]

    status, tx_data = run_cli_command(
        backend_client,
        Namespace(
            command="tx",
            tx_command="record",
            amount="25",
            currency="CNY",
            from_account_id=cash_id,
            to_account_id=expense_id,
            purpose=f"{suffix} lunch",
            occurred_at=None,
            category_id=None,
            idempotency_key=f"{suffix}-tx",
        ),
    )
    assert status == 200
    transaction_id = tx_data["transaction"]["transaction_id"]

    status, tx_list = run_cli_command(
        backend_client,
        Namespace(command="tx", tx_command="list", account_id=cash_id, category_id=None, limit=10),
    )
    assert status == 200
    assert any(tx["transaction_id"] == transaction_id for tx in tx_list["transactions"])

    status, balance = run_cli_command(
        backend_client,
        Namespace(command="account", account_command="balance", account_id=cash_id, include_drafts=False),
    )
    assert status == 200
    assert balance["official_balance"]["amount"] == "75"

def test_cli_explicit_idempotency_contract(backend_client: BackendApiClient):
    suffix = unique(f"{backend_client.name}-cli-idem")
    key = f"{suffix}-account"
    args = account_create_args(name=f"{suffix} Cash", currency="USD", opening_balance="10", key=key)

    first_status, first_data = run_cli_command(backend_client, args)
    replay_status, replay_data = run_cli_command(backend_client, args)
    conflict_status, conflict_data = run_cli_command(
        backend_client,
        account_create_args(name=f"{suffix} Different", currency="USD", opening_balance="10", key=key),
    )

    assert first_status == 200
    assert replay_status == 200
    assert replay_data["idempotent_replay"] is True
    assert replay_data["account"]["account_id"] == first_data["account"]["account_id"]
    assert conflict_status == 409
    assert "idempotency" in str(conflict_data).lower()


def test_cli_requester_auth_contract(backend_client: BackendApiClient):
    suffix = unique(f"{backend_client.name}-cli-auth")
    args = account_create_args(name=f"{suffix} Cash", key=f"{suffix}-cash")
    result = dispatch_api_command(
        args,
        CliConfig(base_url=f"contract://{backend_client.name}", token=None),
        requester_for_backend(backend_client),
    )

    assert result is not None
    status, data = result
    assert status == 401
    assert "detail" in data
