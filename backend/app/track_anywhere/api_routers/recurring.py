from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_service_ports import RecurringService
from ..api_serialization import serialize
from ..commands import CreateRecurringItemCommand, GenerateRecurringDraftsCommand, UpdateRecurringItemCommand
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.post("/recurring/items", dependencies=protected)
def create_recurring_item(
    payload: CreateRecurringItemCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: RecurringService,
):
    try:
        item, replay = service.create_recurring_item(token, command_payload(payload), idempotency_key=key)
        return {"recurring_item": serialize(item), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.item.create", recorder=service)


@router.get("/recurring/items", dependencies=protected)
def list_recurring_items(
    token: AuthToken,
    service: RecurringService,
    status: str | None = None,
    kind: str | None = None,
):
    try:
        return {"recurring_items": serialize(service.list_recurring_items(token, status=status, kind=kind))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.item.list", recorder=service)


@router.get("/recurring/items/{recurring_id}", dependencies=protected)
def get_recurring_item(recurring_id: str, token: AuthToken, service: RecurringService):
    try:
        return {"recurring_item": serialize(service.get_recurring_item(token, recurring_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.item.get", recorder=service)


@router.patch("/recurring/items/{recurring_id}", dependencies=protected)
def update_recurring_item(
    recurring_id: str,
    payload: UpdateRecurringItemCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: RecurringService,
):
    try:
        item, replay = service.update_recurring_item(
            token,
            recurring_id,
            command_payload(payload),
            idempotency_key=key,
        )
        return {"recurring_item": serialize(item), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.item.update", recorder=service)


@router.get("/recurring/reminders", dependencies=protected)
def recurring_reminders(token: AuthToken, service: RecurringService, as_of: str | None = None, window_days: int = 0):
    try:
        return service.check_recurring_reminders(token, as_of=as_of, window_days=window_days)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.reminders.check", recorder=service)


@router.post("/recurring/drafts", dependencies=protected)
def generate_recurring_drafts(
    payload: GenerateRecurringDraftsCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: RecurringService,
):
    try:
        result, replay = service.generate_recurring_drafts(token, command_payload(payload), idempotency_key=key)
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "recurring.draft.generate", recorder=service)


@router.post("/books/{book_id}/recurring/items", dependencies=protected)
def create_book_recurring_item(
    book_id: str,
    payload: CreateRecurringItemCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: RecurringService,
):
    try:
        item, replay = service.create_recurring_item(
            token,
            {**command_payload(payload), "book_id": book_id},
            idempotency_key=key,
        )
        return {"recurring_item": serialize(item), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.recurring.item.create", recorder=service)


@router.get("/books/{book_id}/recurring/items", dependencies=protected)
def list_book_recurring_items(
    book_id: str,
    token: AuthToken,
    service: RecurringService,
    status: str | None = None,
    kind: str | None = None,
):
    try:
        return {
            "recurring_items": serialize(
                service.list_recurring_items(token, status=status, kind=kind, book_id=book_id)
            )
        }
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.recurring.item.list", recorder=service)


@router.get("/books/{book_id}/recurring/reminders", dependencies=protected)
def book_recurring_reminders(
    book_id: str,
    token: AuthToken,
    service: RecurringService,
    as_of: str | None = None,
    window_days: int = 0,
):
    try:
        return service.check_recurring_reminders(token, as_of=as_of, window_days=window_days, book_id=book_id)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.recurring.reminders.check", recorder=service)


@router.post("/books/{book_id}/recurring/drafts", dependencies=protected)
def generate_book_recurring_drafts(
    book_id: str,
    payload: GenerateRecurringDraftsCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: RecurringService,
):
    try:
        result, replay = service.generate_recurring_drafts(
            token,
            command_payload(payload),
            idempotency_key=key,
            book_id=book_id,
        )
        return {"result": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "book.recurring.draft.generate", recorder=service)
