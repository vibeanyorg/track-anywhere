from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..commands import CreateAccountCommand, CreateCategoryCommand, RecordTransactionCommand
from ..domain_commands import (
    AddCategoryAliasCommand,
    CreateBookCommand,
    CreateBudgetCommand,
    CreateBudgetTargetCommand,
    MergeCategoryCommand,
    ReverseBookTransactionCommand,
    UpdateCategoryCommand,
)
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter(prefix="/books")


@router.get("", dependencies=protected)
def list_books(token: AuthToken):
    try:
        return {"books": serialize(service.list_books(token))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.list")


@router.post("", dependencies=protected)
def create_book(payload: CreateBookCommand, token: AuthToken, key: IdempotencyKey):
    try:
        book, replay = service.create_book(token, command_payload(payload), idempotency_key=key)
        return {"book": serialize(book), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.create")


@router.get("/{book_id}", dependencies=protected)
def get_book(book_id: str, token: AuthToken):
    try:
        return {"book": serialize(service.get_book(token, book_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.get")


@router.get("/{book_id}/accounts", dependencies=protected)
def list_book_accounts(book_id: str, token: AuthToken):
    try:
        return {"accounts": serialize(service.list_book_accounts(token, book_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.account.list")


@router.post("/{book_id}/accounts", dependencies=protected)
def create_book_account(book_id: str, payload: CreateAccountCommand, token: AuthToken, key: IdempotencyKey):
    try:
        account, replay = service.create_book_account(token, book_id, command_payload(payload), idempotency_key=key)
        return {"account": serialize(account), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.account.create")


@router.get("/{book_id}/transactions", dependencies=protected)
def list_book_transactions(book_id: str, token: AuthToken, limit: int = 20):
    try:
        return {"transactions": serialize(service.list_book_transactions(token, book_id, limit=limit))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.transaction.list")


@router.post("/{book_id}/transactions", dependencies=protected)
def record_book_transaction(book_id: str, payload: RecordTransactionCommand, token: AuthToken, key: IdempotencyKey):
    try:
        transaction, replay = service.record_book_transaction(token, book_id, command_payload(payload), idempotency_key=key)
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.transaction.record")


@router.post("/{book_id}/transactions/{transaction_id}/reverse", dependencies=protected)
def reverse_book_transaction(book_id: str, transaction_id: str, payload: ReverseBookTransactionCommand, token: AuthToken, key: IdempotencyKey):
    try:
        transaction, replay = service.reverse_book_transaction(
            token,
            book_id,
            {"transaction_id": transaction_id, **command_payload(payload)},
            idempotency_key=key,
        )
        return {"transaction": serialize(transaction), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.transaction.reverse")


@router.get("/{book_id}/categories", dependencies=protected)
def list_book_categories(book_id: str, token: AuthToken, kind: str | None = None):
    try:
        return {"categories": serialize(service.list_book_categories(token, book_id, kind=kind))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.category.list")


@router.post("/{book_id}/categories", dependencies=protected)
def create_book_category(book_id: str, payload: CreateCategoryCommand, token: AuthToken, key: IdempotencyKey):
    try:
        category, replay = service.create_book_category(token, book_id, command_payload(payload), idempotency_key=key)
        return {"category": serialize(category), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.category.create")


@router.patch("/{book_id}/categories/{category_id}", dependencies=protected)
def update_book_category(book_id: str, category_id: str, payload: UpdateCategoryCommand, token: AuthToken, key: IdempotencyKey):
    try:
        category, replay = service.update_book_category(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        return {"category": serialize(category), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.category.update")


@router.post("/{book_id}/categories/{category_id}/aliases", dependencies=protected)
def add_book_category_alias(book_id: str, category_id: str, payload: AddCategoryAliasCommand, token: AuthToken, key: IdempotencyKey):
    try:
        alias, replay = service.add_book_category_alias(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        return {"alias": serialize(alias), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.category.alias.add")


@router.post("/{book_id}/categories/{category_id}/merge", dependencies=protected)
def merge_book_category(book_id: str, category_id: str, payload: MergeCategoryCommand, token: AuthToken, key: IdempotencyKey):
    try:
        category, replay = service.merge_book_category(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        return {"category": serialize(category), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.category.merge")


@router.get("/{book_id}/classification-events", dependencies=protected)
def list_book_classification_events(book_id: str, token: AuthToken):
    try:
        return {"events": serialize(service.list_book_classification_events(token, book_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.classification_event.list")


@router.get("/{book_id}/budgets", dependencies=protected)
def list_book_budgets(book_id: str, token: AuthToken):
    try:
        return {"budgets": serialize(service.list_budgets(token, book_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.budget.list")


@router.post("/{book_id}/budgets", dependencies=protected)
def create_book_budget(book_id: str, payload: CreateBudgetCommand, token: AuthToken, key: IdempotencyKey):
    try:
        budget, replay = service.create_budget(token, book_id, command_payload(payload), idempotency_key=key)
        return {"budget": serialize(budget), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.budget.create")


@router.post("/{book_id}/budgets/{budget_id}/targets", dependencies=protected)
def add_book_budget_target(book_id: str, budget_id: str, payload: CreateBudgetTargetCommand, token: AuthToken, key: IdempotencyKey):
    try:
        target, replay = service.add_budget_target(token, book_id, budget_id, command_payload(payload), idempotency_key=key)
        return {"budget_target": serialize(target), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.budget.target.add")


@router.get("/{book_id}/budgets/{budget_id}/targets", dependencies=protected)
def list_book_budget_targets(book_id: str, budget_id: str, token: AuthToken):
    try:
        return {"budget_targets": serialize(service.list_budget_targets(token, book_id, budget_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.budget.target.list")


@router.get("/{book_id}/budgets/{budget_id}/execution", dependencies=protected)
def get_book_budget_execution(book_id: str, budget_id: str, token: AuthToken):
    try:
        return service.budget_execution_report(token, book_id, budget_id)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.budget.execution")


@router.get("/{book_id}/reports/spending", dependencies=protected)
def book_spending_report(book_id: str, token: AuthToken, group_by: str = "category_parent", currency: str | None = None):
    try:
        return service.spending_report(token, book_id, group_by=group_by, currency=currency)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.report.spending")
