from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, Lock
from time import sleep

import pytest

from track_anywhere.idempotency import IdempotencyStore
from track_anywhere.errors import StaleVersion, ValidationError
from track_anywhere.security import Actor, DeploymentSecurityConfig
from track_anywhere.service import FinanceService
from track_anywhere.storage_models import TransactionRecord


class _RecordingAttachmentScanner:
    def __init__(self) -> None:
        self.contents: list[bytes] = []

    def scan(self, content: bytes) -> None:
        self.contents.append(content)


def test_failed_persistence_does_not_poison_idempotency_retry(tmp_path, monkeypatch):
    service = FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{tmp_path / 'idempotency-retry.sqlite3'}",
    )
    original_save = service.storage.save_draft_change
    attempts = 0

    def fail_once(changes):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        return original_save(changes)

    monkeypatch.setattr(service.storage, "save_draft_change", fail_once)

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.capture_draft(
            service.owner_token,
            {"memo": "retry me", "currency": "CNY"},
            idempotency_key="retry-after-storage-failure",
        )

    draft, replay = service.capture_draft(
        service.owner_token,
        {"memo": "retry me", "currency": "CNY"},
        idempotency_key="retry-after-storage-failure",
    )

    assert replay is False
    assert attempts == 2
    with service.storage.unit_of_work() as uow:
        assert uow.drafts.get_draft(draft.draft_id) is not None


def test_concurrent_same_idempotency_key_runs_side_effect_once():
    store = IdempotencyStore()
    actor = Actor("owner", "user", frozenset({"ledger:confirm"}))
    counter_lock = Lock()
    executions = 0

    def side_effect():
        nonlocal executions
        with counter_lock:
            executions += 1
        sleep(0.05)
        return "recorded"

    def invoke():
        result = store.run(
            key="same-key",
            actor=actor,
            operation="expense.record",
            request_hash="same-request",
            fn=side_effect,
        )
        if result[1] is False:
            store.mark_clean()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: invoke(), range(2)))

    assert executions == 1
    assert sorted(replay for _, replay in results) == [False, True]


def test_different_idempotent_writes_do_not_share_a_commit_boundary():
    store = IdempotencyStore()
    actor = Actor("owner", "user", frozenset({"ledger:confirm"}))
    first_started = Event()
    release_first = Event()
    second_executed = Event()

    def first_side_effect():
        first_started.set()
        assert release_first.wait(timeout=2)
        return "first"

    def first_write():
        result = store.run(
            key="first-key",
            actor=actor,
            operation="expense.record",
            request_hash="first-request",
            fn=first_side_effect,
        )
        store.mark_clean()
        return result

    def second_write():
        result = store.run(
            key="second-key",
            actor=actor,
            operation="expense.record",
            request_hash="second-request",
            fn=lambda: second_executed.set() or "second",
        )
        store.mark_clean()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_write)
        assert first_started.wait(timeout=2)
        second_future = executor.submit(second_write)
        sleep(0.05)
        second_ran_before_first_commit = second_executed.is_set()
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert second_ran_before_first_commit is False


def test_stale_draft_confirmation_cannot_create_a_second_transaction(tmp_path, monkeypatch):
    service = FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{tmp_path / 'draft-cas.sqlite3'}",
    )
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="draft-cas-cash",
    )
    expense, _ = service.create_account(
        service.owner_token,
        {"name": "Food", "type": "expense", "currency": "CNY"},
        idempotency_key="draft-cas-expense",
    )
    draft, _ = service.capture_draft(
        service.owner_token,
        {
            "memo": "one coffee",
            "amount": "20",
            "currency": "CNY",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
        },
        idempotency_key="draft-cas-capture",
    )
    stale_draft = deepcopy(draft)
    service.confirm_draft(
        service.owner_token,
        {"draft_id": draft.draft_id, "expected_version": 1},
        idempotency_key="draft-cas-first-confirm",
    )
    monkeypatch.setattr(service, "_stored_draft", lambda _draft_id: deepcopy(stale_draft))

    with pytest.raises(StaleVersion, match="draft version conflict"):
        service.confirm_draft(
            service.owner_token,
            {"draft_id": draft.draft_id, "expected_version": 1},
            idempotency_key="draft-cas-second-confirm",
        )

    assert service.storage.confirmed_transaction_count(book_id=draft.book_id) == 1


def test_stale_transaction_cannot_be_reversed_twice(tmp_path, monkeypatch):
    service = FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{tmp_path / 'reversal-cas.sqlite3'}",
    )
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="reversal-cas-cash",
    )
    category, _ = service.create_category(
        service.owner_token,
        {"kind": "expense", "name": "Food"},
        idempotency_key="reversal-cas-category",
    )
    transaction, _ = service.record_expense(
        service.owner_token,
        {
            "amount": "20",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "coffee",
        },
        idempotency_key="reversal-cas-expense",
    )
    stale_transaction = deepcopy(transaction)
    service.reverse_transaction(
        service.owner_token,
        {"transaction_id": transaction.transaction_id, "memo": "first reversal"},
        idempotency_key="reversal-cas-first",
    )
    monkeypatch.setattr(
        service,
        "_get_transaction_from_storage",
        lambda _transaction_id: deepcopy(stale_transaction),
    )

    with pytest.raises((StaleVersion, ValidationError)):
        service.reverse_transaction(
            service.owner_token,
            {"transaction_id": transaction.transaction_id, "memo": "second reversal"},
            idempotency_key="reversal-cas-second",
        )

    assert service.storage.confirmed_transaction_count(book_id=transaction.book_id) == 2


def test_database_enforces_one_reversal_per_original_transaction():
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in TransactionRecord.__table__.constraints
        if getattr(constraint, "unique", False) or constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("reverses_transaction_id",) in unique_column_sets


def test_attachment_content_is_scanned_and_survives_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'attachments.sqlite3'}"
    scanner = _RecordingAttachmentScanner()
    config = DeploymentSecurityConfig(
        mode="production",
        tls_enabled=True,
        key_provider_configured=True,
        backup_encryption_documented=True,
        attachment_scanner_available=True,
    )
    service = FinanceService(config, database_url=database_url, attachment_scanner=scanner)
    content = b"\x89PNG\r\n\x1a\nreal receipt bytes"

    result, _ = service.upload_attachment(
        service.owner_token,
        filename="receipt.png",
        mime_type="image/png",
        content=content,
        idempotency_key="persist-real-attachment",
    )
    attachment_id = result["attachment"].attachment_id
    restarted = FinanceService(config, database_url=database_url, attachment_scanner=scanner)

    assert scanner.contents == [content]
    assert restarted.storage.attachment_content(attachment_id) == content
