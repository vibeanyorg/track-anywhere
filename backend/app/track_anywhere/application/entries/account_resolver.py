from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ...domain.journal import AccountSystemRole, AccountType
from .contracts import AccountRef, ClarificationChoice
from .errors import EntryErrorCode, EntryGatewayError


class AccountUse(StrEnum):
    EXPENSE_SOURCE = "expense_source"
    INCOME_DESTINATION = "income_destination"
    TRANSFER_SOURCE = "transfer_source"
    TRANSFER_DESTINATION = "transfer_destination"
    CARD_PAYMENT_FUNDING = "card_payment_funding"
    CARD_PAYMENT_CARD = "card_payment_card"
    ADJUSTED = "adjusted"


@dataclass(frozen=True, slots=True)
class EntryAccount:
    account_id: UUID
    book_id: UUID
    display_name: str
    asset_code: str
    account_type: AccountType
    account_subtype: str | None = None
    system_role: AccountSystemRole = AccountSystemRole.STANDARD
    status: str = "active"
    last4: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("account display_name must be nonblank")
        if not self.asset_code or self.asset_code != self.asset_code.upper():
            raise ValueError("account asset_code must be uppercase")
        if self.last4 is not None and (
            len(self.last4) != 4 or not self.last4.isascii() or not self.last4.isdigit()
        ):
            raise ValueError("account last4 must contain four ASCII digits")


@dataclass(frozen=True, slots=True)
class AccountResolution:
    account: EntryAccount | None
    choices: tuple[ClarificationChoice, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return self.account is None and bool(self.choices)


def resolve_account(
    reference: AccountRef,
    *,
    accounts: tuple[EntryAccount, ...],
    book_id: UUID,
    asset_code: str,
    use: AccountUse,
    category_ids: frozenset[UUID] = frozenset(),
) -> AccountResolution:
    if reference.account_id is not None:
        if reference.account_id in category_ids:
            raise EntryGatewayError(
                EntryErrorCode.ACCOUNT_INELIGIBLE,
                "a category ID cannot be used as an account",
                field="account",
            )
        matching_id = tuple(
            account
            for account in accounts
            if account.book_id == book_id and account.account_id == reference.account_id
        )
        if not matching_id:
            raise EntryGatewayError(
                EntryErrorCode.ACCOUNT_NOT_FOUND,
                "account was not found in the requested Book",
                field="account",
            )
        account = matching_id[0]
        _require_eligible(account, asset_code=asset_code, use=use)
        return AccountResolution(account=account)

    assert reference.query is not None
    query = _normalize(reference.query)
    candidates = tuple(
        account
        for account in accounts
        if account.book_id == book_id
        and account.asset_code == asset_code
        and account.status == "active"
        and _is_eligible(account, use=use)
        and query in _account_query_names(account)
        and (reference.last4 is None or account.last4 == reference.last4)
        and (
            reference.subtype is None
            or account.account_subtype == reference.subtype
        )
    )
    if not candidates:
        raise EntryGatewayError(
            EntryErrorCode.ACCOUNT_NOT_FOUND,
            "no eligible account exactly matches the query",
            field="account",
        )
    if len(candidates) > 1:
        return AccountResolution(
            account=None,
            choices=tuple(
                ClarificationChoice(
                    choice_id=str(account.account_id),
                    label=_choice_label(account),
                    resolved_id=account.account_id,
                )
                for account in sorted(candidates, key=lambda item: str(item.account_id))
            ),
        )
    return AccountResolution(account=candidates[0])


def resolve_internal_account(
    *,
    accounts: tuple[EntryAccount, ...],
    book_id: UUID,
    asset_code: str,
    role: AccountSystemRole,
) -> EntryAccount:
    if role is AccountSystemRole.STANDARD:
        raise ValueError("an internal account role is required")
    expected_type = {
        AccountSystemRole.EXPENSE_CLEARING: AccountType.EXPENSE,
        AccountSystemRole.INCOME_CLEARING: AccountType.INCOME,
        AccountSystemRole.BALANCE_ADJUSTMENT: AccountType.EQUITY,
    }.get(role)
    candidates = tuple(
        account
        for account in accounts
        if account.book_id == book_id
        and account.asset_code == asset_code
        and account.system_role is role
        and account.status == "active"
        and (expected_type is None or account.account_type is expected_type)
    )
    if len(candidates) != 1:
        raise EntryGatewayError(
            EntryErrorCode.ACCOUNT_INELIGIBLE,
            f"exactly one active {role.value} account is required",
            field="account",
        )
    return candidates[0]


def _require_eligible(
    account: EntryAccount,
    *,
    asset_code: str,
    use: AccountUse,
) -> None:
    if account.status != "active":
        raise EntryGatewayError(
            EntryErrorCode.ACCOUNT_CLOSED,
            "selected account is not active",
            field="account",
        )
    if account.asset_code != asset_code or not _is_eligible(account, use=use):
        raise EntryGatewayError(
            EntryErrorCode.ACCOUNT_INELIGIBLE,
            "selected account is not eligible for this entry",
            field="account",
        )


def _is_eligible(account: EntryAccount, *, use: AccountUse) -> bool:
    if account.system_role is not AccountSystemRole.STANDARD:
        return False
    is_asset = account.account_type is AccountType.ASSET
    is_card = (
        account.account_type is AccountType.LIABILITY
        and account.account_subtype == "credit_card"
    )
    if use is AccountUse.EXPENSE_SOURCE:
        return is_asset or is_card
    if use is AccountUse.CARD_PAYMENT_CARD:
        return is_card
    return is_asset


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def derive_account_last4(current_name: str) -> str | None:
    """Derive a terminal ASCII last4 without treating longer numbers as suffixes.

    Outer whitespace is ignored. Bare ``1234`` and compact ``(1234)`` suffixes
    are accepted; spaces inside the parentheses are intentionally unsupported.
    A digit immediately before a bare suffix, including a Unicode digit,
    rejects the candidate.
    """

    value = current_name.strip()
    parenthesized = (
        len(value) >= 6
        and value.endswith(")")
        and value[-6] == "("
    )
    start = len(value) - (5 if parenthesized else 4)
    end = len(value) - 1 if parenthesized else len(value)
    if start < 0:
        return None
    candidate = value[start:end]
    if (
        len(candidate) != 4
        or not candidate.isascii()
        or not candidate.isdigit()
    ):
        return None
    if start > 0 and value[start - 1].isdigit():
        return None
    return candidate


def _account_query_names(account: EntryAccount) -> frozenset[str]:
    names = {
        _normalize(account.display_name),
        *(_normalize(alias) for alias in account.aliases),
    }
    if account.last4 == derive_account_last4(account.display_name):
        base_name = _display_name_without_last4(account.display_name)
        if base_name:
            names.add(_normalize(base_name))
    return frozenset(names)


def _display_name_without_last4(current_name: str) -> str:
    value = current_name.strip()
    if derive_account_last4(value) is None:
        return value
    if len(value) >= 6 and value.endswith(")") and value[-6] == "(":
        return value[:-6].rstrip()
    return value[:-4].rstrip()


def _choice_label(account: EntryAccount) -> str:
    suffix = (
        f" ••••{account.last4}"
        if account.last4 is not None
        and derive_account_last4(account.display_name) != account.last4
        else ""
    )
    return f"{account.display_name}{suffix}"


__all__ = [
    "AccountResolution",
    "AccountUse",
    "EntryAccount",
    "derive_account_last4",
    "resolve_account",
    "resolve_internal_account",
]
