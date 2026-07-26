from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, NAMESPACE_URL, uuid5

from ...domain.credit_cards import CreditCardIntent, CreditCardTransactionRecorded
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
from ...domain.journal.events import JournalPostingFact, JournalTransactionPosted
from ...domain.reporting import ReportingDimension, ReportingLineKind
from ..event_batch import PendingEvent
from ..journal.assign_reporting_lines import (
    ReportingLineInput,
    build_reporting_lines_assigned,
    validate_reporting_allocations,
)
from ..ledger_committer import LedgerWritePlan
from .account_resolver import (
    AccountUse,
    EntryAccount,
    resolve_account,
    resolve_internal_account,
)
from .amounts import EntryAsset, NormalizedAmount, normalize_amount
from .category_resolver import (
    CategoryUsageKind,
    EntryCategory,
    resolve_category,
)
from .contracts import (
    AdjustmentEntryInput,
    CategoryAllocationInput,
    Clarification,
    ClarificationCode,
    CreditCardPaymentEntryInput,
    EverydayEntryInput,
    ExpenseEntryInput,
    IncomeEntryInput,
    RefundEntryInput,
    TransferEntryInput,
)
from .errors import (
    EntryClarificationRequired,
    EntryErrorCode,
    EntryGatewayError,
)
from .policies import normalize_category_allocations, require_distinct_accounts


_FINANCIAL_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/everyday-entry.financial",
)
_REPORTING_EVENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/everyday-entry.reporting",
)
_POSTING_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/everyday-entry.posting",
)
_LINE_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/events/everyday-entry.reporting-line",
)


@dataclass(frozen=True, slots=True)
class OriginalCategoryAllocation:
    category: EntryCategory
    units: int


