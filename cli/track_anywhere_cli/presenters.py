from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

Presenter = Callable[[Any], Any]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def object_summary(title: str, fields: list[tuple[str, Any]]) -> Table:
    table = Table(title=title, show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in fields:
        table.add_row(label, _stringify(value))
    return table


def empty_panel(title: str, message: str) -> Panel:
    return Panel(message, title=title)


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


def _money_amounts(rows: Any) -> str:
    if not isinstance(rows, list):
        return ""
    parts: list[str] = []
    for item in rows:
        row = _as_dict(item)
        if not row:
            continue
        amount = row.get("amount")
        currency = row.get("currency")
        if amount is None and currency is None:
            continue
        if amount is None:
            parts.append(_stringify(currency))
        elif currency is None:
            parts.append(_stringify(amount))
        else:
            parts.append(f"{amount} {currency}")
    return ", ".join(parts)


def transaction_summary(data: Any, *, title: str) -> Table:
    transaction = _as_dict(_as_dict(data).get("transaction"))
    if not transaction:
        transaction = _as_dict(data)
    amount_rows = transaction.get("postings") or transaction.get("lines")
    amount_row_count = len(amount_rows) if isinstance(amount_rows, list) else 0
    fields = [
        ("Transaction ID", transaction.get("transaction_id")),
        ("Memo", transaction.get("memo")),
        ("Purpose", transaction.get("purpose")),
        ("Occurred at", transaction.get("occurred_at")),
        ("Category", transaction.get("category_id")),
        ("Version", transaction.get("version")),
        ("Reversed by", transaction.get("reversed_by")),
        ("Amount row count", amount_row_count),
        ("Amounts", _money_amounts(amount_rows)),
    ]
    if isinstance(data, dict) and "idempotent_replay" in data:
        fields.append(("Idempotent replay", data.get("idempotent_replay")))
    return object_summary(title, fields)


def transaction_list_summary(data: Any) -> Table | Panel:
    transactions = _as_dict(data).get("transactions", [])
    if not transactions:
        return empty_panel("Transactions", "No transactions found.")
    table = Table(title="Transactions")
    table.add_column("Transaction ID")
    table.add_column("Memo")
    table.add_column("Purpose")
    table.add_column("Occurred at")
    table.add_column("Category")
    table.add_column("Postings", overflow="fold")
    table.add_column("Version", justify="right")
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        table.add_row(
            _stringify(tx.get("transaction_id")),
            _stringify(tx.get("memo")),
            _stringify(tx.get("purpose")),
            _stringify(tx.get("occurred_at")),
            _stringify(tx.get("category_id")),
            _money_amounts(tx.get("postings") or tx.get("lines")),
            _stringify(tx.get("version")),
        )
    return table


def capture_summary(data: Any) -> Table:
    payload = _as_dict(data)
    if "draft" in payload and isinstance(payload.get("draft"), dict):
        draft = payload["draft"]
        return object_summary(
            "Capture confirmed",
            [
                ("Dry run", payload.get("dry_run", False)),
                ("Draft ID", draft.get("draft_id")),
                ("State", draft.get("state")),
                ("Source", draft.get("source")),
                ("Memo", draft.get("memo")),
                ("Confidence", draft.get("confidence")),
                ("Missing fields", _stringify(draft.get("missing_fields"))),
                ("Source account", draft.get("source_account_id")),
                ("Expense account", draft.get("expense_account_id")),
            ],
        )

    request_payload = _as_dict(payload.get("payload"))
    return object_summary(
        "Capture response",
        [
            ("Dry run", payload.get("dry_run")),
            ("Policy decision", payload.get("policy_decision")),
            ("Source account", request_payload.get("source_account_id")),
            ("Expense account", request_payload.get("expense_account_id")),
            ("Amount", request_payload.get("amount")),
            ("Currency", request_payload.get("currency")),
            ("Memo", request_payload.get("memo")),
        ],
    )


def recurring_item_summary(data: Any, *, title: str) -> Table:
    item = _as_dict(_as_dict(data).get("recurring_item"))
    if not item:
        item = _as_dict(data)
    recurrence = _as_dict(item.get("recurrence"))
    recurrence_desc = ""
    if recurrence:
        recurrence_type = recurrence.get("type")
        if recurrence_type == "monthly_day":
            recurrence_desc = f"monthly day {recurrence.get('day')}"
        elif recurrence_type == "yearly_date":
            recurrence_desc = f"yearly {recurrence.get('month')}-{recurrence.get('day')}"
        else:
            recurrence_desc = _stringify(recurrence_type)
    fields = [
        ("Recurring ID", item.get("recurring_id")),
        ("Name", item.get("name")),
        ("Status", item.get("status")),
        ("Kind", item.get("kind")),
        ("Provider", item.get("provider")),
        ("Reference", item.get("reference")),
        ("Anchor date", item.get("anchor_date")),
        ("Recurrence", recurrence_desc),
        ("Amount", item.get("amount")),
        ("Currency", item.get("currency")),
        ("Source account", item.get("source_account_id")),
        ("Category", item.get("category_id")),
        ("Reminder days", item.get("reminder_days")),
        ("Version", item.get("version")),
    ]
    if isinstance(data, dict) and "idempotent_replay" in data:
        fields.append(("Idempotent replay", data.get("idempotent_replay")))
    return object_summary(title, fields)


def investment_event_summary(data: Any) -> Table:
    event = _as_dict(_as_dict(data).get("event"))
    if not event:
        event = _as_dict(data)
    fields = [
        ("Event ID", event.get("event_id")),
        ("Account ID", event.get("account_id")),
        ("Type", event.get("event_type")),
        ("Amount", event.get("amount")),
        ("Currency", event.get("currency")),
        ("Occurred at", event.get("occurred_at")),
        ("Memo", event.get("memo")),
        ("Units", event.get("units")),
        ("NAV", event.get("nav")),
        ("Version", event.get("version")),
    ]
    if isinstance(data, dict) and "idempotent_replay" in data:
        fields.append(("Idempotent replay", data.get("idempotent_replay")))
    return object_summary("Investment event", fields)


def investment_performance_summary(data: Any) -> Table:
    payload = _as_dict(data)
    fields = [
        ("Account ID", payload.get("account_id")),
        ("Currency", payload.get("currency")),
        ("As-of", payload.get("as_of")),
        ("Current value", payload.get("current_value")),
        ("Contributions", payload.get("contributions")),
        ("Withdrawals", payload.get("withdrawals")),
        ("Income", payload.get("income")),
        ("Net contributed", payload.get("net_contributed")),
        ("Total return", payload.get("total_return")),
        ("First invested at", payload.get("first_invested_at")),
        ("Holding days", payload.get("holding_days")),
        ("Event count", payload.get("event_count")),
        ("Money weighted annualized return", payload.get("money_weighted_annualized_return")),
        ("Money weighted annualized return (%)", payload.get("money_weighted_annualized_return_percent")),
        ("Method", payload.get("method")),
    ]
    return object_summary("Investment performance", fields)


def success_panel(title: str) -> Presenter:
    def present(data: Any) -> Panel:
        return Panel(str(data), title=title)

    return present


def generic_payload_panel(title: str) -> Presenter:
    def present(data: Any) -> Panel:
        return Panel(str(data), title=title)

    return present


def recurring_reminders_table(data: Any) -> Table:
    reminders = data.get("reminders", []) if isinstance(data, dict) else []
    table = Table(title="Recurring reminders")
    table.add_column("Name")
    table.add_column("Provider")
    table.add_column("Renewal")
    table.add_column("Reminder")
    table.add_column("Lead", justify="right")
    table.add_column("Amount", justify="right")
    for item in reminders:
        if not isinstance(item, dict):
            continue
        amount = ""
        if item.get("amount") is not None:
            amount = f"{item['amount']} {item.get('currency') or ''}".strip()
        table.add_row(
            str(item.get("name") or ""),
            str(item.get("provider") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("reminder_date") or ""),
            str(item.get("lead_days") or ""),
            amount,
        )
    return table


def recurring_items_table(data: Any) -> Table:
    items = data.get("recurring_items", []) if isinstance(data, dict) else []
    table = Table(title="Recurring items")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Kind")
    table.add_column("Provider")
    table.add_column("Amount", justify="right")
    for item in items:
        if not isinstance(item, dict):
            continue
        amount = ""
        if item.get("amount") is not None:
            amount = f"{item['amount']} {item.get('currency') or ''}".strip()
        table.add_row(
            str(item.get("name") or ""),
            str(item.get("status") or ""),
            str(item.get("kind") or ""),
            str(item.get("provider") or ""),
            amount,
        )
    return table


def recurring_drafts_table(data: Any) -> Table:
    result = _as_dict(_as_dict(data).get("result", data))
    table = Table(title=f"Recurring draft run {result.get('as_of', '')}".strip())
    table.add_column("Action")
    table.add_column("Name")
    table.add_column("Renewal")
    table.add_column("Draft")
    for item in result.get("created", []):
        if not isinstance(item, dict):
            continue
        table.add_row(
            "created",
            str(item.get("recurring_id") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("draft_id") or ""),
        )
    for item in result.get("skipped", []):
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("reason") or "skipped"),
            str(item.get("name") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("last_draft_id") or ""),
        )
    return table


