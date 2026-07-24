from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from ...domain.credit_cards import CreditCardIntent, CreditCardTransactionRecorded
from ...domain.journal import AccountSystemRole, PostingSide
from ...domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    JournalTransactionPosted,
    ReversalReasonCode,
)
from ...infrastructure.db.models.catalog import (
    AccountRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from ...infrastructure.db.models.credit_cards import CreditCardTransactionRecord
from ...infrastructure.db.models.projections import (
    ReportingLineRecord,
    TransactionExternalReferenceRecord,
)
from ...serialization.canonical_json import JSONValue
from ..catalogs._authorization import require_catalog_write
from ..command_bus import execute_financial
from ..entries.commit import _attach_narrative
from ..entries.compiler import compile_entry
from ..entries.contracts import (
    AccountRef,
    CategoryRef,
    ExpenseEntryInput,
    MoneyDenomination,
    MoneyInput,
)
from ..entries.prepare import load_compilation_context
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..journal.post_transaction import authorize_journal_write
from ..journal.reverse_transaction import (
    ReverseTransactionCommand,
    _build_reverse_plan,
    _load_reversal_source,
)
from ..ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from ..unit_of_work import UnitOfWork


_REPAIR_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/repairs/misclassified-expense",
)
_SYSTEM_ACCOUNT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/accounts/system-role",
)
_INTERNAL_ACCOUNT_SPECS = {
    AccountSystemRole.EXPENSE_CLEARING: ("expense", "expense_clearing"),
    AccountSystemRole.INCOME_CLEARING: ("income", "income_clearing"),
    AccountSystemRole.BALANCE_ADJUSTMENT: ("equity", "balance_adjustment"),
}


class ExpenseHistoryRepairError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RepairCategory:
    category_id: UUID
    category_version_id: UUID
    name: str
    parent_category_id: UUID | None = None
    usage_kind: str = "expense"

    def __post_init__(self) -> None:
        if (
            type(self.category_id) is not UUID
            or type(self.category_version_id) is not UUID
            or (
                self.parent_category_id is not None
                and type(self.parent_category_id) is not UUID
            )
        ):
            raise ValueError("repair category identifiers must be UUIDs")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("repair category name must be nonblank")
        if self.usage_kind not in {"expense", "income", "both"}:
            raise ValueError("repair category usage_kind is invalid")


@dataclass(frozen=True, slots=True)
class RepairMisclassifiedExpense:
    book_id: UUID
    command_id: UUID
    original_transaction_id: UUID
    reversal_transaction_id: UUID
    replacement_transaction_id: UUID
    wrong_expense_account_id: UUID
    category_id: UUID
    operation: str = field(
        default="history_repair.misclassified_expense",
        init=False,
    )

    def __post_init__(self) -> None:
        identifiers = (
            self.book_id,
            self.command_id,
            self.original_transaction_id,
            self.reversal_transaction_id,
            self.replacement_transaction_id,
            self.wrong_expense_account_id,
            self.category_id,
        )
        if any(type(value) is not UUID for value in identifiers):
            raise IdempotencyValidationError("repair identifiers must be UUIDs")
        transaction_ids = {
            self.original_transaction_id,
            self.reversal_transaction_id,
            self.replacement_transaction_id,
        }
        if len(transaction_ids) != 3:
            raise IdempotencyValidationError(
                "repair transaction identifiers must be distinct"
            )

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "category_id": str(self.category_id),
            "original_transaction_id": str(self.original_transaction_id),
            "replacement_transaction_id": str(self.replacement_transaction_id),
            "reversal_transaction_id": str(self.reversal_transaction_id),
            "wrong_expense_account_id": str(self.wrong_expense_account_id),
        }


UnitOfWorkFactory = Callable[[], UnitOfWork]


def repair_command_id(book_id: UUID, original_transaction_id: UUID) -> UUID:
    return _repair_id(book_id, original_transaction_id, "command")


def reversal_transaction_id(book_id: UUID, original_transaction_id: UUID) -> UUID:
    return _repair_id(book_id, original_transaction_id, "reversal")


def replacement_transaction_id(
    book_id: UUID,
    original_transaction_id: UUID,
) -> UUID:
    return _repair_id(book_id, original_transaction_id, "replacement")


