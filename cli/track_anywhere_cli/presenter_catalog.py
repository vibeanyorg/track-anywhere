from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.table import Table

from .presenter_base import _as_dict, _stringify, empty_panel, object_summary


def account_list(data: Any) -> Table | Panel:
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    if not accounts:
        return empty_panel("Accounts", "No accounts found.")
    table = Table(title="Accounts")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Currency")
    table.add_column("Balance", justify="right")
    for account in accounts:
        if not isinstance(account, dict):
            continue
        table.add_row(
            _stringify(account.get("account_id")),
            _stringify(account.get("name")),
            _stringify(account.get("type")),
            _stringify(account.get("currency")),
            _stringify(account.get("balance", account.get("current_balance"))),
        )
    return table


def summary_accounts(data: Any) -> Table | Panel:
    groups = _as_dict(data).get("groups", [])
    if not groups:
        return empty_panel("Account summary", "No account summary rows found.")
    table = Table(title="Account summary")
    table.add_column("Group")
    table.add_column("Currency")
    table.add_column("Assets", justify="right")
    table.add_column("Liabilities", justify="right")
    table.add_column("Net", justify="right")
    for row in groups:
        if not isinstance(row, dict):
            continue
        table.add_row(
            _stringify(row.get("group", row.get("key"))),
            _stringify(row.get("currency")),
            _stringify(row.get("asset_amount", row.get("amount", ""))),
            _stringify(row.get("liability_amount", "")),
            _stringify(row.get("net_amount", row.get("amount", ""))),
        )
    return table


def summary_categories(data: Any) -> Table | Panel:
    groups = _as_dict(data).get("groups", _as_dict(data).get("categories", []))
    if not groups:
        return empty_panel("Category summary", "No category summary rows found.")
    table = Table(title="Category summary")
    table.add_column("Category ID")
    table.add_column("Kind")
    table.add_column("Primary")
    table.add_column("Secondary")
    table.add_column("Currency")
    table.add_column("Amount", justify="right")
    table.add_column("Tx", justify="right")
    for row in groups:
        if not isinstance(row, dict):
            continue
        table.add_row(
            _stringify(row.get("category_id")),
            _stringify(row.get("kind")),
            _stringify(row.get("primary")),
            _stringify(row.get("secondary")),
            _stringify(row.get("currency")),
            _stringify(row.get("amount")),
            _stringify(row.get("transaction_count")),
        )
    return table


def user_list(data: Any) -> Table | Panel:
    users = _as_dict(data).get("users", [])
    if not users:
        return empty_panel("Users", "No users found.")
    table = Table(title="Users")
    table.add_column("ID")
    table.add_column("Username")
    table.add_column("Display Name")
    for user in users:
        if not isinstance(user, dict):
            continue
        table.add_row(
            _stringify(user.get("user_id")),
            _stringify(user.get("username")),
            _stringify(user.get("display_name")),
        )
    return table


def user_summary(data: Any) -> Table:
    user = _as_dict(_as_dict(data).get("user"))
    return object_summary(
        "User",
        [
            ("User ID", user.get("user_id")),
            ("Username", user.get("username")),
            ("Display Name", user.get("display_name")),
        ],
    )


def category_list(data: Any) -> Table | Panel:
    categories = _as_dict(data).get("categories", [])
    if not categories:
        return empty_panel("Categories", "No categories found.")
    table = Table(title="Categories")
    table.add_column("ID")
    table.add_column("Kind")
    table.add_column("Primary")
    table.add_column("Secondary")
    for category in categories:
        if not isinstance(category, dict):
            continue
        table.add_row(
            _stringify(category.get("category_id")),
            _stringify(category.get("kind")),
            _stringify(category.get("primary")),
            _stringify(category.get("secondary")),
        )
    return table


