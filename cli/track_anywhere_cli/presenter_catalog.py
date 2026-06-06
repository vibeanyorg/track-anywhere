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
    table.add_column("Balance semantics")
    table.add_column("Natural balance", justify="right")
    for account in accounts:
        if not isinstance(account, dict):
            continue
        table.add_row(
            _stringify(account.get("account_id")),
            _stringify(account.get("name")),
            _stringify(account.get("type")),
            _stringify(account.get("currency")),
            _stringify(account.get("balance_semantics")),
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
    table.add_column("Funds", justify="right")
    table.add_column("Liabilities owed", justify="right")
    table.add_column("Liability overpaid", justify="right")
    table.add_column("Net", justify="right")
    for row in groups:
        if not isinstance(row, dict):
            continue
        table.add_row(
            _stringify(row.get("group", row.get("key"))),
            _stringify(row.get("currency")),
            _stringify(row.get("asset_amount", row.get("amount", ""))),
            _stringify(row.get("fund_amount", "")),
            _stringify(row.get("liability_outstanding_amount", row.get("liability_amount", ""))),
            _stringify(row.get("liability_overpayment_amount", "")),
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
    payload = _as_dict(data)
    categories = payload.get("categories", [])
    if not categories and payload.get("category"):
        categories = [payload.get("category")]
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
    table.add_column("Outstanding")
    table.add_column("Overpayment")
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
            _stringify(card.get("outstanding_balance", card.get("current_balance"))),
            _stringify(card.get("overpayment_balance", "")),
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
            ("Natural liability balance", card.get("natural_balance", card.get("current_balance"))),
            ("Natural balance semantics", card.get("natural_balance_semantics")),
            ("Balance semantics", card.get("balance_semantics")),
            ("Outstanding balance", card.get("outstanding_balance")),
            ("Outstanding balance semantics", card.get("outstanding_balance_semantics")),
            ("Overpayment balance", card.get("overpayment_balance")),
            ("Overpayment balance semantics", card.get("overpayment_balance_semantics")),
            ("Credit limit", card.get("credit_limit")),
            ("Available credit", card.get("available_credit")),
            ("Derived available credit", card.get("derived_available_credit")),
            ("Derived available credit semantics", card.get("derived_available_credit_semantics")),
            ("Utilization", card.get("utilization_rate")),
        ],
    )


def financial_account_list(data: Any) -> Table | Panel:
    accounts = _as_dict(data).get("financial_accounts", [])
    if not accounts:
        return empty_panel("Financial accounts", "No financial accounts found.")
    table = Table(title="Financial accounts")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Currency")
    table.add_column("Institution")
    table.add_column("Subtype")
    table.add_column("Official balance", justify="right")
    for account in accounts:
        if not isinstance(account, dict):
            continue
        balance = _as_dict(account.get("balance"))
        official = _as_dict(balance.get("official_balance"))
        table.add_row(
            _stringify(account.get("account_id")),
            _stringify(account.get("name")),
            _stringify(account.get("type")),
            _stringify(account.get("currency")),
            _stringify(account.get("institution")),
            _stringify(account.get("subtype")),
            _stringify(official.get("amount")),
        )
    return table


