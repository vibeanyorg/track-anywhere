from __future__ import annotations

from .presenter_base import Presenter
from .presenter_catalog import (
    account_adjust_summary,
    account_balance_summary,
    account_entity_summary,
    account_list,
    category_list,
    category_summary,
    credit_card_list,
    credit_card_summary,
    summary_accounts,
    summary_categories,
    user_list,
    user_summary,
)
from .presenter_operations import (
    capture_summary,
    generic_payload_panel,
    investment_event_summary,
    investment_performance_summary,
    recurring_drafts_table,
    recurring_item_summary,
    recurring_items_table,
    recurring_reminders_table,
    transaction_list_summary,
    transaction_summary,
)


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
    "capabilities": generic_payload_panel(_generic_title("capabilities")),
    "data.backup": generic_payload_panel(_generic_title("data.backup")),
    "auth.login": generic_payload_panel(_generic_title("auth.login")),
    "schema": generic_payload_panel(_generic_title("schema")),
    "version": generic_payload_panel(_generic_title("version")),
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
