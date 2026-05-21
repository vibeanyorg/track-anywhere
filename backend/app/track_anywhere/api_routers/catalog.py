from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..commands import (
    CreateAccountCommand,
    CreateCategoryCommand,
    CreateUserCommand,
    UpdateAccountMetadataCommand,
    UpdateCreditCardProfileCommand,
)
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.get("/accounts", dependencies=protected)
def list_accounts(
    token: AuthToken,
    name: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution_type: str | None = None,
    subtype: str | None = None,
    institution: str | None = None,
):
    try:
        return {
            "accounts": serialize(
                service.list_accounts(
                    token,
                    name=name,
                    type=type,
                    currency=currency,
                    institution_type=institution_type,
                    subtype=subtype,
                    institution=institution,
                )
            )
        }
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "account.list")


@router.post("/accounts", dependencies=protected)
def create_account(payload: CreateAccountCommand, token: AuthToken, key: IdempotencyKey):
    try:
        account, replay = service.create_account(token, command_payload(payload), idempotency_key=key)
        return {"account": serialize(account), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "account.create")


@router.get("/accounts/{account_id}", dependencies=protected)
def get_account(account_id: str, token: AuthToken):
    try:
        return {"account": serialize(service.get_account(token, account_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "account.get")


@router.patch("/accounts/{account_id}", dependencies=protected)
def update_account_metadata(
    account_id: str,
    payload: UpdateAccountMetadataCommand,
    token: AuthToken,
    key: IdempotencyKey,
):
    try:
        account, replay = service.update_account_metadata(token, account_id, command_payload(payload), idempotency_key=key)
        return {"account": serialize(account), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "account.metadata.update")


@router.get("/summary/accounts", dependencies=protected)
def account_summary(
    token: AuthToken,
    group_by: str = "subtype",
    currency: str | None = None,
    institution_type: str | None = None,
    include_system: bool = False,
):
    try:
        return service.account_summary(
            token,
            group_by=group_by,
            currency=currency,
            institution_type=institution_type,
            include_system=include_system,
        )
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "summary.accounts")


@router.get("/summary/categories", dependencies=protected)
def category_summary(token: AuthToken, kind: str | None = None, currency: str | None = None):
    try:
        return service.category_summary(token, kind=kind, currency=currency)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "summary.categories")


@router.get("/users", dependencies=protected)
def list_users(token: AuthToken):
    try:
        return {"users": serialize(service.list_users(token))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "user.list")


@router.post("/users", dependencies=protected)
def create_user(payload: CreateUserCommand, token: AuthToken, key: IdempotencyKey):
    try:
        user, replay = service.create_user(token, command_payload(payload), idempotency_key=key)
        return {"user": serialize(user), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "user.create")


@router.get("/categories", dependencies=protected)
def list_categories(
    token: AuthToken,
    kind: str | None = None,
    name: str | None = None,
    parent_id: str | None = None,
):
    try:
        return {"categories": serialize(service.list_categories(token, kind=kind, name=name, parent_id=parent_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "category.list")


@router.post("/categories", dependencies=protected)
def create_category(payload: CreateCategoryCommand, token: AuthToken, key: IdempotencyKey):
    try:
        category, replay = service.create_category(token, command_payload(payload), idempotency_key=key)
        return {"category": serialize(category), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "category.create")


@router.get("/categories/{category_id}", dependencies=protected)
def get_category(category_id: str, token: AuthToken):
    try:
        return {"category": serialize(service.get_category(token, category_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "category.get")


@router.get("/credit-cards", dependencies=protected)
def list_credit_cards(token: AuthToken):
    try:
        return {"credit_cards": serialize(service.list_credit_cards(token))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credit_card.list")


@router.get("/credit-cards/{account_id}", dependencies=protected)
def get_credit_card(account_id: str, token: AuthToken):
    try:
        return {"credit_card": serialize(service.get_credit_card(token, account_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credit_card.get")


@router.patch("/credit-cards/{account_id}", dependencies=protected)
def update_credit_card_profile(
    account_id: str,
    payload: UpdateCreditCardProfileCommand,
    token: AuthToken,
    key: IdempotencyKey,
):
    try:
        credit_card, replay = service.update_credit_card_profile(token, account_id, command_payload(payload), idempotency_key=key)
        return {"credit_card": serialize(credit_card), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credit_card.profile.update")