def financial_account_summary(data: Any) -> Table:
    account = _as_dict(_as_dict(data).get("financial_account"))
    balance = _as_dict(account.get("balance"))
    official = _as_dict(balance.get("official_balance"))
    fields = [
        ("Account ID", account.get("account_id")),
        ("Ledger account ID", account.get("ledger_account_id")),
        ("Name", account.get("name")),
        ("Type", account.get("type")),
        ("Ledger account type", account.get("ledger_account_type")),
        ("Currency", account.get("currency")),
        ("Institution type", account.get("institution_type")),
        ("Subtype", account.get("subtype")),
        ("Institution", account.get("institution")),
        ("Status", account.get("status")),
        ("Balance semantics", account.get("balance_semantics")),
    ]
    if official:
        fields.extend(
            [
                ("Official balance", official.get("amount")),
                ("Official amount semantics", official.get("amount_semantics")),
                ("Balance source", official.get("source")),
                ("As-of ledger version", official.get("as_of_ledger_version")),
            ]
        )
    return object_summary("Financial account", fields)


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
    liability = _as_dict(payload.get("liability_balance"))
    projected_liability = _as_dict(payload.get("projected_liability_balance"))
    provenance = _as_dict(payload.get("provenance"))
    fields = [
        ("Account ID", payload.get("account_id")),
        ("Account type", payload.get("account_type")),
        ("Currency", payload.get("currency")),
        ("Balance semantics", payload.get("balance_semantics")),
        ("Official balance", official.get("amount")),
        ("Official amount semantics", official.get("amount_semantics")),
        ("Balance source", official.get("source")),
        ("As-of ledger version", official.get("as_of_ledger_version")),
    ]
    if liability:
        fields.extend(
            [
                ("Liability semantics", liability.get("semantics")),
                ("Outstanding amount", liability.get("outstanding_amount")),
                ("Outstanding amount semantics", liability.get("outstanding_amount_semantics")),
                ("Overpayment amount", liability.get("overpayment_amount")),
                ("Overpayment amount semantics", liability.get("overpayment_amount_semantics")),
            ]
        )
    if projected:
        fields.extend(
            [
                ("Projected balance", projected.get("amount")),
                ("Projected amount semantics", projected.get("amount_semantics")),
                ("Pending impact", projected.get("pending_impact")),
                ("Pending impact semantics", projected.get("pending_impact_semantics")),
                ("Projection version", projected.get("projection_version")),
                ("Included draft ids", projected.get("included_draft_ids")),
            ]
        )
    if projected_liability:
        fields.extend(
            [
                ("Projected outstanding amount", projected_liability.get("outstanding_amount")),
                (
                    "Projected outstanding amount semantics",
                    projected_liability.get("outstanding_amount_semantics"),
                ),
                ("Projected overpayment amount", projected_liability.get("overpayment_amount")),
                (
                    "Projected overpayment amount semantics",
                    projected_liability.get("overpayment_amount_semantics"),
                ),
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


def posting_semantics_audit_summary(data: Any) -> Table:
    payload = _as_dict(data)
    counts = _as_dict(payload.get("counts"))
    fields = [
        ("Cutover ready", payload.get("cutover_ready")),
        ("Auto rewrite ready", payload.get("auto_rewrite_ready")),
        ("Issue count", _count(payload.get("issues"))),
        ("Manual review blockers", _count(payload.get("manual_review_blockers"))),
        ("Auto rewrite candidates", _count(payload.get("auto_rewrite_candidates"))),
        ("Manual review recommendations", _count(payload.get("manual_review_recommendations"))),
    ]
    for key, value in counts.items():
        fields.append((key.replace("_", " ").title(), value))
    return object_summary("Posting semantics audit", fields)


def posting_semantics_cutover_plan_summary(data: Any) -> Table:
    payload = _as_dict(data)
    fields = [
        ("Cutover ready", payload.get("cutover_ready")),
        ("Auto rewrite ready", payload.get("auto_rewrite_ready")),
        ("Blocking issue count", _count(payload.get("blocking_issues", payload.get("manual_review_blockers")))),
        ("Auto rewrite candidate count", _count(payload.get("auto_rewrite_candidates"))),
        ("Manual review recommendation count", _count(payload.get("manual_review_recommendations"))),
    ]
    counts = _as_dict(payload.get("counts"))
    for key, value in counts.items():
        fields.append((key.replace("_", " ").title(), value))
    return object_summary("Posting semantics cutover plan", fields)


def posting_semantics_write_summary(data: Any) -> Table:
    payload = _as_dict(data)
    fields = []
    for key in (
        "status",
        "book_id",
        "cutover_ready",
        "confirmed_postings_rewritten",
        "draft_postings_rewritten",
        "confirmed_liability_postings_rewritten",
        "draft_liability_postings_rewritten",
        "rewritten_postings",
        "rewritten_draft_postings",
        "resolved_postings",
        "resolved_draft_postings",
        "idempotent_replay",
    ):
        if key in payload:
            fields.append((key.replace("_", " ").title(), payload.get(key)))
    for key, value in _as_dict(payload.get("result")).items():
        fields.append((key.replace("_", " ").title(), value))
    posting_semantics = _as_dict(payload.get("posting_semantics"))
    if posting_semantics:
        fields.extend(
            [
                ("Canonical model", posting_semantics.get("canonical_model")),
                ("Amount rule", posting_semantics.get("debit_credit_amount_rule")),
                ("Side rule", posting_semantics.get("debit_credit_side_rule")),
                ("Legacy signed scope", posting_semantics.get("legacy_signed_scope")),
            ]
        )
    if not fields:
        fields = [("Response", payload)]
    return object_summary("Posting semantics write", fields)


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


def _count(value: Any) -> int | str:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    if value is None:
        return ""
    return str(value)