def _generic_title(command_path: str) -> str:
    return f"{command_path} response"


PRESENTERS: dict[str, Presenter] = {
    "account.list": account_list,
    "account.find": account_list,
    "capture": capture_summary,
    "draft.confirm": lambda data: transaction_summary(data, title="Draft confirm"),
    "tx.record": lambda data: transaction_summary(data, title="Transaction recorded"),
    "recurring.reminders": recurring_reminders_table,
    "recurring.list": recurring_items_table,
    "recurring.draft_due": recurring_drafts_table,
    "tx.list": transaction_list_summary,
    "tx.show": lambda data: transaction_summary(data, title="Transaction"),
    "tx.reverse": lambda data: transaction_summary(data, title="Reversed transaction"),
    "balance.adjust": lambda data: transaction_summary(data, title="Account adjustment"),
    "balance": account_balance_summary,
    "expense.record": lambda data: transaction_summary(data, title="Expense recorded"),
    "income.record": lambda data: transaction_summary(data, title="Income recorded"),
    "recurring.create": lambda data: recurring_item_summary(data, title="Recurring item created"),
    "recurring.show": lambda data: recurring_item_summary(data, title="Recurring item"),
    "recurring.update": lambda data: recurring_item_summary(data, title="Recurring item updated"),
    "summary.accounts": summary_accounts,
    "summary.categories": summary_categories,
    "user.create": user_summary,
    "user.list": user_list,
    "category.create": category_summary,
    "category.list": category_list,
    "category.find": category_list,
    "category.show": category_summary,
    "credit_card.list": credit_card_list,
    "credit_card.show": credit_card_summary,
    "credit_card.update": credit_card_summary,
    "account.create": account_entity_summary,
    "account.show": account_entity_summary,
    "account.update": account_entity_summary,
    "account.balance": account_balance_summary,
    "account.adjust": account_adjust_summary,
    "investment.event": investment_event_summary,
    "investment.performance": investment_performance_summary,
    "auth.dev_token": generic_payload_panel(_generic_title("auth.dev_token")),
    "auth.status": generic_payload_panel(_generic_title("auth.status")),
    "data.backup": generic_payload_panel(_generic_title("data.backup")),
    "auth.login": generic_payload_panel(_generic_title("auth.login")),
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
