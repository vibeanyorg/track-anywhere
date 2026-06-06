from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..api_dependencies import AuthToken
from ..api_errors import raise_command_error
from ..api_ports.catalog import CatalogService
from ..api_serialization import serialize
from ..balance_semantics import balance_semantics_for_account_type
from .common import COMMAND_ERRORS, protected


router = APIRouter()


class LedgerAccountResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    name: str
    type: str
    currency: str
    institution_type: str | None = None
    subtype: str | None = None
    institution: str | None = None
    book_id: str
    version: int
    balance_semantics: str


class LedgerAccountsResponse(BaseModel):
    ledger_accounts: list[LedgerAccountResource]


class LedgerAccountResponse(BaseModel):
    ledger_account: LedgerAccountResource


class OfficialBalanceResource(BaseModel):
    amount: str
    amount_semantics: str
    source: str
    as_of_ledger_version: int


class BalanceProvenanceResource(BaseModel):
    confirmed_transaction_count: int
    draft_count: int


class LiabilityBalanceResource(BaseModel):
    semantics: str
    outstanding_amount: str
    overpayment_amount: str
    outstanding_amount_semantics: str
    overpayment_amount_semantics: str


class AccountBalanceResource(BaseModel):
    account_id: str
    account_type: str
    currency: str
    balance_semantics: str
    official_balance: OfficialBalanceResource
    default_view: str
    provenance: BalanceProvenanceResource
    liability_balance: LiabilityBalanceResource | None = None


class FinancialAccountResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    ledger_account_id: str
    name: str
    type: str
    ledger_account_type: str
    currency: str
    institution_type: str | None = None
    subtype: str | None = None
    institution: str | None = None
    book_id: str
    status: str
    balance_semantics: str
    balance: AccountBalanceResource | None = None


class FinancialAccountsResponse(BaseModel):
    financial_accounts: list[FinancialAccountResource]


class FinancialAccountResponse(BaseModel):
    financial_account: FinancialAccountResource


@router.get("/ledger-accounts", response_model=LedgerAccountsResponse, dependencies=protected)
def list_ledger_accounts(
    token: AuthToken,
    service: CatalogService,
    name: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution_type: str | None = None,
    subtype: str | None = None,
    institution: str | None = None,
):
    try:
        return {
            "ledger_accounts": [
                _serialize_ledger_account(account)
                for account in service.list_ledger_accounts(
                    token,
                    name=name,
                    type=type,
                    currency=currency,
                    institution_type=institution_type,
                    subtype=subtype,
                    institution=institution,
                )
            ]
        }
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger_account.list", recorder=service)


@router.get("/ledger-accounts/{account_id}", response_model=LedgerAccountResponse, dependencies=protected)
def get_ledger_account(account_id: str, token: AuthToken, service: CatalogService):
    try:
        return {"ledger_account": _serialize_ledger_account(service.get_ledger_account(token, account_id))}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "ledger_account.get", recorder=service)


@router.get(
    "/financial-accounts",
    response_model=FinancialAccountsResponse,
    response_model_exclude_none=True,
    dependencies=protected,
)
def list_financial_accounts(
    token: AuthToken,
    service: CatalogService,
    q: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution_type: str | None = None,
    subtype: str | None = None,
    institution: str | None = None,
    status: str | None = None,
    include: str | None = None,
):
    try:
        return {
            "financial_accounts": service.list_financial_accounts(
                token,
                q=q,
                type=type,
                currency=currency,
                institution_type=institution_type,
                subtype=subtype,
                institution=institution,
                status=status,
                include_balance=_include_balance(include),
            )
        }
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "financial_account.list", recorder=service)


@router.get(
    "/financial-accounts/{account_id}",
    response_model=FinancialAccountResponse,
    response_model_exclude_none=True,
    dependencies=protected,
)
def get_financial_account(
    account_id: str,
    token: AuthToken,
    service: CatalogService,
    include: str | None = None,
):
    try:
        return {
            "financial_account": service.get_financial_account(
                token,
                account_id,
                include_balance=_include_balance(include),
            )
        }
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "financial_account.get", recorder=service)


@router.get(
    "/financial-accounts/{account_id}/balance",
    response_model=AccountBalanceResource,
    response_model_exclude_none=True,
    dependencies=protected,
)
def get_financial_account_balance(account_id: str, token: AuthToken, service: CatalogService):
    try:
        return service.financial_account_balance(token, account_id)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "financial_account.balance.get", recorder=service)


def _serialize_ledger_account(account) -> dict[str, object]:
    payload = serialize(account)
    payload["balance_semantics"] = balance_semantics_for_account_type(account.type)
    return payload


def _include_balance(include: str | None) -> bool:
    if include is None:
        return False
    requested = {part.strip() for part in include.split(",") if part.strip()}
    unsupported = requested - {"balance"}
    if unsupported:
        raise HTTPException(status_code=422, detail="include supports only balance")
    return "balance" in requested
