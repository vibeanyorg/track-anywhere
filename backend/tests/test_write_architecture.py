from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import event

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


SERVICE_MODULES = Path("backend/app/track_anywhere").glob("service_*.py")


def test_api_write_use_cases_do_not_call_legacy_full_snapshot_persistence():
    offenders: list[str] = []
    pattern = re.compile(r"\bself\._persist\(\)")
    for path in SERVICE_MODULES:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path}:{line_number}: {line.strip()}")

    assert offenders == []


def test_legacy_full_snapshot_helpers_are_not_exposed_as_generic_save_methods():
    repo_root = Path.cwd()
    offenders: list[str] = []
    forbidden_patterns = [
        re.compile(r"\bdef _persist\("),
        re.compile(r"\bservice\._persist\(\)"),
        re.compile(r"\bdef save\(self, service"),
        re.compile(r"\bstorage\.save\s*="),
        re.compile(r"\bstorage\.save\("),
    ]
    for root_name in ("backend/app", "backend/tests"):
        for path in (repo_root / root_name).rglob("*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if any(pattern.search(line) for pattern in forbidden_patterns):
                    offenders.append(f"{path.relative_to(repo_root)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_common_writes_do_not_depend_on_legacy_full_snapshot_persistence(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token

    def fail_full_snapshot(_service):
        raise AssertionError("API write path called legacy full snapshot persistence")

    service.storage.save_full_snapshot_for_legacy_bootstrap = fail_full_snapshot

    cash, _ = service.create_account(
        token,
        {"name": "Scoped Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="scoped-cash",
    )
    expense_account, _ = service.create_account(
        token,
        {"name": "Scoped Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="scoped-expense-account",
    )
    category, _ = service.ensure_category_path(
        token,
        {"kind": "expense", "path": "Food / Lunch"},
        idempotency_key="scoped-food-lunch",
    )
    service.record_transaction(
        token,
        {
            "amount": "5",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": expense_account.account_id,
            "purpose": "direct write",
        },
        idempotency_key="scoped-transfer",
    )
    service.record_expense(
        token,
        {
            "amount": "7",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category["category"].category_id,
            "purpose": "category write",
        },
        idempotency_key="scoped-expense",
    )
    service.adjust_balance(
        token,
        {
            "account_id": cash.account_id,
            "amount": "3",
            "currency": "CNY",
            "purpose": "adjust write",
        },
        idempotency_key="scoped-adjust",
    )
    card, _ = service.create_account(
        token,
        {
            "name": "Scoped Card",
            "type": "liability",
            "currency": "CNY",
            "subtype": "credit_card",
            "institution_type": "bank",
            "institution": "Scoped Bank",
        },
        idempotency_key="scoped-card-account",
    )
    service.create_payment_instrument(
        token,
        {
            "slug": "scoped-card",
            "display_name": "Scoped Card",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "1234",
        },
        idempotency_key="scoped-card-instrument",
    )


def test_record_transaction_write_scope_has_small_sql_budget(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Budget Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="budget-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Budget Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="budget-expense",
    )
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.lower().split()))

    event.listen(service.storage.engine, "before_cursor_execute", capture_statement)
    try:
        service.record_transaction(
            token,
            {
                "amount": "8",
                "currency": "CNY",
                "from_account_id": cash.account_id,
                "to_account_id": expense.account_id,
                "purpose": "budgeted write",
            },
            idempotency_key="budget-record",
        )
    finally:
        event.remove(service.storage.engine, "before_cursor_execute", capture_statement)

    writes = [statement for statement in statements if statement.startswith(("insert", "update", "delete"))]
    forbidden_tables = {
        "accounts",
        "assets",
        "categories",
        "recurring_items",
        "payment_instruments",
        "payment_profiles",
        "credentials",
    }
    forbidden_writes = [
        statement
        for statement in writes
        for table in forbidden_tables
        if f" {table} " in statement or f" {table}(" in statement
    ]

    assert len(statements) <= 18
    assert forbidden_writes == []
