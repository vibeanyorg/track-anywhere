from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.table import Table

from .presenter_base import Presenter, _as_dict, _stringify, empty_panel, object_summary


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
