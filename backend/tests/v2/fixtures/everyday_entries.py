from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text

from track_anywhere.application.entries.account_resolver import EntryAccount
from track_anywhere.application.entries.amounts import EntryAsset
from track_anywhere.application.entries.category_resolver import (
    CategoryUsageKind,
    EntryCategory,
)
from track_anywhere.application.entries.compiler import (
    EntryCompilationContext,
    OriginalCategoryAllocation,
    OriginalEntry,
)
from track_anywhere.application.entries.contracts import (
    AccountRef,
    CategoryAllocationInput,
    CategoryRef,
    CreditCardPaymentEntryInput,
    EntryNarrativeInput,
    EverydayEntryInput,
    ExpenseEntryInput,
    ExternalReferenceInput,
    ExternalReferenceKind,
    MoneyDenomination,
    MoneyInput,
    RefundEntryInput,
)
from track_anywhere.domain.journal import AccountSystemRole, AccountType


BOOK_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("10000000-0000-4000-8000-000000000002")
TRANSACTION_ID = UUID("10000000-0000-4000-8000-000000000003")
WALLET_ID = UUID("10000000-0000-4000-8000-000000000010")
ICBC_DEBIT_ID = UUID("10000000-0000-4000-8000-000000000011")
ICBC_CARD_ID = UUID("10000000-0000-4000-8000-000000000012")
BOC_DEBIT_ID = UUID("10000000-0000-4000-8000-000000000013")
EXPENSE_CLEARING_ID = UUID("10000000-0000-4000-8000-000000000014")
INCOME_CLEARING_ID = UUID("10000000-0000-4000-8000-000000000015")
BALANCE_ADJUSTMENT_ID = UUID("10000000-0000-4000-8000-000000000016")

FOOD_ID = UUID("10000000-0000-4000-8000-000000000020")
TAKEAWAY_ID = UUID("10000000-0000-4000-8000-000000000021")
DRINK_ID = UUID("10000000-0000-4000-8000-000000000022")
HOUSEHOLD_ID = UUID("10000000-0000-4000-8000-000000000023")
FOOD_VERSION_ID = UUID("10000000-0000-4000-8000-000000000030")
TAKEAWAY_VERSION_ID = UUID("10000000-0000-4000-8000-000000000031")
DRINK_VERSION_ID = UUID("10000000-0000-4000-8000-000000000032")
HOUSEHOLD_VERSION_ID = UUID("10000000-0000-4000-8000-000000000033")

ORIGINAL_EXPENSE_ID = UUID("10000000-0000-4000-8000-000000000040")
ORIGINAL_CARD_CHARGE_ID = UUID("10000000-0000-4000-8000-000000000041")
OCCURRED_AT = datetime(2026, 7, 24, 12, 30, tzinfo=UTC)
ACTOR_ID = "human:everyday-golden"


@dataclass(frozen=True, slots=True)
class GoldenEntryScenario:
    name: str
    utterance: str
    entry: EverydayEntryInput
    expected_units: int
    expected_value: str
    expected_financial_kind: Literal[
        "standard",
        "credit_card_charge",
        "credit_card_payment",
    ]
    expected_postings: tuple[tuple[UUID, str], tuple[UUID, str]]
    expected_categories: tuple[UUID, ...]
    expected_reporting_units: tuple[int, ...]
    cli_argv: tuple[str, ...]
    mcp_tool: str


def money(
    value: str,
    *,
    denomination: MoneyDenomination = MoneyDenomination.ASSET_UNIT,
    source_text: str | None = None,
) -> MoneyInput:
    return MoneyInput(
        value=value,
        denomination=denomination,
        asset_code="CNY",
        source_text=value if source_text is None else source_text,
    )


def golden_asset() -> EntryAsset:
    return EntryAsset(
        asset_code="CNY",
        kind="fiat",
        ledger_scale=2,
        input_scale=2,
        minor_unit_scale=2,
    )