def canonical_expense_clearing_account_id(
    book_id: UUID,
    asset_code: str,
) -> UUID:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if (
        type(asset_code) is not str
        or not asset_code
        or asset_code != asset_code.upper()
    ):
        raise ValueError("asset_code is invalid")
    return canonical_internal_account_id(
        book_id,
        asset_code,
        AccountSystemRole.EXPENSE_CLEARING,
    )


def canonical_internal_account_id(
    book_id: UUID,
    asset_code: str,
    role: AccountSystemRole,
) -> UUID:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if (
        type(asset_code) is not str
        or not asset_code
        or asset_code != asset_code.upper()
    ):
        raise ValueError("asset_code is invalid")
    if role not in _INTERNAL_ACCOUNT_SPECS:
        raise ValueError("unsupported canonical internal account role")
    return uuid5(
        _SYSTEM_ACCOUNT_NAMESPACE,
        f"{book_id}:{asset_code}:{role.value}",
    )


def repair_category(
    book_id: UUID,
    path: tuple[str, ...],
) -> RepairCategory:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if (
        type(path) is not tuple
        or not path
        or any(type(part) is not str or not part.strip() for part in path)
    ):
        raise ValueError("category path must contain nonblank text")
    normalized = tuple(part.strip() for part in path)
    identity = "/".join(normalized)
    category_id = uuid5(_REPAIR_NAMESPACE, f"{book_id}:category:{identity}")
    return RepairCategory(
        category_id=category_id,
        category_version_id=uuid5(
            _REPAIR_NAMESPACE,
            f"{book_id}:category-version:{identity}:1",
        ),
        name=normalized[-1],
        parent_category_id=(
            None
            if len(normalized) == 1
            else repair_category(book_id, normalized[:-1]).category_id
        ),
    )


def ensure_repair_categories(
    *,
    book_id: UUID,
    categories: tuple[RepairCategory, ...],
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
) -> tuple[UUID, ...]:
    if type(categories) is not tuple or any(
        type(category) is not RepairCategory for category in categories
    ):
        raise ValueError("categories must be typed and immutable")
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, book_id)
        available = set(
            uow.session.scalars(
                select(CategoryRecord.category_id).where(
                    CategoryRecord.book_id == book_id
                )
            )
        )
        for definition in categories:
            if (
                definition.parent_category_id is not None
                and definition.parent_category_id not in available
            ):
                raise ExpenseHistoryRepairError(
                    "repair category parent must already exist or precede its child"
                )
            existing = uow.session.get(
                CategoryRecord,
                (book_id, definition.category_id),
            )
            if existing is None:
                existing = CategoryRecord(
                    book_id=book_id,
                    category_id=definition.category_id,
                    parent_category_id=definition.parent_category_id,
                    current_name=definition.name.strip(),
                    current_version_id=None,
                    status="active",
                )
                uow.session.add(existing)
                uow.session.flush()
                uow.session.add(
                    CategoryVersionRecord(
                        book_id=book_id,
                        category_id=definition.category_id,
                        category_version_id=definition.category_version_id,
                        parent_category_id=definition.parent_category_id,
                        name=definition.name.strip(),
                        status="active",
                        usage_kind=definition.usage_kind,
                        change_reason_code="history_repair_catalog",
                    )
                )
                uow.session.flush()
                existing.current_version_id = definition.category_version_id
                uow.session.flush()
            version = uow.session.get(
                CategoryVersionRecord,
                (
                    book_id,
                    definition.category_id,
                    definition.category_version_id,
                ),
            )
            if (
                existing.parent_category_id != definition.parent_category_id
                or existing.current_name != definition.name.strip()
                or existing.current_version_id != definition.category_version_id
                or existing.status != "active"
                or version is None
                or version.parent_category_id != definition.parent_category_id
                or version.name != definition.name.strip()
                or version.status != "active"
                or version.usage_kind != definition.usage_kind
                or version.change_reason_code != "history_repair_catalog"
            ):
                raise ExpenseHistoryRepairError(
                    "repair category conflicts with existing catalog"
                )
            available.add(definition.category_id)
    return tuple(category.category_id for category in categories)


