from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from ...domain.journal import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountSystemRole,
    JournalValidator,
    PostingDraft,
    PostingSide,
    PostTransaction,
    TransactionKind,
)
from ...domain.journal.events import (
    FinancialExternalReference,
    JournalPostingFact,
    JournalTransactionPosted,
)
from ...domain.money import AssetPolicy
from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.catalogs import (
    CatalogNotFound,
    CatalogRepository,
)
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork
from .post_transaction import AssetUnavailable, Authorize, authorize_journal_write


_AMOUNT_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_FX_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.record-fx",
)


@dataclass(frozen=True, slots=True)
class RecordFxCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    source_account_id: UUID
    source_trading_account_id: UUID
    source_asset_code: str
    source_amount: str
    target_trading_account_id: UUID
    target_account_id: UUID
    target_asset_code: str
    target_amount: str
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="journal.record_fx", init=False)

    def __post_init__(self) -> None:
        identifiers = (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
            ("source_account_id", self.source_account_id),
            ("source_trading_account_id", self.source_trading_account_id),
            ("target_trading_account_id", self.target_trading_account_id),
            ("target_account_id", self.target_account_id),
        )
        for name, value in identifiers:
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        account_ids = tuple(value for name, value in identifiers if name.endswith("account_id"))
        if len(account_ids) != len(set(account_ids)):
            raise IdempotencyValidationError("FX account identities must be distinct")
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version < 0
        ):
            raise IdempotencyValidationError(
                "expected_stream_version must be a non-negative integer"
            )
        for name, value in (
            ("source_asset_code", self.source_asset_code),
            ("target_asset_code", self.target_asset_code),
        ):
            if (
                type(value) is not str
                or not value
                or len(value) > 16
                or value.upper() != value
            ):
                raise IdempotencyValidationError(f"{name} is invalid")
        if self.source_asset_code == self.target_asset_code:
            raise IdempotencyValidationError("FX requires two different assets")
        for name, value in (
            ("source_amount", self.source_amount),
            ("target_amount", self.target_amount),
        ):
            if (
                type(value) is not str
                or not value
                or _AMOUNT_LITERAL.fullmatch(value) is None
            ):
                raise IdempotencyValidationError(
                    f"{name} must be an unsigned plain-decimal string"
                )
        try:
            format_utc_microseconds(self.effective_at)
        except (TypeError, ValueError):
            raise IdempotencyValidationError(
                "effective_at must be a timezone-aware datetime"
            ) from None
        if self.description_ref is not None and type(self.description_ref) is not UUID:
            raise IdempotencyValidationError("description_ref must be a UUID or null")
        if type(self.external_references) is not tuple or any(
            type(reference) is not FinancialExternalReference
            for reference in self.external_references
        ):
            raise IdempotencyValidationError(
                "external_references must be an immutable typed tuple"
            )

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "description_ref": (
                None if self.description_ref is None else str(self.description_ref)
            ),
            "effective_at": format_utc_microseconds(self.effective_at),
            "expected_stream_version": self.expected_stream_version,
            "external_references": [
                reference.model_dump(mode="json")
                for reference in self.external_references
            ],
            "source_account_id": str(self.source_account_id),
            "source_amount": self.source_amount,
            "source_asset_code": self.source_asset_code,
            "source_trading_account_id": str(self.source_trading_account_id),
            "target_account_id": str(self.target_account_id),
            "target_amount": self.target_amount,
            "target_asset_code": self.target_asset_code,
            "target_trading_account_id": str(self.target_trading_account_id),
            "transaction_id": str(self.transaction_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def execute_record_fx(
    command: RecordFxCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not RecordFxCommand:
        raise IdempotencyValidationError("command must be a RecordFxCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected record FX command")
        return _build_fx_plan(command, uow, locked_head, actor=actor)

    return execute_financial(
        command,
        raw_key=raw_key,
        actor=actor,
        authorize=authorize,
        handler=handler,
        uow_factory=uow_factory,
        ledger_committer=committer,
        max_attempts=max_attempts,
    )


def _build_fx_plan(
    command: RecordFxCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")

    payload = _build_fx_payload(command, uow)
    pending = PendingEvent(
        event_id=uuid5(_FX_EVENT_NAMESPACE, f"{command.command_id}:event"),
        stream_type="journal_transaction",
        stream_id=command.transaction_id,
        payload=payload,
        command_id=command.command_id,
        actor_subject_id=actor.subject_id,
        correlation_id=command.command_id,
        causation_event_id=None,
        effective_at=command.effective_at,
    )
    return LedgerWritePlan(
        expected_stream_versions={
            ("journal_transaction", command.transaction_id): (
                command.expected_stream_version
            )
        },
        events=(pending,),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(command.transaction_id),
            "as_of_book_position": locked_head.last_position + 1,
        },
    )


def _build_fx_payload(
    command: RecordFxCommand,
    uow: UnitOfWork,
) -> JournalTransactionPosted:
    catalogs = CatalogRepository(uow.session)
    account_ids = (
        command.target_account_id,
        command.target_trading_account_id,
        command.source_trading_account_id,
        command.source_account_id,
    )
    db_accounts = {}
    for account_id in sorted(account_ids, key=str):
        try:
            db_accounts[account_id] = catalogs.get_account(
                command.book_id,
                account_id,
                lock=RowLock.SHARE,
            )
        except CatalogNotFound:
            continue

    asset_policies: dict[str, AssetPolicy] = {}
    for asset_code in sorted(
        (command.source_asset_code, command.target_asset_code)
    ):
        try:
            asset = catalogs.get_asset(asset_code, lock=RowLock.SHARE)
        except CatalogNotFound:
            raise AssetUnavailable(f"asset is unavailable: {asset_code}") from None
        if asset.status != "active":
            raise AssetUnavailable(f"asset is unavailable: {asset_code}")
        asset_policies[asset_code] = AssetPolicy(
            input_scale=asset.input_scale,
            ledger_scale=asset.ledger_scale,
        )

    target_units = asset_policies[command.target_asset_code].parse_online(
        command.target_amount
    ).units
    source_units = asset_policies[command.source_asset_code].parse_online(
        command.source_amount
    ).units
    leg_values = (
        (
            "target-user",
            command.target_account_id,
            command.target_asset_code,
            PostingSide.DEBIT,
            target_units,
        ),
        (
            "target-trading",
            command.target_trading_account_id,
            command.target_asset_code,
            PostingSide.CREDIT,
            target_units,
        ),
        (
            "source-trading",
            command.source_trading_account_id,
            command.source_asset_code,
            PostingSide.DEBIT,
            source_units,
        ),
        (
            "source-user",
            command.source_account_id,
            command.source_asset_code,
            PostingSide.CREDIT,
            source_units,
        ),
    )
    posting_drafts = tuple(
        PostingDraft(
            posting_id=str(
                uuid5(_FX_EVENT_NAMESPACE, f"{command.command_id}:{leg_name}")
            ),
            position=position,
            account_id=str(account_id),
            asset_code=asset_code,
            side=side,
            units=units,
        )
        for position, (leg_name, account_id, asset_code, side, units) in enumerate(
            leg_values
        )
    )
    domain_accounts = tuple(
        AccountSnapshot(
            account_id=str(snapshot.account_id),
            book_id=str(snapshot.book_id),
            asset_code=snapshot.asset_code,
            system_role=AccountSystemRole(snapshot.system_role or "standard"),
            status=snapshot.status,
        )
        for snapshot in db_accounts.values()
    )
    domain_command = PostTransaction(
        transaction_id=str(command.transaction_id),
        book_id=str(command.book_id),
        kind=TransactionKind.FX,
        postings=posting_drafts,
    )
    JournalValidator.validate(
        domain_command,
        catalog=AccountCatalogSnapshot(accounts=domain_accounts),
    )

    return JournalTransactionPosted(
        transaction_id=command.transaction_id,
        kind=TransactionKind.FX,
        postings=tuple(
            JournalPostingFact(
                posting_id=UUID(posting.posting_id),
                position=posting.position,
                account_id=UUID(posting.account_id),
                asset_code=posting.asset_code,
                side=posting.side,
                units=str(posting.units),
            )
            for posting in posting_drafts
        ),
        description_ref=command.description_ref,
        external_references=command.external_references,
    )


__all__ = ["RecordFxCommand", "execute_record_fx"]
