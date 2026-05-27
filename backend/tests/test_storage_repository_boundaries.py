from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_ledger_repository_owns_hot_ledger_writes():
    source = (BACKEND / "storage_repositories/ledger.py").read_text()

    assert "def save_accounts" in source
    assert "def save_transactions" in source
    assert "self.storage._save_accounts" not in source
    assert "self.storage._save_transactions" not in source
    assert "self.storage._save_transaction_postings" not in source
    assert "self.storage._replace_transaction_lines" not in source


def test_workflow_repositories_own_workflow_writes():
    source = (BACKEND / "storage_repositories/workflow.py").read_text()

    assert "def save(self, drafts" in source
    assert "def save_items" in source
    assert "self.storage._save_drafts" not in source
    assert "self.storage._save_recurring_items" not in source


def test_finance_repositories_own_finance_writes():
    source = (BACKEND / "storage_repositories/finance.py").read_text()

    assert "def save(self, funds" in source
    assert "def save(self, budgets" in source
    assert "def save_events" in source
    assert "def save_valuations" in source
    assert "self.storage._save_funds" not in source
    assert "self.storage._save_budgets" not in source
    assert "self.storage._save_investment_events" not in source
    assert "self.storage._save_investment_valuations" not in source


def test_catalog_repositories_own_catalog_writes():
    source = (BACKEND / "storage_repositories/catalog.py").read_text()

    assert "def save(self, assets" in source
    assert "def save(self, books" in source
    assert "def save(self, categories" in source
    assert "def save_history" in source
    assert "def save(self, counterparties" in source
    assert "def save(self, instruments" in source
    assert "def save(self, profiles" in source
    forbidden = [
        "self.storage._save_assets",
        "self.storage._save_books",
        "self.storage._save_categories",
        "self.storage._save_category_history",
        "self.storage._save_counterparties",
        "self.storage._save_payment_instruments",
        "self.storage._save_payment_profiles",
    ]
    assert all(item not in source for item in forbidden)


def test_security_repositories_own_security_writes():
    source = (BACKEND / "storage_repositories/security.py").read_text()

    assert "def save_events" in source
    assert "def save(self, credentials" in source
    assert "def save_receipts" in source
    assert "self.storage._save_audit_events" not in source
    assert "self.storage._save_credentials" not in source
    assert "self.storage._save_idempotency_receipts" not in source


def test_write_benchmark_does_not_depend_on_legacy_storage_writer_privates():
    source = (REPO_ROOT / "scripts/benchmark-write-performance.py").read_text()

    assert "storage._save_accounts" not in source
    assert "storage._save_transactions" not in source
    assert "storage._save_audit_events" not in source
    assert "storage.unit_of_work()" in source
