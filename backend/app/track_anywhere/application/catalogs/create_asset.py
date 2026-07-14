from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ...infrastructure.db.models.catalog import AssetRecord
from ..idempotency import CommandActor
from ..unit_of_work import UnitOfWork
from ._authorization import require_catalog_write


@dataclass(frozen=True, slots=True)
class CreateAsset:
    book_id: UUID
    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    display_scale: int
    current_name: str

    def __post_init__(self) -> None:
        if type(self.book_id) is not UUID:
            raise ValueError("book_id must be a UUID")
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or len(self.asset_code) > 16
            or self.asset_code != self.asset_code.upper()
        ):
            raise ValueError("asset_code is invalid")
        if type(self.kind) is not str or not self.kind.strip():
            raise ValueError("kind must be nonblank")
        if (
            type(self.ledger_scale) is not int
            or type(self.input_scale) is not int
            or type(self.display_scale) is not int
            or not 0 <= self.input_scale <= self.ledger_scale <= 30
            or not 0 <= self.display_scale <= self.ledger_scale
        ):
            raise ValueError("asset scales are invalid")
        if type(self.current_name) is not str or not self.current_name.strip():
            raise ValueError("current_name must be nonblank")


def create_asset(
    command: CreateAsset,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> dict[str, object]:
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        uow.session.add(
            AssetRecord(
                asset_code=command.asset_code,
                kind=command.kind.strip(),
                ledger_scale=command.ledger_scale,
                input_scale=command.input_scale,
                display_scale=command.display_scale,
                current_name=command.current_name.strip(),
                status="active",
            )
        )
        uow.session.flush()
        return {
            "asset_code": command.asset_code,
            "as_of_book_position": _head(uow, command.book_id),
        }


def _head(uow: UnitOfWork, book_id: UUID) -> int:
    from ...infrastructure.db.models.event_store import BookEventHeadRecord

    head = uow.session.get(BookEventHeadRecord, book_id)
    if head is None:
        raise ValueError("Book event head is unavailable")
    return head.last_position


__all__ = ["CreateAsset", "create_asset"]
