from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import and_, func, select

from ...domain.credit_cards.events import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from ...domain.journal import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountSystemRole,
    AccountType,
    JournalValidator,
    PostingDraft,
    PostingSide,
    PostTransaction,
    TransactionKind,
)

from ...domain.journal.events import (
    FinancialExternalReference,
    JournalPostingFact,
)
from ...domain.money import AssetPolicy
from ...infrastructure.db.models.credit_cards import CreditCardTransactionRecord
from ...infrastructure.db.models.projections import (
    JournalTransactionRecord,
    TransactionReversalRecord,
)
from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.catalogs import CatalogRepository
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
from ..journal.post_transaction import Authorize, authorize_journal_write


_AMOUNT_LITERAL = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)
_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/credit-card.record",
)


class CreditCardAccountInvalid(ValueError):
    pass


class CreditCardRefundSourceInvalid(ValueError):
    pass


class CreditCardRefundConflict(RuntimeError):
    pass


class CreditCardRefundExceeded(CreditCardRefundConflict):
    pass


class CreditCardRefundSourceReversed(CreditCardRefundConflict):
    pass


def _validate_common(command: object) -> None:
    for name in ("book_id", "command_id", "transaction_id", "card_account_id"):
        if type(getattr(command, name, None)) is not UUID:
            raise IdempotencyValidationError(f"{name} must be a UUID")
    expected = getattr(command, "expected_stream_version", None)
    if type(expected) is not int or expected != 0:
        raise IdempotencyValidationError(
            "a credit-card transaction stream must start at version zero"
        )
    asset_code = getattr(command, "asset_code", None)
    if (
        type(asset_code) is not str
        or not asset_code
        or len(asset_code) > 16
        or asset_code.upper() != asset_code
    ):
        raise IdempotencyValidationError("asset_code is invalid")
    amount = getattr(command, "amount", None)
    if (
        type(amount) is not str
        or _AMOUNT_LITERAL.fullmatch(amount) is None
        or amount.rstrip("0").rstrip(".") in {"", "0"}
    ):
        raise IdempotencyValidationError(
            "amount must be a positive unsigned plain-decimal string"
        )
    try:
        format_utc_microseconds(getattr(command, "effective_at", None))
    except (TypeError, ValueError):
        raise IdempotencyValidationError(
            "effective_at must be a timezone-aware datetime"
        ) from None
    description_ref = getattr(command, "description_ref", None)
    if description_ref is not None and type(description_ref) is not UUID:
        raise IdempotencyValidationError("description_ref must be a UUID or null")
    references = getattr(command, "external_references", None)
    if type(references) is not tuple or any(
        type(reference) is not FinancialExternalReference for reference in references
    ):
        raise IdempotencyValidationError(
            "external_references must be an immutable typed tuple"
        )


def _payload(command: object, *, counter_name: str) -> dict[str, JSONValue]:
    return {
        "amount": getattr(command, "amount"),
        "asset_code": getattr(command, "asset_code"),
        "card_account_id": str(getattr(command, "card_account_id")),
        counter_name: str(getattr(command, counter_name)),
        "description_ref": (
            None
            if getattr(command, "description_ref") is None
            else str(getattr(command, "description_ref"))
        ),
        "effective_at": format_utc_microseconds(getattr(command, "effective_at")),
        "expected_stream_version": getattr(command, "expected_stream_version"),
        "external_references": [
            reference.model_dump(mode="json")
            for reference in getattr(command, "external_references")
        ],
        "transaction_id": str(getattr(command, "transaction_id")),
    }


@dataclass(frozen=True, slots=True)
class ChargeCreditCardCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    card_account_id: UUID
    expense_account_id: UUID
    asset_code: str
    amount: str
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="credit_card.charge", init=False)

    def __post_init__(self) -> None:
        _validate_common(self)
        if type(self.expense_account_id) is not UUID:
            raise IdempotencyValidationError("expense_account_id must be a UUID")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return _payload(self, counter_name="expense_account_id")


@dataclass(frozen=True, slots=True)
class FeeCreditCardCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    card_account_id: UUID
    expense_account_id: UUID
    asset_code: str
    amount: str
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="credit_card.fee", init=False)

    def __post_init__(self) -> None:
        _validate_common(self)
        if type(self.expense_account_id) is not UUID:
            raise IdempotencyValidationError("expense_account_id must be a UUID")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return _payload(self, counter_name="expense_account_id")


@dataclass(frozen=True, slots=True)
class PaymentCreditCardCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    card_account_id: UUID
    source_account_id: UUID
    asset_code: str
    amount: str
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="credit_card.payment", init=False)

    def __post_init__(self) -> None:
        _validate_common(self)
        if type(self.source_account_id) is not UUID:
            raise IdempotencyValidationError("source_account_id must be a UUID")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return _payload(self, counter_name="source_account_id")


