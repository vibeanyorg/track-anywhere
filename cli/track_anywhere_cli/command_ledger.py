from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import urllib.parse
from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig, command_idempotency_key, safe_backup_label
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def _transaction_payload(args: Namespace) -> dict[str, Any]:
    payload = {
        "amount": args.amount,
        "currency": args.currency,
        "from_account_id": args.from_account_id,
        "to_account_id": args.to_account_id,
        "purpose": args.purpose,
    }
    if getattr(args, "memo", ""):
        payload["memo"] = args.memo
    if args.occurred_at:
        payload["occurred_at"] = args.occurred_at
    if args.category_id:
        payload["category_id"] = args.category_id
    return payload


def handle_ledger_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command == "capture":
        payload = {
            "memo": args.memo,
            "amount": args.amount,
            "currency": args.currency,
            "source_account_id": args.source_account_id,
            "expense_account_id": args.expense_account_id,
        }
        if args.dry_run:
            return 200, {"dry_run": True, "policy_decision": "would_create_draft", "payload": payload}
        return requester(
            config,
            "POST",
            "/api/v1/drafts/capture",
            payload,
            key=command_idempotency_key(args, "draft-capture"),
        )
    if args.command == "draft-confirm":
        return requester(
            config,
            "POST",
            "/api/v1/drafts/confirm",
            {"draft_id": args.draft_id, "expected_version": args.expected_version},
            key=command_idempotency_key(args, "draft-confirm"),
        )
    if args.command in {"record"} or (args.command == "tx" and args.tx_command == "record"):
        return requester(
            config,
            "POST",
            "/api/v1/ledger/transactions",
            _transaction_payload(args),
            key=command_idempotency_key(args, "tx-record"),
        )
    if args.command == "expense" and args.expense_command == "record":
        uses_payment = bool(getattr(args, "payment", None))
        uses_source_account = bool(getattr(args, "from_account_id", None))
        if uses_payment == uses_source_account:
            return 400, {"detail": "expense record requires exactly one of --payment or --from-account-id"}
        payload = {
            "amount": args.amount,
            "currency": args.currency,
            "category_id": args.category_id,
            "purpose": args.purpose,
        }
        if getattr(args, "memo", ""):
            payload["memo"] = args.memo
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        if uses_payment:
            return requester(
                config,
                "POST",
                f"/api/v1/payment-profiles/{urllib.parse.quote(args.payment)}/expenses",
                payload,
                key=command_idempotency_key(args, "payment-profile-expense"),
            )
        payload["from_account_id"] = args.from_account_id
        return requester(config, "POST", "/api/v1/expenses", payload, key=command_idempotency_key(args, "expense-record"))
    if args.command == "income" and args.income_command == "record":
        payload = {
            "amount": args.amount,
            "currency": args.currency,
            "to_account_id": args.to_account_id,
            "category_id": args.category_id,
            "purpose": args.purpose,
        }
        if getattr(args, "memo", ""):
            payload["memo"] = args.memo
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        return requester(config, "POST", "/api/v1/incomes", payload, key=command_idempotency_key(args, "income-record"))
    if args.command == "tx" and args.tx_command == "list":
        path = with_query(
            "/api/v1/ledger/transactions",
            {"account_id": args.account_id, "category_id": args.category_id, "limit": args.limit},
        )
        return requester(config, "GET", path)
    if args.command == "tx" and args.tx_command == "show":
        return requester(config, "GET", f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}")
    if args.command == "tx" and args.tx_command == "snapshot":
        status, data = requester(
            config,
            "GET",
            f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}/snapshot",
        )
        if status < 400 and getattr(args, "output", None):
            snapshot_file = _write_json_file(data, Path(args.output))
            data = {**data, "snapshot_file": str(snapshot_file)}
        return status, data
    if args.command == "tx" and args.tx_command == "reverse":
        payload = {"transaction_id": args.transaction_id, "memo": args.memo}
        return requester(
            config,
            "POST",
            "/api/v1/ledger/reverse",
            payload,
            key=command_idempotency_key(args, "tx-reverse"),
        )
    if args.command == "tx" and args.tx_command == "reclassify":
        backup_info = None
        if getattr(args, "backup_before", False):
            snapshot_status, snapshot_data = requester(
                config,
                "GET",
                f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}/snapshot",
            )
            if snapshot_status >= 400:
                return snapshot_status, snapshot_data
            backup_info = _write_snapshot_backup(
                snapshot_data,
                transaction_id=args.transaction_id,
                output_dir=getattr(args, "backup_dir", None),
                label=getattr(args, "backup_label", None),
            )
        payload = {
            "transaction_id": args.transaction_id,
            "category_id": args.category_id,
            "line_id": args.line_id,
            "memo": args.memo,
        }
        status, data = requester(
            config,
            "POST",
            "/api/v1/ledger/reclassify",
            {key: value for key, value in payload.items() if value not in (None, "")},
            key=command_idempotency_key(args, "tx-reclassify"),
        )
        if status < 400 and backup_info is not None and isinstance(data, dict):
            data = {**data, "backup": backup_info}
        return status, data
    if args.command == "balance-adjust" or (args.command == "account" and args.account_command == "adjust"):
        payload = {
            "account_id": args.account_id,
            "amount": args.amount,
            "currency": args.currency,
            "purpose": args.purpose,
        }
        if getattr(args, "memo", ""):
            payload["memo"] = args.memo
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        return requester(
            config,
            "POST",
            "/api/v1/ledger/adjustments",
            payload,
            key=command_idempotency_key(args, "balance-adjust"),
        )
    if args.command == "balance" or (args.command == "account" and args.account_command == "balance"):
        suffix = "?include_drafts=true" if args.include_drafts else ""
        return requester(config, "GET", f"/api/v1/query/accounts/{args.account_id}/balance{suffix}")
    if args.command == "payment" and args.payment_command == "profile" and args.profile_command == "create":
        payload = {
            "slug": args.slug,
            "display_name": args.display_name,
            "kind": args.kind.replace("-", "_"),
            "instrument_account_id": args.instrument_account_id,
            "backing_account_id": args.backing_account_id,
            "settlement_mode": args.settlement_mode,
            "settlement_rate": args.settlement_rate,
        }
        return requester(
            config,
            "POST",
            "/api/v1/payment-profiles",
            payload,
            key=command_idempotency_key(args, "payment-profile-create"),
        )
    if args.command == "payment" and args.payment_command == "profile" and args.profile_command == "list":
        path = with_query("/api/v1/payment-profiles", {"status": args.status})
        return requester(config, "GET", path)
    if args.command == "payment" and args.payment_command == "profile" and args.profile_command == "status":
        return requester(config, "GET", f"/api/v1/payment-profiles/{urllib.parse.quote(args.payment)}/status")
    return None


def _write_snapshot_backup(
    data: Any,
    *,
    transaction_id: str,
    output_dir: str | None,
    label: str | None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    backup_dir = Path(output_dir).expanduser() if output_dir else Path.cwd() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    label_part = safe_backup_label(label)
    filename_parts = ["tx", transaction_id, "before-reclassify", created_at.strftime("%Y%m%d-%H%M%S")]
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
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