def ensure_expense_clearing_account(
    *,
    book_id: UUID,
    asset_code: str,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
) -> UUID:
    return ensure_internal_accounts(
        book_id=book_id,
        asset_codes=(asset_code,),
        roles=(AccountSystemRole.EXPENSE_CLEARING,),
        actor=actor,
        uow_factory=uow_factory,
    )[0]


def ensure_internal_accounts(
    *,
    book_id: UUID,
    asset_codes: tuple[str, ...],
    roles: tuple[AccountSystemRole, ...],
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
) -> tuple[UUID, ...]:
    if (
        type(asset_codes) is not tuple
        or not asset_codes
        or any(
            type(asset_code) is not str
            or not asset_code
            or asset_code != asset_code.upper()
            for asset_code in asset_codes
        )
        or len(set(asset_codes)) != len(asset_codes)
    ):
        raise ValueError("asset_codes must be unique uppercase identifiers")
    if (
        type(roles) is not tuple
        or not roles
        or any(role not in _INTERNAL_ACCOUNT_SPECS for role in roles)
        or len(set(roles)) != len(roles)
    ):
        raise ValueError("roles must be unique supported internal roles")
    account_ids: list[UUID] = []
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, book_id)
        for asset_code in asset_codes:
            for role in roles:
                account_type, account_subtype = _INTERNAL_ACCOUNT_SPECS[role]
                account_id = canonical_internal_account_id(
                    book_id,
                    asset_code,
                    role,
                )
                expected = {
                    "account_id": account_id,
                    "asset_code": asset_code,
                    "account_type": account_type,
                    "account_subtype": account_subtype,
                    "system_role": role.value,
                    "current_name": (
                        f"Everyday {role.value.replace('_', ' ')} {asset_code}"
                    ),
                    "status": "active",
                }
                existing = uow.session.scalar(
                    select(AccountRecord).where(
                        AccountRecord.book_id == book_id,
                        AccountRecord.system_role == role.value,
                        AccountRecord.asset_code == asset_code,
                    )
                )
                if existing is None:
                    by_id = uow.session.get(
                        AccountRecord,
                        (book_id, account_id),
                    )
                    if by_id is not None:
                        existing = by_id
                    else:
                        existing = AccountRecord(book_id=book_id, **expected)
                        uow.session.add(existing)
                        uow.session.flush()
                actual = {
                    key: getattr(existing, key)
                    for key in expected
                }
                if actual != expected:
                    raise ExpenseHistoryRepairError(
                        "canonical internal account conflicts with existing catalog"
                    )
                account_ids.append(account_id)
    return tuple(account_ids)