def golden_accounts() -> tuple[EntryAccount, ...]:
    return (
        EntryAccount(
            account_id=WALLET_ID,
            book_id=BOOK_ID,
            display_name="微信零钱通",
            asset_code="CNY",
            account_type=AccountType.ASSET,
            account_subtype="wallet",
        ),
        EntryAccount(
            account_id=ICBC_DEBIT_ID,
            book_id=BOOK_ID,
            display_name="工商银行 6184",
            asset_code="CNY",
            account_type=AccountType.ASSET,
            account_subtype="debit_card",
            last4="6184",
        ),
        EntryAccount(
            account_id=ICBC_CARD_ID,
            book_id=BOOK_ID,
            display_name="工商银行 1242",
            asset_code="CNY",
            account_type=AccountType.LIABILITY,
            account_subtype="credit_card",
            last4="1242",
        ),
        EntryAccount(
            account_id=BOC_DEBIT_ID,
            book_id=BOOK_ID,
            display_name="中国银行",
            asset_code="CNY",
            account_type=AccountType.ASSET,
            account_subtype="debit_card",
            last4="2950",
        ),
        EntryAccount(
            account_id=EXPENSE_CLEARING_ID,
            book_id=BOOK_ID,
            display_name="Expense clearing CNY",
            asset_code="CNY",
            account_type=AccountType.EXPENSE,
            system_role=AccountSystemRole.EXPENSE_CLEARING,
        ),
        EntryAccount(
            account_id=INCOME_CLEARING_ID,
            book_id=BOOK_ID,
            display_name="Income clearing CNY",
            asset_code="CNY",
            account_type=AccountType.INCOME,
            system_role=AccountSystemRole.INCOME_CLEARING,
        ),
        EntryAccount(
            account_id=BALANCE_ADJUSTMENT_ID,
            book_id=BOOK_ID,
            display_name="Balance adjustment CNY",
            asset_code="CNY",
            account_type=AccountType.EQUITY,
            system_role=AccountSystemRole.BALANCE_ADJUSTMENT,
        ),
    )