def category_summary(data: Any) -> Table:
    category = _as_dict(_as_dict(data).get("category"))
    return object_summary(
        "Category",
        [
            ("Category ID", category.get("category_id")),
            ("Kind", category.get("kind")),
            ("Primary", category.get("primary")),
            ("Secondary", category.get("secondary")),
        ],
    )


def credit_card_list(data: Any) -> Table | Panel:
    cards = _as_dict(data).get("credit_cards", [])
    if not cards:
        return empty_panel("Credit cards", "No credit cards found.")
    table = Table(title="Credit cards")
    table.add_column("Account ID")
    table.add_column("Name")
    table.add_column("Currency")
    table.add_column("Balance")
    table.add_column("Credit limit")
    table.add_column("Available credit")
    table.add_column("Utilization")
    for card in cards:
        if not isinstance(card, dict):
            continue
        account = _as_dict(card.get("account"))
        table.add_row(
            _stringify(account.get("account_id")),
            _stringify(account.get("name")),
            _stringify(account.get("currency")),
            _stringify(card.get("current_balance")),
            _stringify(card.get("credit_limit")),
            _stringify(card.get("available_credit")),
            _stringify(card.get("utilization_rate")),
        )
    return table


def credit_card_summary(data: Any) -> Table:
    card = _as_dict(_as_dict(data).get("credit_card"))
    account = _as_dict(card.get("account"))
    return object_summary(
        "Credit card",
        [
            ("Account ID", account.get("account_id")),
            ("Name", account.get("name")),
            ("Type", account.get("type")),
            ("Currency", account.get("currency")),
            ("Balance", card.get("current_balance")),
            ("Credit limit", card.get("credit_limit")),
            ("Available credit", card.get("available_credit")),
            ("Derived available credit", card.get("derived_available_credit")),
            ("Utilization", card.get("utilization_rate")),
        ],
    )


def account_entity_summary(data: Any) -> Table:
    account = _as_dict(_as_dict(data).get("account"))
    return object_summary(
        "Account",
        [
            ("Account ID", account.get("account_id")),
            ("Name", account.get("name")),
            ("Type", account.get("type")),
            ("Currency", account.get("currency")),
            ("Institution type", account.get("institution_type")),
            ("Subtype", account.get("subtype")),
            ("Institution", account.get("institution")),
            ("Version", account.get("version")),
        ],
    )


def account_balance_summary(data: Any) -> Table:
    payload = _as_dict(data)
    official = _as_dict(payload.get("official_balance"))
    projected = _as_dict(payload.get("projected_balance"))
    provenance = _as_dict(payload.get("provenance"))
    fields = [
        ("Account ID", payload.get("account_id")),
        ("Currency", payload.get("currency")),
        ("Official balance", official.get("amount")),
        ("Balance source", official.get("source")),
        ("As-of ledger version", official.get("as_of_ledger_version")),
    ]
    if projected:
        fields.extend(
            [
                ("Projected balance", projected.get("amount")),
                ("Pending impact", projected.get("pending_impact")),
                ("Projection version", projected.get("projection_version")),
                ("Included draft ids", projected.get("included_draft_ids")),
            ]
        )
    if provenance:
        fields.extend(
            [
                ("Confirmed transaction count", provenance.get("confirmed_transaction_count")),
                ("Draft count", provenance.get("draft_count")),
            ]
        )
    return object_summary("Account balance", fields)


def account_adjust_summary(data: Any) -> Table:
    transaction = _as_dict(_as_dict(data).get("transaction"))
    if not transaction:
        transaction = _as_dict(data)
    fields = [
        ("Transaction ID", transaction.get("transaction_id")),
        ("Account ID", transaction.get("account_id")),
        ("Currency", transaction.get("currency")),
        ("Amount", transaction.get("amount")),
        ("Purpose", transaction.get("purpose")),
        ("Occurred at", transaction.get("occurred_at")),
    ]
    if isinstance(data, dict) and "idempotent_replay" in data:
        fields.append(("Idempotent replay", data.get("idempotent_replay")))
    return object_summary("Account adjustment", fields)
