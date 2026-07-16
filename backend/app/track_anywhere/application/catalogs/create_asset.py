from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ...infrastructure.db.models.catalog import AssetRecord
from ...serialization.canonical_json import JSONValue
from ..command_bus import execute
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    CommandResult,
    IdempotencyValidationError,
)
from ..unit_of_work import UnitOfWork
from ._authorization import authorize_catalog_write, require_catalog_write


class AssetMetadataConflict(ValueError):
    pass


def _validate_asset_fields(
    *,
    book_id: UUID,
    asset_code: str,
    kind: str,
    ledger_scale: int,
    input_scale: int,
    display_scale: int,
    current_name: str,
) -> None:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if (
        type(asset_code) is not str
        or not asset_code
        or len(asset_code) > 16
        or asset_code != asset_code.upper()
    ):
        raise ValueError("asset_code is invalid")
    if type(kind) is not str or not kind.strip():
        raise ValueError("kind must be nonblank")
    if (
        type(ledger_scale) is not int
        or type(input_scale) is not int
        or type(display_scale) is not int
        or not 0 <= input_scale <= ledger_scale <= 30
        or not 0 <= display_scale <= ledger_scale
    ):
        raise ValueError("asset scales are invalid")
    if type(current_name) is not str or not current_name.strip():
        raise ValueError("current_name must be nonblank")


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
        _validate_asset_fields(
            book_id=self.book_id,
            asset_code=self.asset_code,
            kind=self.kind,
            ledger_scale=self.ledger_scale,
            input_scale=self.input_scale,
            display_scale=self.display_scale,
            current_name=self.current_name,
        )


@dataclass(frozen=True, slots=True)
class CreateOrReuseAssetCommand:
    book_id: UUID
    command_id: UUID
    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    display_scale: int
    current_name: str
    operation: str = field(default="catalog.asset.create-or-reuse", init=False)

    def __post_init__(self) -> None:
        if type(self.command_id) is not UUID:
            raise IdempotencyValidationError("command_id must be a UUID")
        _validate_asset_fields(
            book_id=self.book_id,
            asset_code=self.asset_code,
            kind=self.kind,
            ledger_scale=self.ledger_scale,
            input_scale=self.input_scale,
            display_scale=self.display_scale,
            current_name=self.current_name,
        )

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "asset_code": self.asset_code,
            "current_name": self.current_name.strip(),
            "display_scale": self.display_scale,
            "input_scale": self.input_scale,
            "kind": self.kind.strip(),
            "ledger_scale": self.ledger_scale,
        }


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


def execute_create_or_reuse_asset(
    command: CreateOrReuseAssetCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not CreateOrReuseAssetCommand:
        raise IdempotencyValidationError("command must be a CreateOrReuseAssetCommand")

    def handler(received: object, uow: UnitOfWork) -> CommandResult:
        if received is not command:
            raise IdempotencyValidationError("unexpected create asset command")
        created = _create_or_reuse_asset(command, uow)
        return CommandResult(
            response_schema_version=1,
            status_code=201 if created else 200,
            body={
                "asset_code": command.asset_code,
                "as_of_book_position": _head(uow, command.book_id),
                "created": created,
            },
        )

    return execute(
        command,
        raw_key=raw_key,
        actor=actor,
        authorize=authorize_catalog_write,
        handler=handler,
        uow_factory=uow_factory,
        max_attempts=max_attempts,
    )


def _create_or_reuse_asset(
    command: CreateOrReuseAssetCommand,
    uow: UnitOfWork,
) -> bool:
    inserted = uow.session.execute(
        insert(AssetRecord)
        .values(
            asset_code=command.asset_code,
            kind=command.kind.strip(),
            ledger_scale=command.ledger_scale,
            input_scale=command.input_scale,
            display_scale=command.display_scale,
            current_name=command.current_name.strip(),
            status="active",
        )
        .on_conflict_do_nothing(index_elements=[AssetRecord.asset_code])
        .returning(AssetRecord.asset_code)
    ).scalar_one_or_none()
    if inserted is not None:
        return True

    existing = uow.session.scalar(
        select(AssetRecord)
        .where(AssetRecord.asset_code == command.asset_code)
        .with_for_update(read=True)
    )
    if existing is None or not _metadata_matches(command, existing):
        raise AssetMetadataConflict("asset_code already exists with different metadata")
    return False


def _metadata_matches(
    command: CreateOrReuseAssetCommand,
    existing: AssetRecord,
) -> bool:
    return (
        existing.kind == command.kind.strip()
        and existing.ledger_scale == command.ledger_scale
        and existing.input_scale == command.input_scale
        and existing.display_scale == command.display_scale
        and existing.current_name == command.current_name.strip()
        and existing.status == "active"
    )


def _head(uow: UnitOfWork, book_id: UUID) -> int:
    from ...infrastructure.db.models.event_store import BookEventHeadRecord

    head = uow.session.get(BookEventHeadRecord, book_id)
    if head is None:
        raise ValueError("Book event head is unavailable")
    return head.last_position


__all__ = [
    "AssetMetadataConflict",
    "CreateAsset",
    "CreateOrReuseAssetCommand",
    "create_asset",
    "execute_create_or_reuse_asset",
]
