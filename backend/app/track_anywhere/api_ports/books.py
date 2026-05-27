from __future__ import annotations

from typing import Annotated, Any, Protocol

from .base import AuditRecorder, ServiceDependency


class BookRouteService(AuditRecorder, Protocol):
    def list_books(self, token): ...
    def create_book(self, token, payload: dict[str, Any], *, idempotency_key: str): ...
    def get_book(self, token, book_id: str): ...
    def list_book_accounts(self, token, book_id: str): ...
    def create_book_account(self, token, book_id: str, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_book_transactions(self, token, book_id: str, *, limit: int = 20): ...
    def record_book_transaction(self, token, book_id: str, payload: dict[str, Any], *, idempotency_key: str): ...
    def reverse_book_transaction(self, token, book_id: str, payload: dict[str, Any], *, idempotency_key: str): ...
    def list_book_categories(self, token, book_id: str, **filters): ...
    def create_book_category(self, token, book_id: str, payload: dict[str, Any], *, idempotency_key: str): ...
    def update_book_category(
        self,
        token,
        book_id: str,
        category_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...
    def add_book_category_alias(
        self,
        token,
        book_id: str,
        category_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...
    def merge_book_category(
        self,
        token,
        book_id: str,
        category_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...
    def list_book_classification_events(self, token, book_id: str): ...
    def list_budgets(self, token, book_id: str): ...
    def create_budget(self, token, book_id: str, payload: dict[str, Any], *, idempotency_key: str): ...
    def add_budget_target(
        self,
        token,
        book_id: str,
        budget_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ): ...
    def list_budget_targets(self, token, book_id: str, budget_id: str): ...
    def budget_execution_report(self, token, book_id: str, budget_id: str): ...
    def spending_report(
        self,
        token,
        book_id: str,
        *,
        group_by: str = "category_parent",
        currency: str | None = None,
    ): ...


BookService = Annotated[BookRouteService, ServiceDependency]