@dataclass(frozen=True, slots=True)
class RefundCreditCardCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: int
    card_account_id: UUID
    original_transaction_id: UUID
    asset_code: str
    amount: str
    effective_at: datetime
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()
    operation: str = field(default="credit_card.refund", init=False)

    def __post_init__(self) -> None:
        _validate_common(self)
        if type(self.original_transaction_id) is not UUID:
            raise IdempotencyValidationError("original_transaction_id must be a UUID")
        if self.original_transaction_id == self.transaction_id:
            raise IdempotencyValidationError("a refund cannot reference itself")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return _payload(self, counter_name="original_transaction_id")


def execute_charge_credit_card(
    command: ChargeCreditCardCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not ChargeCreditCardCommand:
        raise IdempotencyValidationError("command must be a ChargeCreditCardCommand")
    return _execute_credit_card(command, intent=CreditCardIntent.CHARGE, **kwargs)


def execute_fee_credit_card(
    command: FeeCreditCardCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not FeeCreditCardCommand:
        raise IdempotencyValidationError("command must be a FeeCreditCardCommand")
    return _execute_credit_card(command, intent=CreditCardIntent.FEE, **kwargs)


def execute_payment_credit_card(
    command: PaymentCreditCardCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not PaymentCreditCardCommand:
        raise IdempotencyValidationError("command must be a PaymentCreditCardCommand")
    return _execute_credit_card(command, intent=CreditCardIntent.PAYMENT, **kwargs)


def execute_refund_credit_card(
    command: RefundCreditCardCommand,
    **kwargs,
) -> CommandOutcome:
    if type(command) is not RefundCreditCardCommand:
        raise IdempotencyValidationError("command must be a RefundCreditCardCommand")
    return _execute_credit_card(command, intent=CreditCardIntent.REFUND, **kwargs)


def _execute_credit_card(
    command: (
        ChargeCreditCardCommand
        | FeeCreditCardCommand
        | PaymentCreditCardCommand
        | RefundCreditCardCommand
    ),
    *,
    intent: CreditCardIntent,
    raw_key: str,
    actor: CommandActor,
    uow_factory,
    authorize: Authorize = authorize_journal_write,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected credit-card command")
        if locked_head.book_id != command.book_id:
            raise IdempotencyValidationError("locked Book does not match command")
        payload = _build_payload(command, intent=intent, uow=uow)
        pending = PendingEvent(
            event_id=uuid5(_EVENT_NAMESPACE, str(command.command_id)),
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
                "intent": intent.value,
                "as_of_book_position": locked_head.last_position + 1,
            },
        )

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


def _build_payload(
    command: (
        ChargeCreditCardCommand
        | FeeCreditCardCommand
        | PaymentCreditCardCommand
        | RefundCreditCardCommand
    ),
    *,
    intent: CreditCardIntent,
    uow: UnitOfWork,
) -> CreditCardTransactionRecorded:
    catalogs = CatalogRepository(uow.session)
    card = catalogs.get_account(
        command.book_id,
        command.card_account_id,
        lock=RowLock.SHARE,
    )
    if (
        card.account_type != AccountType.LIABILITY.value
        or card.account_subtype != "credit_card"
    ):
        raise CreditCardAccountInvalid("card account must be a credit_card liability")

    original_transaction_id: UUID | None = None
    if type(command) is RefundCreditCardCommand:
        original = _load_refund_source(command, uow)
        counter = catalogs.get_account(
            command.book_id,
            original.counter_account_id,
            lock=RowLock.SHARE,
        )
        original_transaction_id = command.original_transaction_id
    elif type(command) is PaymentCreditCardCommand:
        counter = catalogs.get_account(
            command.book_id,
            command.source_account_id,
            lock=RowLock.SHARE,
        )
    else:
        counter = catalogs.get_account(
            command.book_id,
            command.expense_account_id,
            lock=RowLock.SHARE,
        )

    expected_counter_type = (
        AccountType.ASSET if intent is CreditCardIntent.PAYMENT else AccountType.EXPENSE
    )
    if counter.account_type != expected_counter_type.value:
        raise CreditCardAccountInvalid(
            f"{intent.value} counter account must be {expected_counter_type.value}"
        )
    if (
        card.asset_code != command.asset_code
        or counter.asset_code != command.asset_code
    ):
        raise CreditCardAccountInvalid(
            "credit-card accounts must use the requested single asset"
        )
    asset = catalogs.get_asset(command.asset_code, lock=RowLock.SHARE)
    if asset.status != "active":
        raise CreditCardAccountInvalid("credit-card asset is unavailable")
    units = (
        AssetPolicy(
            input_scale=asset.input_scale,
            ledger_scale=asset.ledger_scale,
        )
        .parse_online(command.amount)
        .units
    )

    if type(command) is RefundCreditCardCommand:
        _validate_refund_capacity(command, uow, requested_units=units)

    if intent in {CreditCardIntent.CHARGE, CreditCardIntent.FEE}:
        legs = (
            (counter.account_id, PostingSide.DEBIT),
            (card.account_id, PostingSide.CREDIT),
        )
    else:
        legs = (
            (card.account_id, PostingSide.DEBIT),
            (counter.account_id, PostingSide.CREDIT),
        )
    posting_drafts = tuple(
        PostingDraft(
            posting_id=str(
                uuid5(
                    _EVENT_NAMESPACE,
                    f"{command.transaction_id}:posting:{position}",
                )
            ),
            position=position,
            account_id=str(account_id),
            asset_code=command.asset_code,
            side=side,
            units=units,
        )
        for position, (account_id, side) in enumerate(legs)
    )
    JournalValidator.validate(
        PostTransaction(
            transaction_id=str(command.transaction_id),
            book_id=str(command.book_id),
            kind=TransactionKind.STANDARD,
            postings=posting_drafts,
        ),
        catalog=AccountCatalogSnapshot(
            accounts=tuple(
                AccountSnapshot(
                    account_id=str(snapshot.account_id),
                    book_id=str(snapshot.book_id),
                    asset_code=snapshot.asset_code,
                    account_type=AccountType(snapshot.account_type),
                    account_subtype=snapshot.account_subtype,
                    system_role=AccountSystemRole(snapshot.system_role or "standard"),
                    status=snapshot.status,
                )
                for snapshot in (card, counter)
            )
        ),
    )
    return CreditCardTransactionRecorded(
        intent=intent,
        transaction_id=command.transaction_id,
        card_account_id=card.account_id,
        counter_account_id=counter.account_id,
        original_transaction_id=original_transaction_id,
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


def _load_refund_source(
    command: RefundCreditCardCommand,
    uow: UnitOfWork,
) -> CreditCardTransactionRecord:
    original = uow.session.execute(
        select(CreditCardTransactionRecord)
        .where(
            CreditCardTransactionRecord.book_id == command.book_id,
            CreditCardTransactionRecord.transaction_id
            == command.original_transaction_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if original is None or original.intent != CreditCardIntent.CHARGE.value:
        raise CreditCardRefundSourceInvalid(
            "refund source must be an existing typed credit-card charge"
        )
    if (
        original.card_account_id != command.card_account_id
        or original.asset_code != command.asset_code
    ):
        raise CreditCardRefundSourceInvalid(
            "refund must use the original charge card and asset"
        )
    reversed_source = uow.session.execute(
        select(TransactionReversalRecord.reversal_transaction_id).where(
            TransactionReversalRecord.book_id == command.book_id,
            TransactionReversalRecord.original_transaction_id
            == command.original_transaction_id,
        )
    ).scalar_one_or_none()
    if reversed_source is not None:
        raise CreditCardRefundSourceReversed("refund source charge is reversed")
    original_journal = uow.session.get(
        JournalTransactionRecord,
        (command.book_id, command.original_transaction_id),
    )
    if original_journal is None or command.effective_at < original_journal.effective_at:
        raise CreditCardRefundSourceInvalid("refund cannot precede the original charge")
    return original


def _validate_refund_capacity(
    command: RefundCreditCardCommand,
    uow: UnitOfWork,
    *,
    requested_units: int,
) -> None:
    original = uow.session.get(
        CreditCardTransactionRecord,
        (command.book_id, command.original_transaction_id),
    )
    if original is None:
        raise CreditCardRefundSourceInvalid("refund source is missing")
    active_refunds = uow.session.execute(
        select(func.coalesce(func.sum(CreditCardTransactionRecord.units), 0))
        .outerjoin(
            TransactionReversalRecord,
            and_(
                TransactionReversalRecord.book_id
                == CreditCardTransactionRecord.book_id,
                TransactionReversalRecord.original_transaction_id
                == CreditCardTransactionRecord.transaction_id,
            ),
        )
        .where(
            CreditCardTransactionRecord.book_id == command.book_id,
            CreditCardTransactionRecord.intent == CreditCardIntent.REFUND.value,
            CreditCardTransactionRecord.original_transaction_id
            == command.original_transaction_id,
            TransactionReversalRecord.reversal_transaction_id.is_(None),
        )
    ).scalar_one()
    if int(active_refunds) + requested_units > int(original.units):
        raise CreditCardRefundExceeded(
            "active refunds cannot exceed the original charge amount"
        )


__all__ = [
    "ChargeCreditCardCommand",
    "CreditCardAccountInvalid",
    "CreditCardRefundConflict",
    "CreditCardRefundExceeded",
    "CreditCardRefundSourceInvalid",
    "CreditCardRefundSourceReversed",
    "FeeCreditCardCommand",
    "PaymentCreditCardCommand",
    "RefundCreditCardCommand",
    "execute_charge_credit_card",
    "execute_fee_credit_card",
    "execute_payment_credit_card",
    "execute_refund_credit_card",
]
