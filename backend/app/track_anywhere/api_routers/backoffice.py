from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..api_dependencies import AuthToken
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..errors import TrackAnywhereError
from ..service_auth import ROLE_SCOPES
from .common import protected


router = APIRouter(prefix="/backoffice", tags=["backoffice"])


@router.get("/roles", dependencies=protected)
def list_roles(token: AuthToken):
    try:
        _require_backoffice(token)
        return [
            {"role": role, "scopes": sorted(scopes)}
            for role, scopes in sorted(ROLE_SCOPES.items())
        ]
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.roles.list")


@router.get("/books", dependencies=protected)
def list_books(
    token: AuthToken,
    kind: str | None = None,
    base_currency: str | None = None,
    status: str | None = None,
    search: str | None = None,
    ordering: str = "book_id",
):
    try:
        _require_backoffice(token)
        items = serialize(service.books.list(status=None))
        return _filter_rows(
            items,
            exact={"kind": kind, "base_currency": base_currency, "status": status},
            search=search,
            search_fields=("book_id", "name"),
            ordering=ordering,
            ordering_fields=("book_id", "name", "kind", "base_currency", "status"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.book.list")


@router.get("/book-members", dependencies=protected)
def list_book_members(
    token: AuthToken,
    book_id: str | None = None,
    user_id: str | None = None,
    role: str | None = None,
    status: str | None = None,
    search: str | None = None,
    ordering: str = "book_id",
):
    try:
        _require_backoffice(token)
        items = serialize(list(service.books.members.values()))
        return _filter_rows(
            items,
            exact={"book_id": book_id, "user_id": user_id, "role": role, "status": status},
            search=search,
            search_fields=("book_id", "user_id", "role"),
            ordering=ordering,
            ordering_fields=("book_id", "user_id", "role", "status"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.book_member.list")


@router.get("/accounts", dependencies=protected)
def list_accounts(
    token: AuthToken,
    book_id: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution_type: str | None = None,
    subtype: str | None = None,
    search: str | None = None,
    ordering: str = "account_id",
):
    try:
        _require_backoffice(token)
        items = serialize(list(service.ledger.accounts.values()))
        return _filter_rows(
            items,
            exact={
                "book_id": book_id,
                "type": type,
                "currency": currency,
                "institution_type": institution_type,
                "subtype": subtype,
            },
            search=search,
            search_fields=("account_id", "name", "institution"),
            ordering=ordering,
            ordering_fields=("account_id", "name", "type", "currency", "book_id"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.account.list")


@router.get("/ledger-users", dependencies=protected)
def list_ledger_users(token: AuthToken, search: str | None = None, ordering: str = "username"):
    try:
        _require_backoffice(token)
        items = serialize(list(service.users.users.values()))
        return _filter_rows(
            items,
            exact={},
            search=search,
            search_fields=("user_id", "username", "display_name"),
            ordering=ordering,
            ordering_fields=("user_id", "username", "display_name"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.user.list")


@router.get("/auth-identities", dependencies=protected)
def list_auth_identities(
    token: AuthToken,
    provider: str | None = None,
    status: str | None = None,
    email_verified: bool | None = None,
    search: str | None = None,
    ordering: str = "provider",
):
    try:
        _require_backoffice(token)
        items = serialize(list(service.auth_identities.identities.values()))
        return _filter_rows(
            items,
            exact={"provider": provider, "status": status, "email_verified": email_verified},
            search=search,
            search_fields=("identity_id", "provider", "subject", "email", "user_id"),
            ordering=ordering,
            ordering_fields=("identity_id", "provider", "subject", "email", "user_id", "status"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.auth_identity.list")


@router.get("/categories", dependencies=protected)
def list_categories(
    token: AuthToken,
    book_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    search: str | None = None,
    ordering: str = "path_cache",
):
    try:
        _require_backoffice(token)
        items = serialize(list(service.categories.categories.values()))
        return _filter_rows(
            items,
            exact={"book_id": book_id, "kind": kind, "status": status},
            search=search,
            search_fields=("category_id", "name", "path_cache", "primary", "secondary"),
            ordering=ordering,
            ordering_fields=("category_id", "book_id", "kind", "path_cache", "status"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.category.list")


@router.get("/transactions", dependencies=protected)
def list_transactions(
    token: AuthToken,
    book_id: str | None = None,
    category_id: str | None = None,
    reversed_by: str | None = None,
    search: str | None = None,
    ordering: str = "-occurred_at",
):
    try:
        _require_backoffice(token)
        book_ids = [book_id] if book_id is not None else [book.book_id for book in service.books.list(status=None)]
        transactions = [
            transaction
            for current_book_id in book_ids
            for transaction in service.storage.list_all_confirmed_transactions(book_id=current_book_id)
        ]
        if category_id is not None:
            transactions = [
                transaction
                for transaction in transactions
                if any(line.category_id == category_id for line in transaction.lines)
            ]
        items = serialize(transactions)
        return _filter_rows(
            items,
            exact={"book_id": book_id, "reversed_by": reversed_by},
            search=search,
            search_fields=("transaction_id", "purpose", "memo"),
            ordering=ordering,
            ordering_fields=("transaction_id", "book_id", "occurred_at", "purpose"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.transaction.list")


@router.get("/recurring-items", dependencies=protected)
def list_recurring_items(
    token: AuthToken,
    book_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    currency: str | None = None,
    search: str | None = None,
    ordering: str = "name",
):
    try:
        _require_backoffice(token)
        items = serialize(list(service.recurring.items.values()))
        return _filter_rows(
            items,
            exact={"book_id": book_id, "kind": kind, "status": status, "currency": currency},
            search=search,
            search_fields=("recurring_id", "name", "provider", "reference"),
            ordering=ordering,
            ordering_fields=("recurring_id", "book_id", "name", "kind", "status", "currency"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.recurring_item.list")


@router.get("/audit-events", dependencies=protected)
def list_audit_events(
    token: AuthToken,
    operation: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    entity_ref: str | None = None,
    search: str | None = None,
    ordering: str = "-created_at",
):
    try:
        _require_backoffice(token)
        items = serialize(service.audit.events)
        return _filter_rows(
            items,
            exact={
                "operation": operation,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "entity_ref": entity_ref,
            },
            search=search,
            search_fields=("event_id", "operation", "actor_id", "entity_ref"),
            ordering=ordering,
            ordering_fields=("event_id", "operation", "actor_id", "actor_type", "entity_ref", "created_at"),
        )
    except TrackAnywhereError as exc:
        raise_command_error(exc, "backoffice.audit_event.list")


def _require_backoffice(token: AuthToken) -> None:
    service.actor_from_token(token, "user:write")


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    exact: dict[str, Any],
    search: str | None,
    search_fields: tuple[str, ...],
    ordering: str,
    ordering_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    filtered = rows
    for field, expected in exact.items():
        if expected is not None:
            filtered = [row for row in filtered if row.get(field) == expected]
    if search:
        needle = search.lower()
        filtered = [
            row
            for row in filtered
            if any(needle in str(row.get(field) or "").lower() for field in search_fields)
        ]
    reverse = ordering.startswith("-")
    order_field = ordering[1:] if reverse else ordering
    if order_field not in ordering_fields:
        order_field = ordering_fields[0]
        reverse = False
    return sorted(filtered, key=lambda row: (str(row.get(order_field) or ""), str(row)), reverse=reverse)
