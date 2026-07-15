from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ...domain.journal import AccountType, is_valid_account_subtype
from ...infrastructure.db.models.catalog import AccountRecord, AssetRecord
from ...infrastructure.db.models.event_store import BookEventHeadRecord
from ..idempotency import CommandActor
from ..unit_of_work import UnitOfWork
from ._authorization import require_catalog_write


class AccountAssetUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreateAccount:
    book_id: UUID
    account_id: UUID
    asset_code: str
    account_type: str
    current_name: str
    account_subtype: str | None = None
    system_role: str | None = None

    def __post_init__(self) -> None:
        if type(self.book_id) is not UUID or type(self.account_id) is not UUID:
            raise ValueError("account identifiers must be UUIDs")
        if (
            type(self.asset_code) is not str
            or self.asset_code != self.asset_code.upper()
        ):
            raise ValueError("asset_code is invalid")
        if type(self.account_type) is not str:
            raise ValueError("account_type is invalid")
        try:
            AccountType(self.account_type)
        except ValueError:
            raise ValueError("account_type is invalid") from None
        if not is_valid_account_subtype(self.account_subtype):
            raise ValueError("account_subtype must be null or a lowercase slug")
        if (
            self.account_subtype == "credit_card"
            and self.account_type != AccountType.LIABILITY.value
        ):
            raise ValueError("credit_card subtype requires liability account_type")
        if type(self.current_name) is not str or not self.current_name.strip():
            raise ValueError("current_name must be nonblank")
        if self.system_role is not None and (
            type(self.system_role) is not str or not self.system_role.strip()
        ):
            raise ValueError("system_role must be null or nonblank")


def create_account(
    command: CreateAccount,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> dict[str, object]:
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        asset = uow.session.get(AssetRecord, command.asset_code)
        if asset is None or asset.status != "active":
            raise AccountAssetUnavailable("account asset is unavailable")
        head = uow.session.get(BookEventHeadRecord, command.book_id)
        if head is None:
            raise ValueError("Book event head is unavailable")
        uow.session.add(
            AccountRecord(
                book_id=command.book_id,
                account_id=command.account_id,
                asset_code=command.asset_code,
                account_type=command.account_type,
                account_subtype=command.account_subtype,
                system_role=(
                    None if command.system_role is None else command.system_role.strip()
                ),
                current_name=command.current_name.strip(),
                status="active",
            )
        )
        uow.session.flush()
        return {
            "account_id": str(command.account_id),
            "as_of_book_position": head.last_position,
        }


__all__ = ["AccountAssetUnavailable", "CreateAccount", "create_account"]