def execute_misclassified_expense_repair(
    command: RepairMisclassifiedExpense,
    *,
    actor: CommandActor,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter | None = None,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(command) is not RepairMisclassifiedExpense:
        raise IdempotencyValidationError(
            "command must be a RepairMisclassifiedExpense"
        )
    committer = ledger_committer or LedgerCommitter()

    def handler(
        received: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if received is not command:
            raise IdempotencyValidationError("unexpected history repair command")
        return _build_repair_plan(command, uow, locked_head, actor=actor)

    return execute_financial(
        command,
        raw_key=f"misclassified-expense:{command.original_transaction_id}",
        actor=actor,
        authorize=authorize_journal_write,
        handler=handler,
        uow_factory=uow_factory,
        ledger_committer=committer,
        max_attempts=max_attempts,
    )


def _build_repair_plan(
    command: RepairMisclassifiedExpense,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
) -> LedgerWritePlan:
    source = _load_reversal_source(
        uow,
        book_id=command.book_id,
        reverses_transaction_id=command.original_transaction_id,
        reversal_transaction_id=command.reversal_transaction_id,
    )
    if source.transaction.transaction_kind not in {
        "standard",
        "credit_card_charge",
    }:
        raise ExpenseHistoryRepairError(
            "only standard expenses and credit-card charges can use this repair"
        )
    if _has_reporting(uow, command):
        raise ExpenseHistoryRepairError(
            "source transaction already has reporting lines"
        )

    wrong_account = uow.session.scalar(
        select(AccountRecord).where(
            AccountRecord.book_id == command.book_id,
            AccountRecord.account_id == command.wrong_expense_account_id,
        )
    )
    if (
        wrong_account is None
        or wrong_account.account_type != "expense"
        or wrong_account.system_role is not None
    ):
        raise ExpenseHistoryRepairError(
            "repair target is not a standard expense account"
        )
    wrong_postings = tuple(
        posting
        for posting in source.postings
        if posting.account_id == command.wrong_expense_account_id
    )
    if (
        len(source.postings) != 2
        or len(wrong_postings) != 1
        or wrong_postings[0].side is not PostingSide.DEBIT
    ):
        raise ExpenseHistoryRepairError(
            "source transaction is not a two-leg expense against the target account"
        )
    wrong_posting = wrong_postings[0]
    source_posting = next(
        posting
        for posting in source.postings
        if posting.account_id != command.wrong_expense_account_id
    )
    if (
        source_posting.side is not PostingSide.CREDIT
        or source_posting.asset_code != wrong_posting.asset_code
        or source_posting.units != wrong_posting.units
        or wrong_account.asset_code != wrong_posting.asset_code
    ):
        raise ExpenseHistoryRepairError(
            "source expense postings do not have matching asset and units"
        )

    source_account = uow.session.scalar(
        select(AccountRecord).where(
            AccountRecord.book_id == command.book_id,
            AccountRecord.account_id == source_posting.account_id,
        )
    )
    if source_account is None or source_account.status != "active":
        raise ExpenseHistoryRepairError("source account is unavailable")
    _validate_source_kind(
        uow,
        command=command,
        transaction_kind=source.transaction.transaction_kind,
        source_account=source_account,
    )
    _validate_category(uow, command)

    reverse = ReverseTransactionCommand(
        book_id=command.book_id,
        command_id=command.command_id,
        reversal_transaction_id=command.reversal_transaction_id,
        reverses_transaction_id=command.original_transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=source.event.effective_at,
    )
    reversal_plan = _build_reverse_plan(
        reverse,
        uow,
        locked_head,
        actor=actor,
    )
    entry = ExpenseEntryInput(
        amount=MoneyInput(
            value=wrong_posting.units,
            denomination=MoneyDenomination.MINOR_UNIT,
            asset_code=wrong_posting.asset_code,
            source_text=f"history-repair:{command.original_transaction_id}",
        ),
        source_account=AccountRef(account_id=source_posting.account_id),
        category=CategoryRef(category_id=command.category_id),
        occurred_at=source.event.effective_at,
    )
    context = load_compilation_context(
        uow.session,
        book_id=command.book_id,
        command_id=command.command_id,
        transaction_id=command.replacement_transaction_id,
        actor_subject_id=actor.subject_id,
        entry=entry,
        locked_last_position=locked_head.last_position,
    )
    replacement_plan = compile_entry(entry, context=context)
    if source.transaction.description_ref is not None:
        replacement_plan = _attach_narrative(
            replacement_plan,
            source.transaction.description_ref,
        )
    replacement_plan = _attach_external_references(
        replacement_plan,
        _external_references(uow, command),
    )
    replacement_financial = replacement_plan.events[0].payload
    if (
        source.transaction.transaction_kind == "credit_card_charge"
        and type(replacement_financial) is not CreditCardTransactionRecorded
    ) or (
        source.transaction.transaction_kind == "standard"
        and type(replacement_financial) is not JournalTransactionPosted
    ):
        raise ExpenseHistoryRepairError(
            "replacement transaction kind does not match its source"
        )

    reversal_event = reversal_plan.events[0]
    replacement_events = (
        replace(
            replacement_plan.events[0],
            causation_event_id=reversal_event.event_id,
        ),
        *replacement_plan.events[1:],
    )
    expected = dict(reversal_plan.expected_stream_versions)
    for stream, version in replacement_plan.expected_stream_versions.items():
        if stream in expected:
            raise ExpenseHistoryRepairError("repair stream identities overlap")
        expected[stream] = version
    events = (reversal_event, *replacement_events)
    return LedgerWritePlan(
        expected_stream_versions=expected,
        events=events,
        response_schema_version=1,
        status_code=201,
        body={
            "category_id": str(command.category_id),
            "event_count": len(events),
            "original_transaction_id": str(command.original_transaction_id),
            "replacement_transaction_id": str(command.replacement_transaction_id),
            "reversal_transaction_id": str(command.reversal_transaction_id),
            "wrong_expense_account_id": str(command.wrong_expense_account_id),
        },
    )


def _validate_source_kind(
    uow: UnitOfWork,
    *,
    command: RepairMisclassifiedExpense,
    transaction_kind: str,
    source_account: AccountRecord,
) -> None:
    card_record = uow.session.get(
        CreditCardTransactionRecord,
        (command.book_id, command.original_transaction_id),
    )
    if transaction_kind == "credit_card_charge":
        if (
            source_account.account_subtype != "credit_card"
            or card_record is None
            or card_record.intent != CreditCardIntent.CHARGE.value
            or card_record.card_account_id != source_account.account_id
            or card_record.counter_account_id
            != command.wrong_expense_account_id
        ):
            raise ExpenseHistoryRepairError(
                "credit-card expense metadata does not match its journal postings"
            )
        return
    if source_account.account_subtype == "credit_card" or card_record is not None:
        raise ExpenseHistoryRepairError(
            "standard expense has inconsistent credit-card metadata"
        )


def _validate_category(
    uow: UnitOfWork,
    command: RepairMisclassifiedExpense,
) -> None:
    category = uow.session.get(
        CategoryRecord,
        (command.book_id, command.category_id),
    )
    if (
        category is None
        or category.status != "active"
        or category.current_version_id is None
    ):
        raise ExpenseHistoryRepairError("repair category is unavailable")
    version = uow.session.get(
        CategoryVersionRecord,
        (
            command.book_id,
            command.category_id,
            category.current_version_id,
        ),
    )
    if (
        version is None
        or version.status != "active"
        or version.usage_kind not in {"expense", "both"}
    ):
        raise ExpenseHistoryRepairError(
            "repair category cannot classify an expense"
        )


def _has_reporting(
    uow: UnitOfWork,
    command: RepairMisclassifiedExpense,
) -> bool:
    reporting = uow.session.scalar(
        select(ReportingLineRecord.line_id)
        .where(
            ReportingLineRecord.book_id == command.book_id,
            ReportingLineRecord.transaction_id
            == command.original_transaction_id,
        )
        .limit(1)
    )
    return reporting is not None


def _external_references(
    uow: UnitOfWork,
    command: RepairMisclassifiedExpense,
) -> tuple[FinancialExternalReference, ...]:
    rows = tuple(
        uow.session.scalars(
            select(TransactionExternalReferenceRecord)
            .where(
                TransactionExternalReferenceRecord.book_id == command.book_id,
                TransactionExternalReferenceRecord.transaction_id
                == command.original_transaction_id,
            )
            .order_by(
                TransactionExternalReferenceRecord.provider_code,
                TransactionExternalReferenceRecord.reference_kind,
            )
        )
    )
    return tuple(
        FinancialExternalReference(
            provider_code=row.provider_code,
            kind=ExternalReferenceKind(row.reference_kind),
            reference=row.reference_value,
        )
        for row in rows
    )


def _attach_external_references(
    plan: LedgerWritePlan,
    references: tuple[FinancialExternalReference, ...],
) -> LedgerWritePlan:
    if not references:
        return plan
    first, *rest = plan.events
    if not hasattr(first.payload, "external_references"):
        raise ExpenseHistoryRepairError(
            "replacement transaction cannot preserve external references"
        )
    payload = first.payload.model_copy(update={"external_references": references})
    return replace(plan, events=(replace(first, payload=payload), *rest))


def _repair_id(
    book_id: UUID,
    original_transaction_id: UUID,
    kind: str,
) -> UUID:
    if type(book_id) is not UUID or type(original_transaction_id) is not UUID:
        raise ValueError("repair identifiers must be UUIDs")
    return uuid5(
        _REPAIR_NAMESPACE,
        f"{book_id}:{original_transaction_id}:{kind}",
    )


__all__ = [
    "ExpenseHistoryRepairError",
    "RepairCategory",
    "RepairMisclassifiedExpense",
    "canonical_expense_clearing_account_id",
    "canonical_internal_account_id",
    "ensure_expense_clearing_account",
    "ensure_internal_accounts",
    "ensure_repair_categories",
    "execute_misclassified_expense_repair",
    "repair_category",
    "repair_command_id",
    "replacement_transaction_id",
    "reversal_transaction_id",
]