@dataclass(frozen=True, slots=True)
class OriginalEntry:
    transaction_id: UUID
    kind: str
    asset_code: str
    units: int
    source_account_id: UUID | None = None
    card_account_id: UUID | None = None
    category_allocations: tuple[OriginalCategoryAllocation, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryCompilationContext:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    actor_subject_id: str
    locked_last_position: int
    assets: tuple[EntryAsset, ...]
    accounts: tuple[EntryAccount, ...]
    categories: tuple[EntryCategory, ...]
    current_balance_units: int | None = None
    original_entry: OriginalEntry | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedAllocation:
    category: EntryCategory
    units: int


def compile_entry(
    entry: EverydayEntryInput,
    *,
    context: EntryCompilationContext,
) -> LedgerWritePlan:
    asset_code = _entry_asset_code(entry, context=context)
    asset = _asset(context, asset_code)
    if isinstance(entry, ExpenseEntryInput):
        return _compile_expense(entry, context=context, asset=asset)
    if isinstance(entry, IncomeEntryInput):
        return _compile_income(entry, context=context, asset=asset)
    if isinstance(entry, TransferEntryInput):
        return _compile_transfer(entry, context=context, asset=asset)
    if isinstance(entry, CreditCardPaymentEntryInput):
        return _compile_card_payment(entry, context=context, asset=asset)
    if isinstance(entry, RefundEntryInput):
        return _compile_refund(entry, context=context, asset=asset)
    if isinstance(entry, AdjustmentEntryInput):
        return _compile_adjustment(entry, context=context, asset=asset)
    raise EntryGatewayError(
        EntryErrorCode.UNSUPPORTED,
        "entry kind is unsupported",
    )


def _compile_expense(
    entry: ExpenseEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    amount = normalize_amount(entry.amount, asset=asset)
    if entry.source_account is None:
        raise EntryGatewayError(
            EntryErrorCode.INVALID_INPUT,
            "expense payment instrument was not resolved",
            field="payment_instrument",
        )
    source = _account(
        entry.source_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.EXPENSE_SOURCE,
        field="source_account",
    )
    clearing = resolve_internal_account(
        accounts=context.accounts,
        book_id=context.book_id,
        asset_code=asset.asset_code,
        role=AccountSystemRole.EXPENSE_CLEARING,
    )
    allocations = _category_allocations(
        entry=entry,
        context=context,
        asset=asset,
        total=amount,
        usage_kind=CategoryUsageKind.EXPENSE,
    )
    postings = _postings(
        context,
        asset_code=asset.asset_code,
        units=amount.units,
        legs=(
            (clearing, PostingSide.DEBIT),
            (source, PostingSide.CREDIT),
        ),
    )
    if source.account_subtype == "credit_card":
        financial = CreditCardTransactionRecorded(
            intent=CreditCardIntent.CHARGE,
            transaction_id=context.transaction_id,
            card_account_id=source.account_id,
            counter_account_id=clearing.account_id,
            postings=postings,
        )
        transaction_kind = "credit_card_charge"
    else:
        financial = JournalTransactionPosted(
            transaction_id=context.transaction_id,
            kind=TransactionKind.STANDARD,
            postings=postings,
        )
        transaction_kind = TransactionKind.STANDARD.value
    return _plan(
        entry=entry,
        context=context,
        financial=financial,
        transaction_kind=transaction_kind,
        allocations=allocations,
        line_kind=ReportingLineKind.EXPENSE,
    )


def _compile_income(
    entry: IncomeEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    amount = normalize_amount(entry.amount, asset=asset)
    destination = _account(
        entry.destination_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.INCOME_DESTINATION,
        field="destination_account",
    )
    clearing = resolve_internal_account(
        accounts=context.accounts,
        book_id=context.book_id,
        asset_code=asset.asset_code,
        role=AccountSystemRole.INCOME_CLEARING,
    )
    allocations = _category_allocations(
        entry=entry,
        context=context,
        asset=asset,
        total=amount,
        usage_kind=CategoryUsageKind.INCOME,
    )
    postings = _postings(
        context,
        asset_code=asset.asset_code,
        units=amount.units,
        legs=(
            (destination, PostingSide.DEBIT),
            (clearing, PostingSide.CREDIT),
        ),
    )
    return _plan(
        entry=entry,
        context=context,
        financial=JournalTransactionPosted(
            transaction_id=context.transaction_id,
            kind=TransactionKind.STANDARD,
            postings=postings,
        ),
        transaction_kind=TransactionKind.STANDARD.value,
        allocations=allocations,
        line_kind=ReportingLineKind.INCOME,
    )


def _compile_transfer(
    entry: TransferEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    amount = normalize_amount(entry.amount, asset=asset)
    source = _account(
        entry.source_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.TRANSFER_SOURCE,
        field="source_account",
    )
    destination = _account(
        entry.destination_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.TRANSFER_DESTINATION,
        field="destination_account",
    )
    require_distinct_accounts(source.account_id, destination.account_id)
    postings = _postings(
        context,
        asset_code=asset.asset_code,
        units=amount.units,
        legs=(
            (destination, PostingSide.DEBIT),
            (source, PostingSide.CREDIT),
        ),
    )
    return _plan(
        entry=entry,
        context=context,
        financial=JournalTransactionPosted(
            transaction_id=context.transaction_id,
            kind=TransactionKind.TRANSFER,
            postings=postings,
        ),
        transaction_kind=TransactionKind.TRANSFER.value,
    )


def _compile_card_payment(
    entry: CreditCardPaymentEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    amount = normalize_amount(entry.amount, asset=asset)
    if entry.card_account is None:
        raise EntryGatewayError(
            EntryErrorCode.INVALID_INPUT,
            "credit-card payment instrument was not resolved",
            field="payment_instrument",
        )
    funding = _account(
        entry.funding_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.CARD_PAYMENT_FUNDING,
        field="funding_account",
    )
    card = _account(
        entry.card_account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.CARD_PAYMENT_CARD,
        field="card_account",
    )
    require_distinct_accounts(funding.account_id, card.account_id)
    postings = _postings(
        context,
        asset_code=asset.asset_code,
        units=amount.units,
        legs=(
            (card, PostingSide.DEBIT),
            (funding, PostingSide.CREDIT),
        ),
    )
    return _plan(
        entry=entry,
        context=context,
        financial=CreditCardTransactionRecorded(
            intent=CreditCardIntent.PAYMENT,
            transaction_id=context.transaction_id,
            card_account_id=card.account_id,
            counter_account_id=funding.account_id,
            postings=postings,
        ),
        transaction_kind="credit_card_payment",
    )


def _compile_refund(
    entry: RefundEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    original = context.original_entry
    if (
        original is None
        or original.transaction_id != entry.original_transaction_id
    ):
        raise EntryGatewayError(
            EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
            "original transaction is unavailable for refund compilation",
            field="original_transaction_id",
        )
    if original.asset_code != asset.asset_code:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_INVALID,
            "refund asset must match the original transaction",
            field="amount.asset_code",
        )
    amount = (
        NormalizedAmount(original.units, asset.ledger_scale, asset.asset_code)
        if entry.amount is None
        else normalize_amount(entry.amount, asset=asset)
    )
    if amount.units > original.units:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_INVALID,
            "refund amount exceeds the original transaction amount",
            field="amount.value",
        )
    clearing = resolve_internal_account(
        accounts=context.accounts,
        book_id=context.book_id,
        asset_code=asset.asset_code,
        role=AccountSystemRole.EXPENSE_CLEARING,
    )
    allocations = _refund_allocations(
        entry,
        original=original,
        context=context,
        asset=asset,
        amount=amount,
    )

    if original.kind == "credit_card_charge":
        card = _account_by_id(
            original.card_account_id,
            context=context,
            asset_code=asset.asset_code,
            use=AccountUse.CARD_PAYMENT_CARD,
        )
        postings = _postings(
            context,
            asset_code=asset.asset_code,
            units=amount.units,
            legs=((card, PostingSide.DEBIT), (clearing, PostingSide.CREDIT)),
        )
        financial = CreditCardTransactionRecorded(
            intent=CreditCardIntent.REFUND,
            transaction_id=context.transaction_id,
            card_account_id=card.account_id,
            counter_account_id=clearing.account_id,
            original_transaction_id=original.transaction_id,
            postings=postings,
        )
        transaction_kind = "credit_card_refund"
    elif original.kind == "expense":
        source = _account_by_id(
            original.source_account_id,
            context=context,
            asset_code=asset.asset_code,
            use=AccountUse.EXPENSE_SOURCE,
        )
        postings = _postings(
            context,
            asset_code=asset.asset_code,
            units=amount.units,
            legs=((source, PostingSide.DEBIT), (clearing, PostingSide.CREDIT)),
        )
        financial = JournalTransactionPosted(
            transaction_id=context.transaction_id,
            kind=TransactionKind.REFUND,
            original_transaction_id=original.transaction_id,
            postings=postings,
        )
        transaction_kind = TransactionKind.REFUND.value
    else:
        raise EntryGatewayError(
            EntryErrorCode.UNSUPPORTED,
            "only expense and credit-card charge refunds are supported",
            field="original_transaction_id",
        )
    return _plan(
        entry=entry,
        context=context,
        financial=financial,
        transaction_kind=transaction_kind,
        allocations=allocations,
        line_kind=ReportingLineKind.EXPENSE,
    )


def _compile_adjustment(
    entry: AdjustmentEntryInput,
    *,
    context: EntryCompilationContext,
    asset: EntryAsset,
) -> LedgerWritePlan:
    actual = normalize_amount(entry.actual_balance, asset=asset, allow_zero=True)
    account = _account(
        entry.account,
        context=context,
        asset_code=asset.asset_code,
        use=AccountUse.ADJUSTED,
        field="account",
    )
    if context.current_balance_units is None:
        raise EntryGatewayError(
            EntryErrorCode.UNSUPPORTED,
            "current balance is required to compile an adjustment",
            field="account",
        )
    delta = actual.units - context.current_balance_units
    if delta == 0:
        raise EntryGatewayError(
            EntryErrorCode.INVALID_INPUT,
            "actual balance already matches the ledger balance",
            field="actual_balance",
        )
    clearing = resolve_internal_account(
        accounts=context.accounts,
        book_id=context.book_id,
        asset_code=asset.asset_code,
        role=AccountSystemRole.BALANCE_ADJUSTMENT,
    )
    legs = (
        ((account, PostingSide.DEBIT), (clearing, PostingSide.CREDIT))
        if delta > 0
        else ((clearing, PostingSide.DEBIT), (account, PostingSide.CREDIT))
    )
    postings = _postings(
        context,
        asset_code=asset.asset_code,
        units=abs(delta),
        legs=legs,
    )
    return _plan(
        entry=entry,
        context=context,
        financial=JournalTransactionPosted(
            transaction_id=context.transaction_id,
            kind=TransactionKind.ADJUSTMENT,
            postings=postings,
        ),
        transaction_kind=TransactionKind.ADJUSTMENT.value,
    )


def _plan(
    *,
    entry: EverydayEntryInput,
    context: EntryCompilationContext,
    financial: JournalTransactionPosted | CreditCardTransactionRecorded,
    transaction_kind: str,
    allocations: tuple[_ResolvedAllocation, ...] = (),
    line_kind: ReportingLineKind | None = None,
) -> LedgerWritePlan:
    financial_event_id = uuid5(_FINANCIAL_EVENT_NAMESPACE, str(context.command_id))
    financial_pending = PendingEvent(
        event_id=financial_event_id,
        stream_type="journal_transaction",
        stream_id=context.transaction_id,
        payload=financial,
        command_id=context.command_id,
        actor_subject_id=context.actor_subject_id,
        correlation_id=context.command_id,
        causation_event_id=None,
        effective_at=entry.occurred_at,
    )
    events = [financial_pending]
    expected = {("journal_transaction", context.transaction_id): 0}
    if allocations:
        assert line_kind is not None
        lines = tuple(
            ReportingLineInput(
                line_id=uuid5(
                    _LINE_NAMESPACE,
                    f"{context.command_id}:{position}:line",
                ),
                line_version_id=uuid5(
                    _LINE_NAMESPACE,
                    f"{context.command_id}:{position}:version",
                ),
                catalog_id=allocation.category.category_version_id,
                asset_code=_financial_asset(financial),
                units=str(allocation.units),
                line_kind=line_kind,
                dimension=ReportingDimension.CATEGORY,
                dimension_id=allocation.category.category_id,
            )
            for position, allocation in enumerate(allocations)
        )
        validate_reporting_allocations(
            lines=lines,
            postings=financial.postings,
            transaction_kind=transaction_kind,
        )
        reporting = build_reporting_lines_assigned(
            transaction_id=context.transaction_id,
            classification_revision=1,
            lines=lines,
        )
        events.append(
            PendingEvent(
                event_id=uuid5(
                    _REPORTING_EVENT_NAMESPACE,
                    str(context.command_id),
                ),
                stream_type="reporting_lines",
                stream_id=context.transaction_id,
                payload=reporting,
                command_id=context.command_id,
                actor_subject_id=context.actor_subject_id,
                correlation_id=context.command_id,
                causation_event_id=financial_event_id,
                effective_at=entry.occurred_at,
            )
        )
        expected[("reporting_lines", context.transaction_id)] = 0
    return LedgerWritePlan(
        expected_stream_versions=expected,
        events=tuple(events),
        response_schema_version=1,
        status_code=201,
        body={
            "transaction_id": str(context.transaction_id),
            "entry_kind": entry.kind,
            "as_of_book_position": context.locked_last_position + len(events),
        },
    )


def _postings(
    context: EntryCompilationContext,
    *,
    asset_code: str,
    units: int,
    legs: tuple[tuple[EntryAccount, PostingSide], tuple[EntryAccount, PostingSide]],
) -> tuple[JournalPostingFact, ...]:
    drafts = tuple(
        PostingDraft(
            posting_id=str(
                uuid5(
                    _POSTING_NAMESPACE,
                    f"{context.transaction_id}:{position}",
                )
            ),
            position=position,
            account_id=str(account.account_id),
            asset_code=asset_code,
            side=side,
            units=units,
        )
        for position, (account, side) in enumerate(legs)
    )
    JournalValidator.validate(
        PostTransaction(
            transaction_id=str(context.transaction_id),
            book_id=str(context.book_id),
            kind=TransactionKind.STANDARD,
            postings=drafts,
        ),
        catalog=AccountCatalogSnapshot(
            accounts=tuple(_domain_account(account) for account, _ in legs)
        ),
    )
    return tuple(
        JournalPostingFact(
            posting_id=UUID(draft.posting_id),
            position=draft.position,
            account_id=UUID(draft.account_id),
            asset_code=draft.asset_code,
            side=draft.side,
            units=str(draft.units),
        )
        for draft in drafts
    )


def _domain_account(account: EntryAccount) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=str(account.account_id),
        book_id=str(account.book_id),
        asset_code=account.asset_code,
        account_type=account.account_type,
        account_subtype=account.account_subtype,
        system_role=account.system_role,
        status=account.status,
    )


def _category_allocations(
    *,
    entry: ExpenseEntryInput | IncomeEntryInput,
    context: EntryCompilationContext,
    asset: EntryAsset,
    total: NormalizedAmount,
    usage_kind: CategoryUsageKind,
) -> tuple[_ResolvedAllocation, ...]:
    normalized = normalize_category_allocations(
        amount=entry.amount,
        category_allocations=entry.category_allocations,
        asset=asset,
    )
    raw_allocations = (
        ((entry.category, total.units),)
        if entry.category is not None
        else tuple((item.input.category, item.amount.units) for item in normalized)
    )
    return tuple(
        _ResolvedAllocation(
            category=_category(
                reference,
                context=context,
                usage_kind=usage_kind,
                field=(
                    "category"
                    if entry.category is not None
                    else f"category_allocations.{position}.category"
                ),
            ),
            units=units,
        )
        for position, (reference, units) in enumerate(raw_allocations)
    )


def _refund_allocations(
    entry: RefundEntryInput,
    *,
    original: OriginalEntry,
    context: EntryCompilationContext,
    asset: EntryAsset,
    amount: NormalizedAmount,
) -> tuple[_ResolvedAllocation, ...]:
    if entry.category_allocations:
        assert entry.amount is not None
        normalized = normalize_category_allocations(
            amount=entry.amount,
            category_allocations=entry.category_allocations,
            asset=asset,
        )
        return tuple(
            _ResolvedAllocation(
                category=_category(
                    item.input.category,
                    context=context,
                    usage_kind=CategoryUsageKind.EXPENSE,
                    field=f"category_allocations.{position}.category",
                ),
                units=item.amount.units,
            )
            for position, item in enumerate(normalized)
        )
    if len(original.category_allocations) == 1:
        return (
            _ResolvedAllocation(
                category=original.category_allocations[0].category,
                units=amount.units,
            ),
        )
    if amount.units == original.units and original.category_allocations:
        return tuple(
            _ResolvedAllocation(category=item.category, units=item.units)
            for item in original.category_allocations
        )
    raise EntryGatewayError(
        EntryErrorCode.REFUND_ALLOCATION_REQUIRED,
        "a partial refund of a split transaction requires explicit allocations",
        field="category_allocations",
    )


def _account(
    reference,
    *,
    context: EntryCompilationContext,
    asset_code: str,
    use: AccountUse,
    field: str,
) -> EntryAccount:
    resolution = resolve_account(
        reference,
        accounts=context.accounts,
        book_id=context.book_id,
        asset_code=asset_code,
        use=use,
        category_ids=frozenset(
            category.category_id for category in context.categories
        ),
    )
    if resolution.account is None:
        raise EntryClarificationRequired(
            EntryErrorCode.ACCOUNT_AMBIGUOUS,
            "account selection requires clarification",
            clarifications=(
                Clarification(
                    code=ClarificationCode.ACCOUNT_SELECTION,
                    field=field,
                    prompt="Choose the intended account.",
                    choices=resolution.choices,
                ),
            ),
        )
    return resolution.account


def _account_by_id(
    account_id: UUID | None,
    *,
    context: EntryCompilationContext,
    asset_code: str,
    use: AccountUse,
) -> EntryAccount:
    if account_id is None:
        raise EntryGatewayError(
            EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
            "original transaction account is unavailable",
            field="original_transaction_id",
        )
    from .contracts import AccountRef

    return _account(
        AccountRef(account_id=account_id),
        context=context,
        asset_code=asset_code,
        use=use,
        field="original_transaction_id",
    )


def _category(
    reference,
    *,
    context: EntryCompilationContext,
    usage_kind: CategoryUsageKind,
    field: str,
) -> EntryCategory:
    resolution = resolve_category(
        reference,
        categories=context.categories,
        book_id=context.book_id,
        usage_kind=usage_kind,
        account_ids=frozenset(account.account_id for account in context.accounts),
    )
    if resolution.category is None:
        raise EntryClarificationRequired(
            EntryErrorCode.CATEGORY_AMBIGUOUS,
            "category selection requires clarification",
            clarifications=(
                Clarification(
                    code=ClarificationCode.CATEGORY_SELECTION,
                    field=field,
                    prompt="Choose the intended category.",
                    choices=resolution.choices,
                ),
            ),
        )
    return resolution.category


def _asset(context: EntryCompilationContext, asset_code: str) -> EntryAsset:
    matches = tuple(
        asset for asset in context.assets if asset.asset_code == asset_code
    )
    if len(matches) != 1:
        raise EntryGatewayError(
            EntryErrorCode.DENOMINATION_UNSUPPORTED,
            "exactly one asset policy is required",
            field="amount.asset_code",
        )
    return matches[0]


def _entry_asset_code(
    entry: EverydayEntryInput,
    *,
    context: EntryCompilationContext,
) -> str:
    if isinstance(entry, AdjustmentEntryInput):
        return entry.actual_balance.asset_code
    if isinstance(entry, RefundEntryInput) and entry.amount is None:
        if context.original_entry is None:
            raise EntryGatewayError(
                EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
                "original transaction is unavailable for refund compilation",
                field="original_transaction_id",
            )
        return context.original_entry.asset_code
    assert entry.amount is not None
    return entry.amount.asset_code


def _financial_asset(
    event: JournalTransactionPosted | CreditCardTransactionRecorded,
) -> str:
    return event.postings[0].asset_code


__all__ = [
    "EntryCompilationContext",
    "OriginalCategoryAllocation",
    "OriginalEntry",
    "compile_entry",
]