def golden_categories() -> tuple[EntryCategory, ...]:
    return (
        EntryCategory(
            category_id=FOOD_ID,
            category_version_id=FOOD_VERSION_ID,
            book_id=BOOK_ID,
            path=("食品",),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
        EntryCategory(
            category_id=TAKEAWAY_ID,
            category_version_id=TAKEAWAY_VERSION_ID,
            book_id=BOOK_ID,
            path=("食品", "外卖"),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
        EntryCategory(
            category_id=DRINK_ID,
            category_version_id=DRINK_VERSION_ID,
            book_id=BOOK_ID,
            path=("食品", "饮料"),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
        EntryCategory(
            category_id=HOUSEHOLD_ID,
            category_version_id=HOUSEHOLD_VERSION_ID,
            book_id=BOOK_ID,
            path=("日用",),
            usage_kind=CategoryUsageKind.EXPENSE,
        ),
    )


def golden_context(
    *,
    transaction_id: UUID = TRANSACTION_ID,
    command_id: UUID = COMMAND_ID,
    original_entry: OriginalEntry | None = None,
) -> EntryCompilationContext:
    return EntryCompilationContext(
        book_id=BOOK_ID,
        command_id=command_id,
        transaction_id=transaction_id,
        actor_subject_id=ACTOR_ID,
        locked_last_position=0,
        assets=(golden_asset(),),
        accounts=golden_accounts(),
        categories=golden_categories(),
        original_entry=original_entry,
    )


def seed_golden_book(pg_engine) -> None:
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values ('CNY', 'fiat', 2, 2, 2, 'Chinese Yuan', 'active')
                on conflict (asset_code) do nothing
                """
            )
        )
        connection.execute(
            text(
                """
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, 'Everyday golden', 'CNY', 'active')
                """
            ),
            {"book_id": BOOK_ID},
        )
        connection.execute(
            text(
                """
                insert into book_event_heads (book_id, last_position, last_hash)
                values (:book_id, 0, :zero_hash)
                """
            ),
            {"book_id": BOOK_ID, "zero_hash": bytes(32)},
        )
        connection.execute(
            text(
                """
                insert into users (
                    user_id, subject_type, current_display_name, status
                ) values (:actor_id, 'human', 'Everyday Golden', 'active')
                """
            ),
            {"actor_id": ACTOR_ID},
        )
        connection.execute(
            text(
                """
                insert into book_members (
                    book_id, user_id, role, status, scopes
                ) values (
                    :book_id, :actor_id, 'owner', 'active',
                    '["ledger:read","ledger:write"]'::jsonb
                )
                """
            ),
            {"book_id": BOOK_ID, "actor_id": ACTOR_ID},
        )
        for account in golden_accounts():
            connection.execute(
                text(
                    """
                    insert into accounts (
                        book_id, account_id, asset_code, account_type,
                        account_subtype, system_role, current_name, status
                    ) values (
                        :book_id, :account_id, :asset_code, :account_type,
                        :account_subtype, :system_role, :current_name, 'active'
                    )
                    """
                ),
                {
                    "book_id": BOOK_ID,
                    "account_id": account.account_id,
                    "asset_code": account.asset_code,
                    "account_type": account.account_type.value,
                    "account_subtype": account.account_subtype,
                    "system_role": (
                        None
                        if account.system_role is AccountSystemRole.STANDARD
                        else account.system_role.value
                    ),
                    "current_name": account.display_name,
                },
            )
        for category in golden_categories():
            parent_id = (
                FOOD_ID
                if category.category_id in {TAKEAWAY_ID, DRINK_ID}
                else None
            )
            connection.execute(
                text(
                    """
                    insert into categories (
                        book_id, category_id, parent_category_id, current_name,
                        current_version_id, status
                    ) values (
                        :book_id, :category_id, :parent_id, :name, null, 'active'
                    )
                    """
                ),
                {
                    "book_id": BOOK_ID,
                    "category_id": category.category_id,
                    "parent_id": parent_id,
                    "name": category.path[-1],
                },
            )
        for category in golden_categories():
            parent_id = (
                FOOD_ID
                if category.category_id in {TAKEAWAY_ID, DRINK_ID}
                else None
            )
            connection.execute(
                text(
                    """
                    insert into category_versions (
                        book_id, category_id, category_version_id,
                        parent_category_id, name, status, usage_kind,
                        change_reason_code
                    ) values (
                        :book_id, :category_id, :version_id, :parent_id,
                        :name, 'active', 'expense', 'created'
                    )
                    """
                ),
                {
                    "book_id": BOOK_ID,
                    "category_id": category.category_id,
                    "version_id": category.category_version_id,
                    "parent_id": parent_id,
                    "name": category.path[-1],
                },
            )
            connection.execute(
                text(
                    """
                    update categories
                    set current_version_id = :version_id
                    where book_id = :book_id and category_id = :category_id
                    """
                ),
                {
                    "book_id": BOOK_ID,
                    "category_id": category.category_id,
                    "version_id": category.category_version_id,
                },
            )


def golden_scenarios() -> tuple[GoldenEntryScenario, ...]:
    return (
        GoldenEntryScenario(
            name="takeaway_wallet_53",
            utterance="外卖 微信零钱通 53",
            entry=ExpenseEntryInput(
                amount=money("53"),
                source_account=AccountRef(query="微信零钱通"),
                category=CategoryRef(path=("食品", "外卖")),
                occurred_at=OCCURRED_AT,
                narrative=EntryNarrativeInput(
                    merchant="外卖",
                    external_reference=ExternalReferenceInput(
                        provider_code="meituan",
                        kind=ExternalReferenceKind.PROVIDER_ORDER,
                        reference="golden-order-53",
                    ),
                ),
            ),
            expected_units=5_300,
            expected_value="53.00",
            expected_financial_kind="standard",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (WALLET_ID, "credit"),
            ),
            expected_categories=(TAKEAWAY_ID,),
            expected_reporting_units=(5_300,),
            cli_argv=(
                "expense",
                "53",
                "--from",
                "微信零钱通",
                "--category",
                "食品/外卖",
                "--merchant",
                "外卖",
                "--external-reference",
                "meituan:provider_order:golden-order-53",
            ),
            mcp_tool="ledger_prepare_expense",
        ),
        GoldenEntryScenario(
            name="water_wallet_660_asset",
            utterance="买水 微信零钱通 660",
            entry=ExpenseEntryInput(
                amount=money("660"),
                source_account=AccountRef(query="微信零钱通"),
                category=CategoryRef(path=("食品", "饮料")),
                occurred_at=OCCURRED_AT,
            ),
            expected_units=66_000,
            expected_value="660.00",
            expected_financial_kind="standard",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (WALLET_ID, "credit"),
            ),
            expected_categories=(DRINK_ID,),
            expected_reporting_units=(66_000,),
            cli_argv=(
                "expense",
                "660",
                "--from",
                "微信零钱通",
                "--category",
                "食品/饮料",
            ),
            mcp_tool="ledger_prepare_expense",
        ),
        GoldenEntryScenario(
            name="water_wallet_660_minor",
            utterance="买水 微信零钱通 660分",
            entry=ExpenseEntryInput(
                amount=money(
                    "660",
                    denomination=MoneyDenomination.MINOR_UNIT,
                    source_text="660分",
                ),
                source_account=AccountRef(query="微信零钱通"),
                category=CategoryRef(path=("食品", "饮料")),
                occurred_at=OCCURRED_AT,
            ),
            expected_units=660,
            expected_value="6.60",
            expected_financial_kind="standard",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (WALLET_ID, "credit"),
            ),
            expected_categories=(DRINK_ID,),
            expected_reporting_units=(660,),
            cli_argv=(
                "expense",
                "660",
                "--denomination",
                "minor_unit",
                "--source-text",
                "660分",
                "--from",
                "微信零钱通",
                "--category",
                "食品/饮料",
            ),
            mcp_tool="ledger_prepare_expense",
        ),
        GoldenEntryScenario(
            name="credit_card_charge_19_60",
            utterance="美团外卖 19.60 工行信用卡 1242",
            entry=ExpenseEntryInput(
                amount=money("19.60"),
                source_account=AccountRef(
                    query="工商银行",
                    subtype="credit_card",
                ),
                category=CategoryRef(path=("食品", "外卖")),
                occurred_at=OCCURRED_AT,
                narrative=EntryNarrativeInput(merchant="美团外卖"),
            ),
            expected_units=1_960,
            expected_value="19.60",
            expected_financial_kind="credit_card_charge",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (ICBC_CARD_ID, "credit"),
            ),
            expected_categories=(TAKEAWAY_ID,),
            expected_reporting_units=(1_960,),
            cli_argv=(
                "expense",
                "19.60",
                "--from",
                "工商银行",
                "--from-subtype",
                "credit_card",
                "--category",
                "食品/外卖",
                "--merchant",
                "美团外卖",
            ),
            mcp_tool="ledger_prepare_expense",
        ),
        GoldenEntryScenario(
            name="ordinary_bank_4_05",
            utterance="钱大妈 4.05 中国银行 2950",
            entry=ExpenseEntryInput(
                amount=money("4.05"),
                source_account=AccountRef(
                    query="中国银行",
                    subtype="debit_card",
                ),
                category=CategoryRef(query="日用"),
                occurred_at=OCCURRED_AT,
                narrative=EntryNarrativeInput(merchant="钱大妈"),
            ),
            expected_units=405,
            expected_value="4.05",
            expected_financial_kind="standard",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (BOC_DEBIT_ID, "credit"),
            ),
            expected_categories=(HOUSEHOLD_ID,),
            expected_reporting_units=(405,),
            cli_argv=(
                "expense",
                "4.05",
                "--from",
                "中国银行",
                "--from-subtype",
                "debit_card",
                "--category",
                "日用",
                "--merchant",
                "钱大妈",
            ),
            mcp_tool="ledger_prepare_expense",
        ),
        GoldenEntryScenario(
            name="credit_card_payment_2000",
            utterance="工行储蓄卡还工行信用卡 2000",
            entry=CreditCardPaymentEntryInput(
                amount=money("2000"),
                funding_account=AccountRef(
                    query="工商银行",
                    subtype="debit_card",
                ),
                card_account=AccountRef(
                    query="工商银行",
                    subtype="credit_card",
                ),
                occurred_at=OCCURRED_AT,
            ),
            expected_units=200_000,
            expected_value="2000.00",
            expected_financial_kind="credit_card_payment",
            expected_postings=(
                (ICBC_CARD_ID, "debit"),
                (ICBC_DEBIT_ID, "credit"),
            ),
            expected_categories=(),
            expected_reporting_units=(),
            cli_argv=(
                "card-pay",
                "2000",
                "--from",
                "工商银行",
                "--from-subtype",
                "debit_card",
                "--card",
                "工商银行",
                "--card-subtype",
                "credit_card",
            ),
            mcp_tool="ledger_prepare_credit_card_payment",
        ),
        GoldenEntryScenario(
            name="split_100",
            utterance="一单分到外卖60、饮料40，共100",
            entry=ExpenseEntryInput(
                amount=money("100"),
                source_account=AccountRef(query="微信零钱通"),
                category_allocations=(
                    CategoryAllocationInput(
                        category=CategoryRef(path=("食品", "外卖")),
                        amount=money("60"),
                    ),
                    CategoryAllocationInput(
                        category=CategoryRef(path=("食品", "饮料")),
                        amount=money("40"),
                    ),
                ),
                occurred_at=OCCURRED_AT,
            ),
            expected_units=10_000,
            expected_value="100.00",
            expected_financial_kind="standard",
            expected_postings=(
                (EXPENSE_CLEARING_ID, "debit"),
                (WALLET_ID, "credit"),
            ),
            expected_categories=(TAKEAWAY_ID, DRINK_ID),
            expected_reporting_units=(6_000, 4_000),
            cli_argv=(),
            mcp_tool="ledger_prepare_expense",
        ),
    )


def original_expense() -> OriginalEntry:
    takeaway = next(
        item for item in golden_categories() if item.category_id == TAKEAWAY_ID
    )
    return OriginalEntry(
        transaction_id=ORIGINAL_EXPENSE_ID,
        kind="expense",
        asset_code="CNY",
        units=5_300,
        source_account_id=WALLET_ID,
        category_allocations=(
            OriginalCategoryAllocation(category=takeaway, units=5_300),
        ),
    )


def original_card_charge() -> OriginalEntry:
    takeaway = next(
        item for item in golden_categories() if item.category_id == TAKEAWAY_ID
    )
    return OriginalEntry(
        transaction_id=ORIGINAL_CARD_CHARGE_ID,
        kind="credit_card_charge",
        asset_code="CNY",
        units=1_960,
        card_account_id=ICBC_CARD_ID,
        category_allocations=(
            OriginalCategoryAllocation(category=takeaway, units=1_960),
        ),
    )


def refund_entry(
    *,
    original_transaction_id: UUID = ORIGINAL_EXPENSE_ID,
    amount: MoneyInput | None = None,
) -> RefundEntryInput:
    return RefundEntryInput(
        original_transaction_id=original_transaction_id,
        amount=amount,
        occurred_at=OCCURRED_AT,
    )


def with_context_original(
    original: OriginalEntry,
    *,
    transaction_id: UUID,
    command_id: UUID,
) -> EntryCompilationContext:
    return replace(
        golden_context(),
        transaction_id=transaction_id,
        command_id=command_id,
        original_entry=original,
    )


__all__ = [
    "ACTOR_ID",
    "BALANCE_ADJUSTMENT_ID",
    "BOC_DEBIT_ID",
    "BOOK_ID",
    "COMMAND_ID",
    "DRINK_ID",
    "DRINK_VERSION_ID",
    "EXPENSE_CLEARING_ID",
    "FOOD_ID",
    "FOOD_VERSION_ID",
    "GoldenEntryScenario",
    "HOUSEHOLD_ID",
    "HOUSEHOLD_VERSION_ID",
    "ICBC_CARD_ID",
    "ICBC_DEBIT_ID",
    "INCOME_CLEARING_ID",
    "OCCURRED_AT",
    "ORIGINAL_CARD_CHARGE_ID",
    "ORIGINAL_EXPENSE_ID",
    "TAKEAWAY_ID",
    "TAKEAWAY_VERSION_ID",
    "TRANSACTION_ID",
    "WALLET_ID",
    "golden_accounts",
    "golden_asset",
    "golden_categories",
    "golden_context",
    "golden_scenarios",
    "money",
    "original_card_charge",
    "original_expense",
    "refund_entry",
    "seed_golden_book",
    "with_context_original",
]
