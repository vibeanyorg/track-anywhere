from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import make_url


EXIT_SUCCESS = 0
EXIT_VALIDATION = 2
EXIT_POLICY_DENIED = 3
EXIT_IDEMPOTENCY_CONFLICT = 4
EXIT_STALE_VERSION = 5
EXIT_AUTH = 6
EXIT_SECURITY_PRECONDITION = 7
EXIT_NOT_FOUND = 8
DEFAULT_DATABASE_URL = "sqlite:///./.local/track-anywhere.sqlite3"


@dataclass
class CliConfig:
    base_url: str
    token: str | None
    insecure_automation: bool = False


class TokenStore:
    def __init__(self) -> None:
        self.token_file = Path(
            os.getenv(
                "TRACK_ANYWHERE_TOKEN_FILE",
                str(Path.home() / ".config" / "track-anywhere" / "token"),
            )
        )

    def load(self) -> str | None:
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            token = keyring.get_password("track-anywhere", "cli-token")
            if token:
                return token
        if self.token_file.exists():
            return self.token_file.read_text(encoding="utf-8").strip() or None
        return None

    def save(self, token: str) -> None:
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            keyring.set_password("track-anywhere", "cli-token", token)
            return
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(token + "\n", encoding="utf-8")
        self.token_file.chmod(0o600)
        print(f"warning: OS keyring unavailable; saved token to {self.token_file}", file=sys.stderr)


def generated_idempotency_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def command_idempotency_key(args: argparse.Namespace, prefix: str) -> str:
    return getattr(args, "idempotency_key", None) or generated_idempotency_key(prefix)


def database_url_from_env() -> str:
    return os.getenv("TRACK_ANYWHERE_DATABASE_URL", DEFAULT_DATABASE_URL)


def sqlite_path_from_database_url(database_url: str) -> Path:
    url = make_url(database_url)
    if url.drivername.split("+", 1)[0] != "sqlite":
        raise RuntimeError("data backup currently supports sqlite databases only")
    if not url.database or url.database == ":memory:":
        raise RuntimeError("data backup requires a file-backed sqlite database")
    return Path(url.database).expanduser()


def safe_backup_label(label: str | None) -> str:
    if not label:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")


