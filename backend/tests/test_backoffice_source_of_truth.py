from __future__ import annotations

import re
from pathlib import Path

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_backoffice_use_case_reads_backoffice_models_from_storage():
    source = Path("backend/app/track_anywhere/service_backoffice.py").read_text()
    offenders = []
    forbidden = re.compile(r"\bself\.(books|users|auth_identities|categories|recurring|audit)\b")
    for line_number, line in enumerate(source.splitlines(), start=1):
        if forbidden.search(line):
            offenders.append(f"service_backoffice.py:{line_number}: {line.strip()}")

    assert offenders == []


def test_backoffice_reads_storage_truth_when_service_mirrors_are_stale(tmp_path):
    service = FinanceService(
        DeploymentSecurityConfig(),
        database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}",
    )
    token = service.owner_token

    book, _ = service.create_book(
        token,
        {"name": "Storage Truth Book", "kind": "personal", "base_currency": "CNY"},
        idempotency_key="backoffice-book-storage-truth",
    )
    parent, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Storage Truth Parent"},
        idempotency_key="backoffice-category-parent-storage-truth",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Storage Truth Child", "parent_id": parent.category_id},
        idempotency_key="backoffice-category-child-storage-truth",
    )
    account, _ = service.create_account(
        token,
        {"name": "Storage Truth Recurring Wallet", "type": "asset", "currency": "USD"},
        idempotency_key="backoffice-recurring-account-storage-truth",
    )
    recurring, _ = service.create_recurring_item(
        token,
        {
            "name": "Storage Truth Subscription",
            "kind": "paid",
            "amount": "9.99",
            "currency": "USD",
            "recurrence": {"type": "monthly_day", "day": 15},
            "anchor_date": "2026-06-15",
            "reminder_days": [3],
            "source_account_id": account.account_id,
            "category_id": category.category_id,
        },
        idempotency_key="backoffice-recurring-storage-truth",
    )
    login = service.login_oauth_identity(
        OAuthIdentity(
            provider="google",
            subject="backoffice-storage-truth",
            email="storage-truth@example.com",
            email_verified=True,
            name="Storage Truth User",
            picture=None,
        ),
        role="viewer",
    )

    service.books.books[book.book_id].name = "stale memory book"
    service.books.members[(book.book_id, "owner")].role = "stale"
    service.categories.categories[category.category_id].name = "stale memory category"
    service.recurring.items[recurring.recurring_id].name = "stale memory recurring"
    service.users.users[login["user"]["user_id"]].display_name = "stale memory user"
    service.auth_identities.identities[login["identity"]["identity_id"]].email = "stale@example.com"

    assert any(
        item.book_id == book.book_id and item.name == "Storage Truth Book"
        for item in service.backoffice_books()
    )
    assert any(
        item.book_id == book.book_id and item.role == "owner"
        for item in service.backoffice_book_members()
    )
    assert any(
        item.category_id == category.category_id and item.name == "Storage Truth Child"
        for item in service.backoffice_categories()
    )
    assert any(
        item.recurring_id == recurring.recurring_id and item.name == "Storage Truth Subscription"
        for item in service.backoffice_recurring_items()
    )
    assert any(
        item.user_id == login["user"]["user_id"] and item.display_name == "Storage Truth User"
        for item in service.backoffice_users()
    )
    assert any(
        item.identity_id == login["identity"]["identity_id"] and item.email == "storage-truth@example.com"
        for item in service.backoffice_auth_identities()
    )
