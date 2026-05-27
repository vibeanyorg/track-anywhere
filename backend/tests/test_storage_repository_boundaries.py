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


def test_write_benchmark_does_not_depend_on_legacy_storage_writer_privates():
    source = (REPO_ROOT / "scripts/benchmark-write-performance.py").read_text()

    assert "storage._save_accounts" not in source
    assert "storage._save_transactions" not in source
    assert "storage._save_audit_events" not in source
    assert "storage.unit_of_work()" in source
