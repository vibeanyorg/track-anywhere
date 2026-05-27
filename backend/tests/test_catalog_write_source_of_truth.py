from __future__ import annotations

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_account_metadata_update_uses_storage_truth_when_memory_map_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {
            "name": "Metadata Truth Account",
            "type": "asset",
            "currency": "CNY",
            "institution_type": "bank",
            "subtype": "deposit",
            "institution": "Truth Bank",
        },
        idempotency_key="metadata-truth-account",
    )
    original_book_id = account.book_id
    service.ledger.accounts[account.account_id].book_id = "stale_book"
    service.ledger.accounts[account.account_id].subtype = "stale_subtype"

    updated, replay = service.update_account_metadata(
        token,
        account.account_id,
        {"subtype": "debit_card", "institution": "Storage Truth Bank"},
        idempotency_key="metadata-truth-update",
    )

    persisted = service.get_account(token, account.account_id)
    assert replay is False
    assert updated.book_id == original_book_id
    assert updated.subtype == "debit_card"
    assert persisted.subtype == "debit_card"
    assert persisted.institution == "Storage Truth Bank"


def test_payment_instrument_create_uses_storage_truth_when_memory_map_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "Instrument Truth Card",
            "type": "liability",
            "currency": "CNY",
            "subtype": "credit_card",
            "institution_type": "bank",
            "institution": "Truth Bank",
        },
        idempotency_key="instrument-truth-card",
    )
    original_book_id = card.book_id
    service.ledger.accounts[card.account_id].book_id = "stale_book"
    service.ledger.accounts[card.account_id].subtype = "debit_card"

    instrument, replay = service.create_payment_instrument(
        token,
        {
            "slug": "instrument-truth-card",
            "display_name": "Instrument Truth Card",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "2862",
        },
        idempotency_key="instrument-truth-create",
    )

    assert replay is False
    assert instrument.account_id == card.account_id
    assert instrument.book_id == original_book_id
