from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from track_anywhere.application.entries.account_resolver import (
    AccountUse,
    EntryAccount,
    derive_account_last4,
    resolve_account,
)
from track_anywhere.application.entries.amounts import EntryAsset, normalize_amount
from track_anywhere.application.entries.category_resolver import (
    CategoryUsageKind,
    EntryCategory,
    resolve_category,
)
from track_anywhere.application.entries.compiler import (
    EntryCompilationContext,
    OriginalCategoryAllocation,
    OriginalEntry,
    compile_entry,
)
from track_anywhere.application.entries.prepare import preview_and_resolved
from track_anywhere.application.entries.duplicate_detector import (
    DuplicateCandidate,
    DuplicateEvidenceKind,
    decide_duplicate,
)
from track_anywhere.application.entries.contracts import (
    AccountRef,
    AdjustmentEntryInput,
    BalanceInput,
    CategoryAllocationInput,
    CategoryRef,
    CreditCardPaymentEntryInput,
    ExpenseEntryInput,
    IncomeEntryInput,
    MoneyDenomination,
    MoneyInput,
    PreparedEntryStatus,
    RefundEntryInput,
    TransferEntryInput,
)
from track_anywhere.application.entries.errors import (
    EntryClarificationRequired,
    EntryErrorCode,
    EntryGatewayError,
)
from track_anywhere.application.journal.assign_reporting_lines import (
    ReportingLineInput,
    build_reporting_lines_assigned,
    validate_reporting_allocations,
)
from track_anywhere.domain.credit_cards import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from track_anywhere.domain.journal import AccountSystemRole, AccountType, PostingSide
from track_anywhere.domain.journal.events import JournalTransactionPosted
from track_anywhere.domain.journal.models import TransactionKind
from track_anywhere.domain.reporting import (
    ReportingDimension,
    ReportingLineKind,
    ReportingLinesAssigned,
)


BOOK_ID = UUID("00000000-0000-0000-0000-000000000001")
COMMAND_ID = UUID("00000000-0000-0000-0000-000000000002")
TRANSACTION_ID = UUID("00000000-0000-0000-0000-000000000003")
WALLET_ID = UUID("00000000-0000-0000-0000-000000000010")
CARD_ID = UUID("00000000-0000-0000-0000-000000000011")
EXPENSE_CLEARING_ID = UUID("00000000-0000-0000-0000-000000000012")
INCOME_CLEARING_ID = UUID("00000000-0000-0000-0000-000000000013")
ADJUSTMENT_ID = UUID("00000000-0000-0000-0000-000000000014")
BANK_ID = UUID("00000000-0000-0000-0000-000000000015")
USD_CARD_ID = UUID("00000000-0000-0000-0000-000000000016")
CNY_FX_ID = UUID("00000000-0000-0000-0000-000000000017")
USD_FX_ID = UUID("00000000-0000-0000-0000-000000000018")
FOOD_ID = UUID("00000000-0000-0000-0000-000000000020")
DRINK_ID = UUID("00000000-0000-0000-0000-000000000021")
FOOD_VERSION_ID = UUID("00000000-0000-0000-0000-000000000030")
DRINK_VERSION_ID = UUID("00000000-0000-0000-0000-000000000031")
SALARY_ID = UUID("00000000-0000-0000-0000-000000000022")
SALARY_VERSION_ID = UUID("00000000-0000-0000-0000-000000000032")
OCCURRED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)


def _asset() -> EntryAsset:
    return EntryAsset(
        asset_code="CNY",
        kind="fiat",
        ledger_scale=2,
        input_scale=2,
        minor_unit_scale=2,
    )


def _account(
    account_id: UUID,
    display_name: str,
    account_type: AccountType,
    *,
    subtype: str | None = None,
    role: AccountSystemRole = AccountSystemRole.STANDARD,
    last4: str | None = None,
    asset_code: str = "CNY",
) -> EntryAccount:
    return EntryAccount(
        account_id=account_id,
        book_id=BOOK_ID,
        display_name=display_name,
        asset_code=asset_code,
        account_type=account_type,
        account_subtype=subtype,
        system_role=role,
        last4=last4,
    )


