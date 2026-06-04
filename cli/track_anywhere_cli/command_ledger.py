from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import urllib.parse

from .config import CliConfig, command_idempotency_key, safe_backup_label
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_ledger_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_ledger_command_path(args)
    if command_path is None:
        return None
    handler = LEDGER_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_ledger_command_path(args: Namespace) -> str | None:
    command = getattr(args, "command", None)
    if command == "capture":
        return "capture"
    if command == "draft-confirm":
        return "draft.confirm"
    if command == "record":
        return "tx.record"
    if command == "tx":
        tx_command = getattr(args, "tx_command", None)
        if tx_command in {"record", "list", "show", "snapshot", "reverse", "reclassify"}:
            return f"tx.{tx_command}"
        if tx_command == "fx-exchange":
            return "tx.fx-exchange"
    if command == "expense" and getattr(args, "expense_command", None) == "record":
        return "expense.record"
    if command == "income" and getattr(args, "income_command", None) == "record":
        return "income.record"
    if command == "balance-adjust":
        return "balance.adjust"
    if command == "balance":
        return "balance"
    if command == "account" and getattr(args, "account_command", None) in {"adjust", "balance"}:
        return f"account.{args.account_command}"
    return None


def request_capture_draft(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    capture_payload = {
        "memo": args.memo,
        "amount": args.amount,
        "currency": args.currency,
        "source_account_id": args.source_account_id,
        "expense_account_id": args.expense_account_id,
    }
    if args.dry_run:
        return 200, {"dry_run": True, "policy_decision": "would_create_draft", "payload": capture_payload}
    return requester(config, "POST", "/api/v1/drafts/capture", capture_payload, key=command_idempotency_key(args, "draft-capture"))


def request_confirm_draft(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(
        config,
        "POST",
        "/api/v1/drafts/confirm",
        {"draft_id": args.draft_id, "expected_version": args.expected_version},
        key=command_idempotency_key(args, "draft-confirm"),
    )


def request_record_transaction(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "POST", "/api/v1/ledger/transactions", _transaction_payload(args), key=command_idempotency_key(args, "tx-record"))


def request_record_fx_exchange(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "POST", "/api/v1/ledger/fx-exchanges", _fx_exchange_payload(args), key=command_idempotency_key(args, "tx-fx-exchange"))


def request_record_expense(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    uses_payment_profile = bool(getattr(args, "payment", None))
    uses_source_account = bool(getattr(args, "from_account_id", None))
    if uses_payment_profile == uses_source_account:
        return 400, {"detail": "expense record requires exactly one of --payment or --from-account-id"}

    expense_payload = _category_money_payload(args)
    if uses_payment_profile:
        payment_profile_ref = urllib.parse.quote(args.payment)
        return requester(
            config,
            "POST",
            f"/api/v1/payment-profiles/{payment_profile_ref}/expenses",
            expense_payload,
            key=command_idempotency_key(args, "payment-profile-expense"),
        )

    expense_payload["from_account_id"] = args.from_account_id
    return requester(config, "POST", "/api/v1/expenses", expense_payload, key=command_idempotency_key(args, "expense-record"))


def request_record_income(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    income_payload = _category_money_payload(args)
    income_payload["to_account_id"] = args.to_account_id
    return requester(config, "POST", "/api/v1/incomes", income_payload, key=command_idempotency_key(args, "income-record"))


def request_list_transactions(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    transaction_query = with_query(
        "/api/v1/ledger/transactions",
        {"account_id": args.account_id, "category_id": args.category_id, "counterparty": getattr(args, "counterparty", None), "limit": args.limit},
    )
    return requester(config, "GET", transaction_query)


def request_show_transaction(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}")


def request_transaction_snapshot(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    status, snapshot_data = requester(config, "GET", f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}/snapshot")
    if status < 400 and getattr(args, "output", None):
        snapshot_file = _write_json_file(snapshot_data, Path(args.output))
        snapshot_data = {**snapshot_data, "snapshot_file": str(snapshot_file)}
    return status, snapshot_data


def request_reverse_transaction(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    reversal_payload = {"transaction_id": args.transaction_id, "memo": args.memo}
    return requester(config, "POST", "/api/v1/ledger/reverse", reversal_payload, key=command_idempotency_key(args, "tx-reverse"))


def request_reclassify_transaction(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    backup_info = _snapshot_backup_before_reclassification(args, config, requester) if getattr(args, "backup_before", False) else None
    if isinstance(backup_info, tuple):
        return backup_info

    reclassification_payload = {
        key: value
        for key, value in {"transaction_id": args.transaction_id, "category_id": args.category_id, "line_id": args.line_id, "memo": args.memo}.items()
        if value not in (None, "")
    }
    status, response_data = requester(
        config,
        "POST",
        "/api/v1/ledger/reclassify",
        reclassification_payload,
        key=command_idempotency_key(args, "tx-reclassify"),
    )
    if status < 400 and backup_info is not None and isinstance(response_data, dict):
        response_data = {**response_data, "backup": backup_info}
    return status, response_data


def request_adjust_account_balance(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    adjustment_payload = {"account_id": args.account_id, "amount": args.amount, "currency": args.currency, "purpose": args.purpose}
    if getattr(args, "memo", ""):
        adjustment_payload["memo"] = args.memo
    if args.occurred_at:
        adjustment_payload["occurred_at"] = args.occurred_at
    return requester(config, "POST", "/api/v1/ledger/adjustments", adjustment_payload, key=command_idempotency_key(args, "balance-adjust"))


def request_get_account_balance(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    include_drafts_suffix = "?include_drafts=true" if args.include_drafts else ""
    return requester(config, "GET", f"/api/v1/query/accounts/{args.account_id}/balance{include_drafts_suffix}")


def _transaction_payload(args: Namespace) -> dict[str, Any]:
    transaction_payload = {
        "amount": args.amount,
        "currency": args.currency,
        "from_account_id": args.from_account_id,
        "to_account_id": args.to_account_id,
        "purpose": args.purpose,
    }
    _add_optional_ledger_fields(transaction_payload, args)
    if args.category_id:
        transaction_payload["category_id"] = args.category_id
    return transaction_payload


def _fx_exchange_payload(args: Namespace) -> dict[str, Any]:
    fx_payload = {
        "from_account_id": args.from_account_id,
        "from_amount": args.from_amount,
        "from_currency": args.from_currency,
        "to_account_id": args.to_account_id,
        "to_amount": args.to_amount,
        "to_currency": args.to_currency,
        "purpose": args.purpose,
        "rate_source": args.rate_source,
    }
    _add_optional_ledger_fields(fx_payload, args)
    if args.fee_account_id:
        fx_payload["fee_account_id"] = args.fee_account_id
    if args.fee_amount:
        fx_payload["fee_amount"] = args.fee_amount
    return fx_payload


def _category_money_payload(args: Namespace) -> dict[str, Any]:
    money_payload = {"amount": args.amount, "currency": args.currency, "category_id": args.category_id, "purpose": args.purpose}
    _add_optional_ledger_fields(money_payload, args)
    return money_payload


def _add_optional_ledger_fields(payload: dict[str, Any], args: Namespace) -> None:
    if getattr(args, "memo", ""):
        payload["memo"] = args.memo
    if args.occurred_at:
        payload["occurred_at"] = args.occurred_at
    if getattr(args, "counterparty", None):
        payload["counterparty"] = args.counterparty


def _snapshot_backup_before_reclassification(args: Namespace, config: CliConfig, requester: Requester) -> dict[str, Any] | tuple[int, Any]:
    snapshot_status, snapshot_data = requester(
        config,
        "GET",
        f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}/snapshot",
    )
    if snapshot_status >= 400:
        return snapshot_status, snapshot_data
    return _write_snapshot_backup(
        snapshot_data,
        transaction_id=args.transaction_id,
        output_dir=getattr(args, "backup_dir", None),
        label=getattr(args, "backup_label", None),
    )


def _write_snapshot_backup(data: Any, *, transaction_id: str, output_dir: str | None, label: str | None) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    backup_dir = Path(output_dir).expanduser() if output_dir else Path.cwd() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename_parts = ["tx", transaction_id, "before-reclassify", created_at.strftime("%Y%m%d-%H%M%S")]
    label_part = safe_backup_label(label)
    if label_part:
        filename_parts.append(label_part)
    backup_path = backup_dir / ("-".join(filename_parts) + ".json")
    _write_json_file(data, backup_path)
    return {
        "backup_path": str(backup_path),
        "bytes": backup_path.stat().st_size,
        "created_at": created_at.isoformat(),
        "backup_type": "transaction_snapshot",
        "transaction_id": transaction_id,
    }


def _write_json_file(data: Any, path: Path) -> Path:
    output_path = path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output_path


LEDGER_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "capture": request_capture_draft,
    "draft.confirm": request_confirm_draft,
    "tx.record": request_record_transaction,
    "tx.fx-exchange": request_record_fx_exchange,
    "tx.list": request_list_transactions,
    "tx.show": request_show_transaction,
    "tx.snapshot": request_transaction_snapshot,
    "tx.reverse": request_reverse_transaction,
    "tx.reclassify": request_reclassify_transaction,
    "expense.record": request_record_expense,
    "income.record": request_record_income,
    "balance.adjust": request_adjust_account_balance,
    "account.adjust": request_adjust_account_balance,
    "balance": request_get_account_balance,
    "account.balance": request_get_account_balance,
}