def create_sqlite_backup(database_url: str | None = None, output_dir: str | None = None, label: str | None = None) -> dict[str, Any]:
    resolved_database_url = database_url or database_url_from_env()
    source_path = sqlite_path_from_database_url(resolved_database_url)
    if not source_path.exists():
        raise RuntimeError(f"sqlite database not found: {source_path}")

    backup_dir = Path(output_dir).expanduser() if output_dir else source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().astimezone().replace(microsecond=0)
    suffix = source_path.suffix or ".sqlite3"
    label_part = safe_backup_label(label)
    filename_parts = [source_path.stem, created_at.strftime("%Y%m%d-%H%M%S")]
    if label_part:
        filename_parts.append(label_part)
    backup_path = backup_dir / ("-".join(filename_parts) + suffix)

    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)

    return {
        "backup_path": str(backup_path),
        "bytes": backup_path.stat().st_size,
        "created_at": created_at.isoformat(),
        "database_url": resolved_database_url,
        "source_path": str(source_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ta")
    parser.add_argument("--base-url", default=os.getenv("TRACK_ANYWHERE_API", "http://localhost:8000"))
    parser.add_argument("--token", default=None, help="Bearer token. Prefer OS keyring; this is for one-shot use.")
    parser.add_argument("--insecure-automation", action="store_true", help="Allow env-token automation with warning.")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login")
    login.add_argument("token")

    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_login = auth_sub.add_parser("login")
    auth_login.add_argument("token")
    auth_dev = auth_sub.add_parser("dev-token")
    auth_dev.add_argument("--json", action="store_true")
    auth_status = auth_sub.add_parser("status")
    auth_status.add_argument("--json", action="store_true")

    data = sub.add_parser("data")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    data_backup = data_sub.add_parser("backup")
    data_backup.add_argument("--database-url", default=None)
    data_backup.add_argument("--output-dir")
    data_backup.add_argument("--label")
    data_backup.add_argument("--json", action="store_true")

    summary = sub.add_parser("summary")
    summary_sub = summary.add_subparsers(dest="summary_command", required=True)
    summary_accounts = summary_sub.add_parser("accounts")
    summary_accounts.add_argument("--group-by", default="subtype")
    summary_accounts.add_argument("--currency")
    summary_accounts.add_argument("--institution-type")
    summary_accounts.add_argument("--include-system", action="store_true")
    summary_accounts.add_argument("--json", action="store_true")

    user_group = sub.add_parser("user")
    user_sub = user_group.add_subparsers(dest="user_command", required=True)
    user_create = user_sub.add_parser("create")
    user_create.add_argument("username")
    user_create.add_argument("--display-name")
    user_create.add_argument("--idempotency-key")
    user_create.add_argument("--json", action="store_true")
    user_list = user_sub.add_parser("list")
    user_list.add_argument("--json", action="store_true")

    account_group = sub.add_parser("account")
    account_sub = account_group.add_subparsers(dest="account_command", required=True)
    account_create = account_sub.add_parser("create")
    account_create.add_argument("name")
    account_create.add_argument("--type", default="asset")
    account_create.add_argument("--currency", default="CNY")
    account_create.add_argument("--opening-balance", default="0")
    account_create.add_argument("--institution-type")
    account_create.add_argument("--subtype")
    account_create.add_argument("--institution")
    account_create.add_argument("--idempotency-key")
    account_create.add_argument("--json", action="store_true")
    account_list = account_sub.add_parser("list")
    account_list.add_argument("--name")
    account_list.add_argument("--type")
    account_list.add_argument("--currency")
    account_list.add_argument("--institution-type")
    account_list.add_argument("--subtype")
    account_list.add_argument("--institution")
    account_list.add_argument("--json", action="store_true")
    account_find = account_sub.add_parser("find")
    account_find.add_argument("--name", required=True)
    account_find.add_argument("--type")
    account_find.add_argument("--currency")
    account_find.add_argument("--institution-type")
    account_find.add_argument("--subtype")
    account_find.add_argument("--institution")
    account_find.add_argument("--json", action="store_true")
    account_show = account_sub.add_parser("show")
    account_show.add_argument("account_id")
    account_show.add_argument("--json", action="store_true")
    account_update = account_sub.add_parser("update")
    account_update.add_argument("account_id")
    account_update.add_argument("--institution-type")
    account_update.add_argument("--subtype")
    account_update.add_argument("--institution")
    account_update.add_argument("--idempotency-key")
    account_update.add_argument("--json", action="store_true")
    account_balance = account_sub.add_parser("balance")
    account_balance.add_argument("account_id")
    account_balance.add_argument("--include-drafts", action="store_true")
    account_balance.add_argument("--json", action="store_true")
    account_adjust = account_sub.add_parser("adjust")
    account_adjust.add_argument("account_id")
    account_adjust.add_argument("--amount", required=True, help="Delta to apply to the account balance; negative values decrease it.")
    account_adjust.add_argument("--purpose", required=True)
    account_adjust.add_argument("--occurred-at")
    account_adjust.add_argument("--currency", default="CNY")
    account_adjust.add_argument("--idempotency-key")
    account_adjust.add_argument("--json", action="store_true")

    account = sub.add_parser("account-create")
    account.add_argument("name")
    account.add_argument("--type", default="asset")
    account.add_argument("--currency", default="CNY")
    account.add_argument("--opening-balance", default="0")
    account.add_argument("--institution-type")
    account.add_argument("--subtype")
    account.add_argument("--institution")
    account.add_argument("--idempotency-key")
    account.add_argument("--json", action="store_true")

    capture = sub.add_parser("capture")
    capture.add_argument("memo")
    capture.add_argument("--amount", type=str)
    capture.add_argument("--source-account-id")
    capture.add_argument("--expense-account-id")
    capture.add_argument("--currency", default="CNY")
    capture.add_argument("--idempotency-key")
    capture.add_argument("--dry-run", action="store_true")
    capture.add_argument("--json", action="store_true")

    confirm = sub.add_parser("draft-confirm")
    confirm.add_argument("draft_id")
    confirm.add_argument("--expected-version", type=int, required=True)
    confirm.add_argument("--idempotency-key")
    confirm.add_argument("--json", action="store_true")

    tx = sub.add_parser("tx")
    tx_sub = tx.add_subparsers(dest="tx_command", required=True)
    tx_record = tx_sub.add_parser("record")
    tx_record.add_argument("--amount", required=True)
    tx_record.add_argument("--from-account-id", "--from", dest="from_account_id", required=True)
    tx_record.add_argument("--to-account-id", "--to", dest="to_account_id", required=True)
    tx_record.add_argument("--purpose", required=True)
    tx_record.add_argument("--occurred-at")
    tx_record.add_argument("--currency", default="CNY")
    tx_record.add_argument("--idempotency-key")
    tx_record.add_argument("--json", action="store_true")
    tx_list = tx_sub.add_parser("list")
    tx_list.add_argument("--account-id")
    tx_list.add_argument("--limit", type=int, default=20)
    tx_list.add_argument("--json", action="store_true")
    tx_show = tx_sub.add_parser("show")
    tx_show.add_argument("transaction_id")
    tx_show.add_argument("--json", action="store_true")

    record = sub.add_parser("record")
    record.add_argument("--amount", required=True)
    record.add_argument("--from-account-id", "--from", dest="from_account_id", required=True)
    record.add_argument("--to-account-id", "--to", dest="to_account_id", required=True)
    record.add_argument("--purpose", required=True)
    record.add_argument("--occurred-at")
    record.add_argument("--currency", default="CNY")
    record.add_argument("--idempotency-key")
    record.add_argument("--json", action="store_true")

    balance_adjust = sub.add_parser("balance-adjust")
    balance_adjust.add_argument("account_id")
    balance_adjust.add_argument("--amount", required=True, help="Delta to apply to the account balance; negative values decrease it.")
    balance_adjust.add_argument("--purpose", required=True)
    balance_adjust.add_argument("--occurred-at")
    balance_adjust.add_argument("--currency", default="CNY")
    balance_adjust.add_argument("--idempotency-key")
    balance_adjust.add_argument("--json", action="store_true")

    query = sub.add_parser("balance")
    query.add_argument("account_id")
    query.add_argument("--include-drafts", action="store_true")
    query.add_argument("--json", action="store_true")
    return parser


def resolve_token(args: argparse.Namespace) -> str | None:
    if args.token:
        return args.token
    env_token = os.getenv("TRACK_ANYWHERE_TOKEN")
    if env_token:
        if not args.insecure_automation:
            raise RuntimeError("TRACK_ANYWHERE_TOKEN requires --insecure-automation; prefer OS keyring")
        print("warning: using insecure env-token automation", file=sys.stderr)
        return env_token
    return TokenStore().load()


def request_json(config: CliConfig, method: str, path: str, payload: dict[str, Any] | None = None, key: str | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    if key:
        headers["X-Idempotency-Key"] = key
    req = urllib.request.Request(f"{config.base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except Exception:
            parsed = {"detail": str(exc)}
        return exc.code, parsed


def with_query(path: str, params: dict[str, Any]) -> str:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
    return f"{path}?{query}" if query else path


def exit_for_status(status: int, detail: Any) -> int:
    if status < 400:
        return EXIT_SUCCESS
    text = json.dumps(detail)
    if status == 401:
        return EXIT_AUTH
    if status == 403:
        return EXIT_POLICY_DENIED
    if status == 409 and "idempotency" in text:
        return EXIT_IDEMPOTENCY_CONFLICT
    if status == 409:
        return EXIT_STALE_VERSION
    if status == 404:
        return EXIT_NOT_FOUND
    if status == 400:
        return EXIT_SECURITY_PRECONDITION
    return EXIT_VALIDATION


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "login" or (args.command == "auth" and args.auth_command == "login"):
        TokenStore().save(args.token)
        print("token saved")
        return EXIT_SUCCESS

    if args.command == "auth" and args.auth_command == "dev-token":
        config = CliConfig(base_url=args.base_url, token=None, insecure_automation=args.insecure_automation)
        status, data = request_json(config, "POST", "/api/v1/auth/dev-token")
        if status < 400:
            TokenStore().save(data["token"])
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print("token saved" if status < 400 else data)
        return exit_for_status(status, data)

    if args.command == "auth" and args.auth_command == "status":
        token = resolve_token(args)
        data = {
            "authenticated": token is not None,
            "base_url": args.base_url,
            "token_source": "configured" if token else None,
        }
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print("authenticated" if token else "not authenticated")
        return EXIT_SUCCESS

    if args.command == "data" and args.data_command == "backup":
        try:
            data = create_sqlite_backup(args.database_url, args.output_dir, args.label)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_VALIDATION
        if args.json:
            print(json.dumps({"backup": data}, indent=2, sort_keys=True))
        else:
            print(f"backup created: {data['backup_path']}")
        return EXIT_SUCCESS

    try:
        token = resolve_token(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_AUTH

    config = CliConfig(base_url=args.base_url, token=token, insecure_automation=args.insecure_automation)
    if args.command == "user" and args.user_command == "create":
        payload = {"username": args.username, "display_name": args.display_name}
        status, data = request_json(
            config,
            "POST",
            "/api/v1/users",
            payload,
            key=command_idempotency_key(args, "user-create"),
        )
    elif args.command == "user" and args.user_command == "list":
        status, data = request_json(config, "GET", "/api/v1/users")
    elif args.command == "summary" and args.summary_command == "accounts":
        status, data = request_json(
            config,
            "GET",
            with_query(
                "/api/v1/summary/accounts",
                {
                    "group_by": args.group_by,
                    "currency": args.currency,
                    "institution_type": args.institution_type,
                    "include_system": "true" if args.include_system else None,
                },
            ),
        )
    elif args.command == "account" and args.account_command == "list":
        status, data = request_json(
            config,
            "GET",
            with_query(
                "/api/v1/accounts",
                {
                    "name": args.name,
                    "type": args.type,
                    "currency": args.currency,
                    "institution_type": args.institution_type,
                    "subtype": args.subtype,
                    "institution": args.institution,
                },
            ),
        )
    elif args.command == "account" and args.account_command == "find":
        status, data = request_json(
            config,
            "GET",
            with_query(
                "/api/v1/accounts",
                {
                    "name": args.name,
                    "type": args.type,
                    "currency": args.currency,
                    "institution_type": args.institution_type,
                    "subtype": args.subtype,
                    "institution": args.institution,
                },
            ),
        )
    elif args.command == "account" and args.account_command == "show":
        status, data = request_json(config, "GET", f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}")
    elif args.command == "account" and args.account_command == "update":
        payload = {
            key: value
            for key, value in {
                "institution_type": args.institution_type,
                "subtype": args.subtype,
                "institution": args.institution,
            }.items()
            if value is not None
        }
        status, data = request_json(
            config,
            "PATCH",
            f"/api/v1/accounts/{urllib.parse.quote(args.account_id)}",
            payload,
            key=command_idempotency_key(args, "account-update"),
        )
    elif args.command == "account-create" or (args.command == "account" and args.account_command == "create"):
        status, data = request_json(
            config,
            "POST",
            "/api/v1/accounts",
            {
                key: value
                for key, value in {
                    "name": args.name,
                    "type": args.type,
                    "currency": args.currency,
                    "opening_balance": args.opening_balance,
                    "institution_type": args.institution_type,
                    "subtype": args.subtype,
                    "institution": args.institution,
                }.items()
                if value is not None
            },
            key=command_idempotency_key(args, "account-create"),
        )
    elif args.command == "capture":
        payload = {
            "memo": args.memo,
            "amount": args.amount,
            "currency": args.currency,
            "source_account_id": args.source_account_id,
            "expense_account_id": args.expense_account_id,
        }
        if args.dry_run:
            data = {"dry_run": True, "policy_decision": "would_create_draft", "payload": payload}
            status = 200
        else:
            status, data = request_json(config, "POST", "/api/v1/drafts/capture", payload, key=command_idempotency_key(args, "draft-capture"))
    elif args.command == "draft-confirm":
        status, data = request_json(
            config,
            "POST",
            "/api/v1/drafts/confirm",
            {"draft_id": args.draft_id, "expected_version": args.expected_version},
            key=command_idempotency_key(args, "draft-confirm"),
        )
    elif args.command in {"record"} or (args.command == "tx" and args.tx_command == "record"):
        payload = {
            "amount": args.amount,
            "currency": args.currency,
            "from_account_id": args.from_account_id,
            "to_account_id": args.to_account_id,
            "purpose": args.purpose,
        }
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        status, data = request_json(
            config,
            "POST",
            "/api/v1/ledger/transactions",
            payload,
            key=command_idempotency_key(args, "tx-record"),
        )
    elif args.command == "tx" and args.tx_command == "list":
        status, data = request_json(
            config,
            "GET",
            with_query("/api/v1/ledger/transactions", {"account_id": args.account_id, "limit": args.limit}),
        )
    elif args.command == "tx" and args.tx_command == "show":
        status, data = request_json(config, "GET", f"/api/v1/ledger/transactions/{urllib.parse.quote(args.transaction_id)}")
    elif args.command == "balance-adjust" or (args.command == "account" and args.account_command == "adjust"):
        payload = {
            "account_id": args.account_id,
            "amount": args.amount,
            "currency": args.currency,
            "purpose": args.purpose,
        }
        if args.occurred_at:
            payload["occurred_at"] = args.occurred_at
        status, data = request_json(
            config,
            "POST",
            "/api/v1/ledger/adjustments",
            payload,
            key=command_idempotency_key(args, "balance-adjust"),
        )
    elif args.command == "balance" or (args.command == "account" and args.account_command == "balance"):
        suffix = "?include_drafts=true" if args.include_drafts else ""
        status, data = request_json(config, "GET", f"/api/v1/query/accounts/{args.account_id}/balance{suffix}")
    else:
        parser.error("unknown command")

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(data)
    return exit_for_status(status, data)


if __name__ == "__main__":
    raise SystemExit(main())