def _accounts() -> tuple[EntryAccount, ...]:
    return (
        _account(WALLET_ID, "微信零钱通", AccountType.ASSET),
        _account(BANK_ID, "工商银行储蓄卡", AccountType.ASSET, last4="6184"),
        _account(
            CARD_ID,
            "工商银行信用卡",
            AccountType.LIABILITY,
            subtype="credit_card",
            last4="1242",
        ),
        _account(
            EXPENSE_CLEARING_ID,
            "Expense clearing CNY",
            AccountType.EXPENSE,
            role=AccountSystemRole.EXPENSE_CLEARING,
        ),
        _account(
            INCOME_CLEARING_ID,
            "Income clearing CNY",
            AccountType.INCOME,
            role=AccountSystemRole.INCOME_CLEARING,
        ),
        _account(
            ADJUSTMENT_ID,
            "Balance adjustment CNY",
            AccountType.EQUITY,
            role=AccountSystemRole.BALANCE_ADJUSTMENT,
        ),
    )


def _categories() -> tuple[EntryCategory, ...]:
    return (
        EntryCategory(
            category_id=FOOD_ID,
            category_version_id=FOOD_VERSION_ID,
            book_id=BOOK_ID,
            path=("食品", "外卖"),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
        EntryCategory(
            category_id=SALARY_ID,
            category_version_id=SALARY_VERSION_ID,
            book_id=BOOK_ID,
            path=("收入", "工资"),
            usage_kind=CategoryUsageKind.INCOME,
        ),
        EntryCategory(
            category_id=DRINK_ID,
            category_version_id=DRINK_VERSION_ID,
            book_id=BOOK_ID,
            path=("食品", "饮料"),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
    )


def _context(
    *,
    accounts: tuple[EntryAccount, ...] | None = None,
) -> EntryCompilationContext:
    return EntryCompilationContext(
        book_id=BOOK_ID,
        command_id=COMMAND_ID,
        transaction_id=TRANSACTION_ID,
        actor_subject_id="subject-1",
        locked_last_position=40,
        assets=(_asset(),),
        accounts=accounts or _accounts(),
        categories=_categories(),
    )


def _money(
    value: str,
    *,
    denomination: MoneyDenomination = MoneyDenomination.ASSET_UNIT,
    source_text: str | None = None,
) -> MoneyInput:
    return MoneyInput(
        value=value,
        denomination=denomination,
        asset_code="CNY",
        source_text=source_text or value,
    )


def test_normalizes_660_asset_units_without_float_or_guessing() -> None:
    amount = normalize_amount(_money("660"), asset=_asset())

    assert amount.units == 66_000
    assert amount.asset_unit_value() == "660"


def test_normalizes_660_minor_units_as_six_yuan_sixty() -> None:
    amount = normalize_amount(
        _money(
            "660",
            denomination=MoneyDenomination.MINOR_UNIT,
            source_text="660分",
        ),
        asset=_asset(),
    )

    assert amount.units == 660
    assert amount.asset_unit_value() == "6.6"


def test_rejects_explicit_source_denomination_contradiction_without_correction() -> None:
    with pytest.raises(EntryGatewayError) as raised:
        normalize_amount(
            _money(
                "660",
                denomination=MoneyDenomination.ASSET_UNIT,
                source_text="660分",
            ),
            asset=_asset(),
        )

    assert raised.value.code is EntryErrorCode.AMOUNT_SOURCE_MISMATCH


def test_account_query_ambiguity_returns_structured_choices() -> None:
    duplicate = _account(
        UUID("00000000-0000-0000-0000-000000000016"),
        "微信零钱通",
        AccountType.ASSET,
    )

    resolution = resolve_account(
        AccountRef(query=" 微信零钱通 "),
        accounts=_accounts() + (duplicate,),
        book_id=BOOK_ID,
        asset_code="CNY",
        use=AccountUse.EXPENSE_SOURCE,
    )

    assert resolution.account is None
    assert tuple(choice.resolved_id for choice in resolution.choices) == tuple(
        sorted((WALLET_ID, duplicate.account_id), key=str)
    )


@pytest.mark.parametrize(
    ("current_name", "expected"),
    (
        ("工商银行 6184", "6184"),
        ("  工商银行信用卡 (1242)  ", "1242"),
        ("Card6184", "6184"),
        ("6184", "6184"),
        ("工商银行 12345", None),
        ("工商银行 １1234", None),
        ("工商银行 １２３４", None),
        ("工商银行 ١٢٣٤", None),
        ("工商银行 ( 1242 )", None),
        ("工商银行 (1242) extra", None),
        ("工商银行", None),
    ),
)
def test_derives_only_an_independent_terminal_ascii_last4(
    current_name: str,
    expected: str | None,
) -> None:
    assert derive_account_last4(current_name) == expected


def test_account_base_name_query_applies_last4_and_subtype_filters() -> None:
    savings_id = UUID("00000000-0000-0000-0000-000000000041")
    other_savings_id = UUID("00000000-0000-0000-0000-000000000042")
    same_last4_card_id = UUID("00000000-0000-0000-0000-000000000043")
    named_card_id = UUID("00000000-0000-0000-0000-000000000044")
    accounts = (
        _account(
            savings_id,
            "工商银行 6184",
            AccountType.ASSET,
            subtype="debit_card",
            last4="6184",
        ),
        _account(
            other_savings_id,
            "工商银行 (9988)",
            AccountType.ASSET,
            subtype="debit_card",
            last4="9988",
        ),
        _account(
            same_last4_card_id,
            "工商银行 (6184)",
            AccountType.LIABILITY,
            subtype="credit_card",
            last4="6184",
        ),
        _account(
            named_card_id,
            "工商银行信用卡 1242",
            AccountType.LIABILITY,
            subtype="credit_card",
            last4="1242",
        ),
    )

    ambiguous = resolve_account(
        AccountRef(query="工商银行"),
        accounts=accounts,
        book_id=BOOK_ID,
        asset_code="CNY",
        use=AccountUse.EXPENSE_SOURCE,
    )
    assert ambiguous.account is None
    assert {choice.resolved_id for choice in ambiguous.choices} == {
        savings_id,
        other_savings_id,
        same_last4_card_id,
    }
    assert all("••••" not in choice.label for choice in ambiguous.choices)

    savings = resolve_account(
        AccountRef(
            query="工商银行",
            last4="6184",
            subtype="debit_card",
        ),
        accounts=accounts,
        book_id=BOOK_ID,
        asset_code="CNY",
        use=AccountUse.EXPENSE_SOURCE,
    )
    assert savings.account is not None
    assert savings.account.account_id == savings_id

    card = resolve_account(
        AccountRef(
            query="工商银行信用卡",
            last4="1242",
            subtype="credit_card",
        ),
        accounts=accounts,
        book_id=BOOK_ID,
        asset_code="CNY",
        use=AccountUse.EXPENSE_SOURCE,
    )
    assert card.account is not None
    assert card.account.account_id == named_card_id

    with pytest.raises(EntryGatewayError) as raised:
        resolve_account(
            AccountRef(query="工商银行", last4="0000"),
            accounts=accounts,
            book_id=BOOK_ID,
            asset_code="CNY",
            use=AccountUse.EXPENSE_SOURCE,
        )
    assert raised.value.code is EntryErrorCode.ACCOUNT_NOT_FOUND

    direct = resolve_account(
        AccountRef(account_id=named_card_id),
        accounts=accounts,
        book_id=BOOK_ID,
        asset_code="CNY",
        use=AccountUse.EXPENSE_SOURCE,
    )
    assert direct.account is not None
    assert direct.account.account_id == named_card_id


def test_compiler_preserves_structured_account_clarification() -> None:
    duplicate = _account(
        UUID("00000000-0000-0000-0000-000000000016"),
        "微信零钱通",
        AccountType.ASSET,
    )
    entry = ExpenseEntryInput(
        amount=_money("53"),
        source_account=AccountRef(query="微信零钱通"),
        category=CategoryRef(category_id=FOOD_ID),
        occurred_at=OCCURRED_AT,
    )

    with pytest.raises(EntryClarificationRequired) as raised:
        compile_entry(
            entry,
            context=_context(accounts=_accounts() + (duplicate,)),
        )

    assert raised.value.code is EntryErrorCode.ACCOUNT_AMBIGUOUS
    clarification = raised.value.clarifications[0]
    assert clarification.field == "source_account"
    assert len(clarification.choices) == 2


def test_rejects_category_id_in_account_reference() -> None:
    with pytest.raises(EntryGatewayError) as raised:
        resolve_account(
            AccountRef(account_id=FOOD_ID),
            accounts=_accounts(),
            book_id=BOOK_ID,
            asset_code="CNY",
            use=AccountUse.EXPENSE_SOURCE,
            category_ids=frozenset({FOOD_ID}),
        )

    assert raised.value.code is EntryErrorCode.ACCOUNT_INELIGIBLE


def test_rejects_account_id_in_category_reference() -> None:
    with pytest.raises(EntryGatewayError) as raised:
        resolve_category(
            CategoryRef(category_id=WALLET_ID),
            categories=_categories(),
            book_id=BOOK_ID,
            usage_kind=CategoryUsageKind.EXPENSE,
            account_ids=frozenset({WALLET_ID}),
        )

    assert raised.value.code is EntryErrorCode.CATEGORY_INELIGIBLE


def test_duplicate_decision_is_deterministic_and_never_silently_skips() -> None:
    soft_id = UUID("00000000-0000-0000-0000-000000000081")
    strong_id = UUID("00000000-0000-0000-0000-000000000082")

    decision = decide_duplicate(
        (
            DuplicateCandidate(
                transaction_id=soft_id,
                evidence_kind=DuplicateEvidenceKind.SOFT_MATCH,
                summary="same amount, account, and nearby time",
            ),
            DuplicateCandidate(
                transaction_id=strong_id,
                evidence_kind=DuplicateEvidenceKind.EXTERNAL_REFERENCE,
                summary="same provider order reference",
            ),
        )
    )

    assert decision.status is PreparedEntryStatus.DUPLICATE_SUSPECTED
    assert tuple(item.transaction_id for item in decision.candidates) == (
        strong_id,
        soft_id,
    )


def test_compiles_expense_and_classification_in_one_write_plan() -> None:
    entry = ExpenseEntryInput(
        amount=_money("660"),
        source_account=AccountRef(account_id=WALLET_ID),
        category=CategoryRef(path=("食品", "饮料")),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    assert len(plan.events) == 2
    assert plan.expected_stream_versions == {
        ("journal_transaction", TRANSACTION_ID): 0,
        ("reporting_lines", TRANSACTION_ID): 0,
    }
    financial = plan.events[0].payload
    reporting = plan.events[1].payload
    assert type(financial) is JournalTransactionPosted
    assert tuple(
        (posting.account_id, posting.side, posting.units)
        for posting in financial.postings
    ) == (
        (EXPENSE_CLEARING_ID, PostingSide.DEBIT, "66000"),
        (WALLET_ID, PostingSide.CREDIT, "66000"),
    )
    assert type(reporting) is ReportingLinesAssigned
    assert reporting.lines[0].dimension_id == DRINK_ID
    assert reporting.lines[0].catalog_id == DRINK_VERSION_ID
    assert reporting.lines[0].units == "66000"
    assert plan.events[1].causation_event_id == plan.events[0].event_id


def test_compiles_exact_category_allocations() -> None:
    entry = ExpenseEntryInput(
        amount=_money("100"),
        source_account=AccountRef(account_id=WALLET_ID),
        category_allocations=(
            CategoryAllocationInput(
                category=CategoryRef(category_id=FOOD_ID),
                amount=_money("60"),
            ),
            CategoryAllocationInput(
                category=CategoryRef(category_id=DRINK_ID),
                amount=_money("40"),
            ),
        ),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    reporting = plan.events[1].payload
    assert type(reporting) is ReportingLinesAssigned
    assert tuple((line.dimension_id, line.units) for line in reporting.lines) == (
        (FOOD_ID, "6000"),
        (DRINK_ID, "4000"),
    )


def test_rejects_category_allocations_that_do_not_equal_amount() -> None:
    entry = ExpenseEntryInput(
        amount=_money("100"),
        source_account=AccountRef(account_id=WALLET_ID),
        category_allocations=(
            CategoryAllocationInput(
                category=CategoryRef(category_id=FOOD_ID),
                amount=_money("99"),
            ),
        ),
        occurred_at=OCCURRED_AT,
    )

    with pytest.raises(EntryGatewayError) as raised:
        compile_entry(entry, context=_context())

    assert raised.value.code is EntryErrorCode.CATEGORY_ALLOCATION_MISMATCH


def test_credit_card_expense_reuses_typed_charge_semantics() -> None:
    entry = ExpenseEntryInput(
        amount=_money("19.60"),
        source_account=AccountRef(account_id=CARD_ID),
        category=CategoryRef(category_id=FOOD_ID),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    financial = plan.events[0].payload
    assert type(financial) is CreditCardTransactionRecorded
    assert financial.intent is CreditCardIntent.CHARGE
    assert financial.card_account_id == CARD_ID
    assert financial.counter_account_id == EXPENSE_CLEARING_ID
    assert len(plan.events) == 2


def test_card_payment_is_typed_and_has_no_reporting_event() -> None:
    entry = CreditCardPaymentEntryInput(
        amount=_money("2000"),
        funding_account=AccountRef(account_id=WALLET_ID),
        card_account=AccountRef(account_id=CARD_ID),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    assert len(plan.events) == 1
    financial = plan.events[0].payload
    assert type(financial) is CreditCardTransactionRecorded
    assert financial.intent is CreditCardIntent.PAYMENT
    assert tuple(
        (posting.account_id, posting.side, posting.units)
        for posting in financial.postings
    ) == (
        (CARD_ID, PostingSide.DEBIT, "200000"),
        (WALLET_ID, PostingSide.CREDIT, "200000"),
    )
    assert ("reporting_lines", TRANSACTION_ID) not in plan.expected_stream_versions


def test_fx_card_payment_preserves_both_amounts_and_only_reports_the_fee() -> None:
    usd = EntryAsset(
        asset_code="USD",
        kind="fiat",
        ledger_scale=2,
        input_scale=2,
        minor_unit_scale=2,
    )
    accounts = _accounts() + (
        _account(
            USD_CARD_ID,
            "广发 Visa",
            AccountType.LIABILITY,
            subtype="credit_card",
            asset_code="USD",
        ),
        _account(
            CNY_FX_ID,
            "FX trading CNY",
            AccountType.SYSTEM,
            role=AccountSystemRole.FX_TRADING,
        ),
        _account(
            USD_FX_ID,
            "FX trading USD",
            AccountType.SYSTEM,
            role=AccountSystemRole.FX_TRADING,
            asset_code="USD",
        ),
    )
    context = replace(_context(accounts=accounts), assets=(_asset(), usd))
    entry = CreditCardPaymentEntryInput(
        amount=MoneyInput(
            value="2.06",
            asset_code="USD",
            source_text="$2.06",
        ),
        source_amount=_money("13.88", source_text="¥13.88"),
        fee_amount=_money("0.10", source_text="¥0.10"),
        fee_category=CategoryRef(category_id=FOOD_ID),
        funding_account=AccountRef(account_id=WALLET_ID),
        card_account=AccountRef(account_id=USD_CARD_ID),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=context)
    preview, resolved = preview_and_resolved(entry, context=context, plan=plan)

    financial = plan.events[0].payload
    reporting = plan.events[1].payload
    assert type(financial) is JournalTransactionPosted
    assert financial.kind is TransactionKind.CREDIT_CARD_PAYMENT
    assert tuple(
        (posting.account_id, posting.asset_code, posting.side, posting.units)
        for posting in financial.postings
    ) == (
        (USD_CARD_ID, "USD", PostingSide.DEBIT, "206"),
        (USD_FX_ID, "USD", PostingSide.CREDIT, "206"),
        (CNY_FX_ID, "CNY", PostingSide.DEBIT, "1388"),
        (EXPENSE_CLEARING_ID, "CNY", PostingSide.DEBIT, "10"),
        (WALLET_ID, "CNY", PostingSide.CREDIT, "1398"),
    )
    assert type(reporting) is ReportingLinesAssigned
    assert tuple(
        (line.asset_code, line.units, line.dimension_id) for line in reporting.lines
    ) == (("CNY", "10", FOOD_ID),)
    assert preview.amount.value == "2.06"
    assert preview.source_amount is not None
    assert preview.source_amount.value == "13.88"
    assert preview.fee_amount is not None
    assert preview.fee_amount.value == "0.10"
    assert resolved.source_trading_account_id == CNY_FX_ID
    assert resolved.target_trading_account_id == USD_FX_ID


def test_reporting_builder_validates_proposed_postings_without_projection() -> None:
    entry = ExpenseEntryInput(
        amount=_money("53"),
        source_account=AccountRef(account_id=WALLET_ID),
        category=CategoryRef(category_id=FOOD_ID),
        occurred_at=OCCURRED_AT,
    )
    plan = compile_entry(entry, context=_context())
    financial = plan.events[0].payload
    reporting = plan.events[1].payload
    assert type(financial) is JournalTransactionPosted
    assert type(reporting) is ReportingLinesAssigned
    line = reporting.lines[0]
    inputs = (
        ReportingLineInput(
            line_id=line.line_id,
            line_version_id=line.line_version_id,
            catalog_id=line.catalog_id,
            asset_code=line.asset_code,
            units=line.units,
            line_kind=line.line_kind,
            dimension=ReportingDimension.CATEGORY,
            dimension_id=line.dimension_id,
        ),
    )

    validate_reporting_allocations(
        lines=inputs,
        postings=financial.postings,
        transaction_kind="standard",
    )
    rebuilt = build_reporting_lines_assigned(
        transaction_id=TRANSACTION_ID,
        classification_revision=1,
        lines=inputs,
    )

    assert rebuilt == reporting


def test_income_uses_income_clearing_and_income_reporting() -> None:
    entry = IncomeEntryInput(
        amount=_money("12000"),
        destination_account=AccountRef(account_id=BANK_ID),
        category=CategoryRef(category_id=SALARY_ID),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    financial = plan.events[0].payload
    reporting = plan.events[1].payload
    assert type(financial) is JournalTransactionPosted
    assert tuple(
        (posting.account_id, posting.side) for posting in financial.postings
    ) == (
        (BANK_ID, PostingSide.DEBIT),
        (INCOME_CLEARING_ID, PostingSide.CREDIT),
    )
    assert type(reporting) is ReportingLinesAssigned
    assert reporting.lines[0].line_kind is ReportingLineKind.INCOME
    assert reporting.lines[0].dimension_id == SALARY_ID


def test_transfer_has_no_classification() -> None:
    entry = TransferEntryInput(
        amount=_money("1000"),
        source_account=AccountRef(account_id=BANK_ID),
        destination_account=AccountRef(account_id=WALLET_ID),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=_context())

    assert len(plan.events) == 1
    financial = plan.events[0].payload
    assert type(financial) is JournalTransactionPosted
    assert financial.kind is TransactionKind.TRANSFER


def test_adjustment_compiles_only_the_balance_delta() -> None:
    entry = AdjustmentEntryInput(
        account=AccountRef(account_id=WALLET_ID),
        actual_balance=BalanceInput(
            value="1250.60",
            asset_code="CNY",
            source_text="1250.60",
        ),
        occurred_at=OCCURRED_AT,
    )
    context = replace(_context(), current_balance_units=120_000)

    plan = compile_entry(entry, context=context)

    assert len(plan.events) == 1
    financial = plan.events[0].payload
    assert type(financial) is JournalTransactionPosted
    assert financial.kind is TransactionKind.ADJUSTMENT
    assert tuple(posting.units for posting in financial.postings) == ("5060", "5060")


def test_non_card_refund_uses_explicit_refund_kind_and_expense_reporting() -> None:
    original = OriginalEntry(
        transaction_id=UUID("00000000-0000-0000-0000-000000000099"),
        kind="expense",
        asset_code="CNY",
        units=5_300,
        source_account_id=WALLET_ID,
        category_allocations=(
            OriginalCategoryAllocation(category=_categories()[0], units=5_300),
        ),
    )
    entry = RefundEntryInput(
        original_transaction_id=original.transaction_id,
        amount=_money("10"),
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=replace(_context(), original_entry=original))

    financial = plan.events[0].payload
    reporting = plan.events[1].payload
    assert type(financial) is JournalTransactionPosted
    assert financial.kind is TransactionKind.REFUND
    assert financial.original_transaction_id == original.transaction_id
    assert tuple(
        (posting.account_id, posting.side, posting.units)
        for posting in financial.postings
    ) == (
        (WALLET_ID, PostingSide.DEBIT, "1000"),
        (EXPENSE_CLEARING_ID, PostingSide.CREDIT, "1000"),
    )
    assert type(reporting) is ReportingLinesAssigned
    assert reporting.lines[0].units == "1000"


def test_credit_card_refund_reuses_typed_refund_relation() -> None:
    original = OriginalEntry(
        transaction_id=UUID("00000000-0000-0000-0000-000000000098"),
        kind="credit_card_charge",
        asset_code="CNY",
        units=1_960,
        card_account_id=CARD_ID,
        category_allocations=(
            OriginalCategoryAllocation(category=_categories()[0], units=1_960),
        ),
    )
    entry = RefundEntryInput(
        original_transaction_id=original.transaction_id,
        occurred_at=OCCURRED_AT,
    )

    plan = compile_entry(entry, context=replace(_context(), original_entry=original))

    financial = plan.events[0].payload
    assert type(financial) is CreditCardTransactionRecorded
    assert financial.intent is CreditCardIntent.REFUND
    assert financial.original_transaction_id == original.transaction_id
    assert len(plan.events) == 2
