from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from backend.tests.v2.fixtures.everyday_entries import (
    COMMAND_ID,
    ICBC_CARD_ID,
    ICBC_DEBIT_ID,
    TAKEAWAY_ID,
    TRANSACTION_ID,
    GoldenEntryScenario,
    golden_context,
    golden_scenarios,
    money,
    original_card_charge,
    original_expense,
    refund_entry,
)
from track_anywhere.application.entries.compiler import compile_entry
from track_anywhere.application.entries.contracts import (
    AccountRef,
    CategoryAllocationInput,
    CategoryRef,
    ExpenseEntryInput,
    PreparedEntryStatus,
)
from track_anywhere.application.entries.duplicate_detector import (
    DuplicateCandidate,
    DuplicateEvidenceKind,
    decide_duplicate,
)
from track_anywhere.application.entries.errors import (
    EntryClarificationRequired,
    EntryErrorCode,
    EntryGatewayError,
)
from track_anywhere.application.entries.prepare import preview_and_resolved
from track_anywhere.domain.credit_cards import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from track_anywhere.domain.journal.events import JournalTransactionPosted
from track_anywhere.domain.journal.models import TransactionKind
from track_anywhere.domain.reporting import ReportingLinesAssigned
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


@pytest.mark.parametrize(
    "scenario",
    golden_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_golden_inputs_compile_to_deterministic_canonical_plans(
    scenario: GoldenEntryScenario,
) -> None:
    context = golden_context()

    first = compile_entry(scenario.entry, context=context)
    second = compile_entry(scenario.entry, context=context)

    assert first == second
    assert tuple(event.stream_type for event in first.events) == (
        ("journal_transaction", "reporting_lines")
        if scenario.expected_categories
        else ("journal_transaction",)
    )
    assert first.expected_stream_versions == {
        (event.stream_type, TRANSACTION_ID): 0 for event in first.events
    }
    financial = first.events[0].payload
    assert isinstance(
        financial,
        (JournalTransactionPosted, CreditCardTransactionRecorded),
    )
    assert tuple(
        (posting.account_id, posting.side.value) for posting in financial.postings
    ) == scenario.expected_postings
    assert {int(posting.units) for posting in financial.postings} == {
        scenario.expected_units
    }
    if scenario.expected_financial_kind == "standard":
        assert type(financial) is JournalTransactionPosted
        assert financial.kind is TransactionKind.STANDARD
    else:
        assert type(financial) is CreditCardTransactionRecorded
        assert financial.intent.value == scenario.expected_financial_kind.removeprefix(
            "credit_card_"
        )

    for event in first.events:
        dumped = PRODUCTION_EVENT_REGISTRY.dump_registered(event.payload)
        assert (
            PRODUCTION_EVENT_REGISTRY.validate_stored(
                type(event.payload).event_type,
                type(event.payload).schema_version,
                dumped,
            )
            == event.payload
        )
    if scenario.expected_categories:
        reporting = first.events[1].payload
        assert type(reporting) is ReportingLinesAssigned
        assert tuple(
            line.dimension_id for line in reporting.lines
        ) == scenario.expected_categories
        assert tuple(int(line.units) for line in reporting.lines) == (
            scenario.expected_reporting_units
        )
        assert first.events[1].causation_event_id == first.events[0].event_id

    preview, resolved = preview_and_resolved(
        scenario.entry,
        context=context,
        plan=first,
    )
    assert preview.amount.value == scenario.expected_value
    assert resolved.category_ids == scenario.expected_categories
    assert preview.category_paths == tuple(
        category.path
        for category_id in scenario.expected_categories
        for category in context.categories
        if category.category_id == category_id
    )


@pytest.mark.parametrize(
    ("original", "amount_value", "expected_units", "expected_intent"),
    (
        (original_expense(), None, 5_300, None),
        (original_expense(), "10", 1_000, None),
        (
            original_card_charge(),
            None,
            1_960,
            CreditCardIntent.REFUND,
        ),
    ),
    ids=("full_wallet_refund", "partial_wallet_refund", "full_card_refund"),
)
def test_refund_golden_plans_link_original_and_keep_negative_reporting_semantics(
    original,
    amount_value: str | None,
    expected_units: int,
    expected_intent: CreditCardIntent | None,
) -> None:
    entry = refund_entry(
        original_transaction_id=original.transaction_id,
        amount=None if amount_value is None else money(amount_value),
    )
    context = replace(
        golden_context(),
        original_entry=original,
        transaction_id=UUID("10000000-0000-4000-8000-000000000050"),
        command_id=UUID("10000000-0000-4000-8000-000000000051"),
    )

    plan = compile_entry(entry, context=context)

    financial = plan.events[0].payload
    if expected_intent is None:
        assert type(financial) is JournalTransactionPosted
        assert financial.kind is TransactionKind.REFUND
    else:
        assert type(financial) is CreditCardTransactionRecorded
        assert financial.intent is expected_intent
    assert financial.original_transaction_id == original.transaction_id
    assert {int(posting.units) for posting in financial.postings} == {
        expected_units
    }
    reporting = plan.events[1].payload
    assert type(reporting) is ReportingLinesAssigned
    assert tuple(
        (line.dimension_id, int(line.units)) for line in reporting.lines
    ) == ((TAKEAWAY_ID, expected_units),)


def test_split_allocations_must_sum_exactly_without_rounding() -> None:
    entry = ExpenseEntryInput(
        amount=money("100"),
        source_account=AccountRef(query="微信零钱通"),
        category_allocations=(
            CategoryAllocationInput(
                category=CategoryRef(path=("食品", "外卖")),
                amount=money("60"),
            ),
            CategoryAllocationInput(
                category=CategoryRef(path=("食品", "饮料")),
                amount=money("39.99"),
            ),
        ),
        occurred_at=golden_scenarios()[0].entry.occurred_at,
    )

    with pytest.raises(EntryGatewayError) as raised:
        compile_entry(entry, context=golden_context())

    assert raised.value.code is EntryErrorCode.CATEGORY_ALLOCATION_MISMATCH


def test_same_name_debit_and_credit_cards_require_or_accept_exact_hints() -> None:
    base = golden_scenarios()[0].entry
    assert isinstance(base, ExpenseEntryInput)
    ambiguous = base.model_copy(
        update={"source_account": AccountRef(query="工商银行")}
    )

    with pytest.raises(EntryClarificationRequired) as raised:
        compile_entry(ambiguous, context=golden_context())

    assert raised.value.code is EntryErrorCode.ACCOUNT_AMBIGUOUS
    assert tuple(
        choice.resolved_id for choice in raised.value.clarifications[0].choices
    ) == (ICBC_DEBIT_ID, ICBC_CARD_ID)

    for reference, expected_id in (
        (AccountRef(query="工商银行", subtype="debit_card"), ICBC_DEBIT_ID),
        (
            AccountRef(query="工商银行", last4="1242", subtype="credit_card"),
            ICBC_CARD_ID,
        ),
    ):
        entry = base.model_copy(update={"source_account": reference})
        financial = compile_entry(entry, context=golden_context()).events[0].payload
        assert expected_id in {
            posting.account_id for posting in financial.postings
        }


def test_external_reference_and_soft_fingerprint_candidates_are_never_silent() -> None:
    external_id = UUID("10000000-0000-4000-8000-000000000060")
    soft_id = UUID("10000000-0000-4000-8000-000000000061")

    decision = decide_duplicate(
        (
            DuplicateCandidate(
                transaction_id=soft_id,
                evidence_kind=DuplicateEvidenceKind.SOURCE_FINGERPRINT,
                summary="same normalized source fingerprint",
            ),
            DuplicateCandidate(
                transaction_id=external_id,
                evidence_kind=DuplicateEvidenceKind.EXTERNAL_REFERENCE,
                summary="same provider order reference",
            ),
        )
    )

    assert decision.status is PreparedEntryStatus.DUPLICATE_SUSPECTED
    assert tuple(item.transaction_id for item in decision.candidates) == (
        external_id,
        soft_id,
    )
