from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from ...domain.journal import (
    AccountCatalogSnapshot,
    AccountClosed,
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
from ...infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
)
from ...infrastructure.db.repositories.catalogs import (
    CatalogNotFound,
    CatalogRepository,
)
from ...serialization.canonical_json import JSONValue, format_utc_microseconds
from ..command_bus import execute_financial
from ..event_batch import PendingEvent
from ..idempotency import (
    AuthorizationScope,
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork


_AMOUNT_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_POST_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/journal.post",
)
_GENERAL_JOURNAL_KINDS = frozenset(
    {
        TransactionKind.STANDARD,
        TransactionKind.OPENING,
        TransactionKind.ADJUSTMENT,
        TransactionKind.TRANSFER,
    }
)


class JournalWriteForbidden(PermissionError):
    pass


class AssetUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PostTransactionPosting:
    posting_id: UUID
    account_id: UUID
    asset_code: str
    side: PostingSide
    amount: str

    def __post_init__(self) -> None:
        if type(self.posting_id) is not UUID:
            raise IdempotencyValidationError("posting_id must be a UUID")
        if type(self.account_id) is not UUID:
            raise IdempotencyValidationError("account_id must be a UUID")
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or len(self.asset_code) > 16
            or self.asset_code.upper() != self.asset_code
        ):
            raise IdempotencyValidationError("posting asset_code is invalid")
        if type(self.side) is not PostingSide:
            raise IdempotencyValidationError("posting side is invalid")
        if (
            type(self.amount) is not str
            or not self.amount
            or _AMOUNT_LITERAL.fullmatch(self.amount) is None
        ):
            raise IdempotencyValidationError(
                "posting amount must be an unsigned plain-decimal string"
            )

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "account_id": str(self.account_id),
            "amount": self.amount,
            "asset_code": self.asset_code,
            "posting_id": str(self.posting_id),
            "side": self.side.value,
        }


@dataclass(frozen=True, slots=True)
class PostTransactionCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    kind: TransactionKind
    postings: tuple[PostTransactionPosting, ...]
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="journal.post", init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("book_id", self.book_id),
            ("command_id", self.command_id),
            ("transaction_id", self.transaction_id),
        ):
            if type(value) is not UUID:
                raise IdempotencyValidationError(f"{name} must be a UUID")
        if (
            type(self.expected_stream_version) is not int
            or self.expected_stream_version < 0
        ):
            raise IdempotencyValidationError(
                "expected_stream_version must be a non-negative integer"
            )
        if (
            type(self.kind) is not TransactionKind
            or self.kind not in _GENERAL_JOURNAL_KINDS
        ):
            raise IdempotencyValidationError(
                "kind must use the general journal transaction contract"
            )
        if (
            type(self.postings) is not tuple
            or len(self.postings) < 2
            or any(
                type(posting) is not PostTransactionPosting for posting in self.postings
            )
        ):
            raise IdempotencyValidationError(
                "postings must be an immutable tuple with at least two items"
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
            "kind": self.kind.value,
            "postings": [posting.canonical_value() for posting in self.postings],
            "transaction_id": str(self.transaction_id),
        }


Authorize = Callable[..., AuthorizationScope]
UnitOfWorkFactory = Callable[[], UnitOfWork]


def authorize_journal_write(
    session: Session,
    actor: CommandActor,
    book_id: UUID,
    *,
    lock_membership: bool,
) -> AuthorizationScope:
    try:
        membership = AuthRepository(session).get_membership(
            book_id,
            actor.subject_id,
            lock=RowLock.SHARE if lock_membership else RowLock.NONE,
        )
    except AuthRecordNotFound:
        raise JournalWriteForbidden("journal write is not authorized") from None
    if (
        membership.status != "active"
        or membership.revoked_at is not None
        or "ledger:write" not in membership.scopes
    ):
        raise JournalWriteForbidden("journal write is not authorized")
    return AuthorizationScope(
        book_id=book_id,
        actor_subject_id=actor.subject_id,
        role=membership.role,
        scopes=membership.scopes,
    )


def execute_post_transaction(
    command: PostTransactionCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not PostTransactionCommand:
        raise IdempotencyValidationError("command must be a PostTransactionCommand")
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected post transaction command")
        return _build_plan(command, uow, locked_head, actor=actor)

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


def _build_plan(
    command: PostTransactionCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    if locked_head.book_id != command.book_id:
        raise IdempotencyValidationError("locked Book does not match command")
    catalogs = CatalogRepository(uow.session)

    db_accounts = {}
    for account_id in sorted(
        {posting.account_id for posting in command.postings},
        key=str,
    ):
        try:
            db_accounts[account_id] = catalogs.get_account(
                command.book_id,
                account_id,
                lock=RowLock.SHARE,
            )
        except CatalogNotFound:
            # Preserve a domain-shaped unknown-account failure without allowing a
            # cross-Book lookup to disclose another Book's catalog.
            continue

    asset_policies: dict[str, AssetPolicy] = {}
    for asset_code in sorted({posting.asset_code for posting in command.postings}):
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
    posting_drafts = tuple(
        PostingDraft(
            posting_id=str(posting.posting_id),
            position=position,
            account_id=str(posting.account_id),
            asset_code=posting.asset_code,
            side=posting.side,
            units=asset_policies[posting.asset_code].parse_online(posting.amount).units,
        )
        for position, posting in enumerate(command.postings)
    )
    domain_command = PostTransaction(
        transaction_id=str(command.transaction_id),
        book_id=str(command.book_id),
        kind=command.kind,
        postings=posting_drafts,
    )
    JournalValidator.validate(
        domain_command,
        catalog=AccountCatalogSnapshot(accounts=domain_accounts),
    )

    payload = JournalTransactionPosted(
        transaction_id=command.transaction_id,
        kind=command.kind,
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
    pending = PendingEvent(
        event_id=uuid5(_POST_EVENT_NAMESPACE, str(command.command_id)),
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


__all__ = [
    "AccountClosed",
    "AssetUnavailable",
    "JournalWriteForbidden",
    "PostTransactionCommand",
    "PostTransactionPosting",
    "authorize_journal_write",
    "execute_post_transaction",
]
