from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from track_anywhere.queries.everyday_entries import (
    DecodedTransactionNarrative,
    EverydayEntryKind,
    EverydayEntryQueryService,
    FieldAvailability,
    NarrativeAccess,
    NarrativeStatus,
    _AccountFact,
    _AssetFact,
    _EntryFacts,
    _ReportingFact,
)
from track_anywhere.queries.journal import (
    CreditCardRelation,
    JournalItem,
    JournalPosting,
)


BOOK_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_BOOK_ID = UUID("00000000-0000-4000-8000-000000000002")
TRANSACTION_ID = UUID("00000000-0000-4000-8000-000000000010")
ORIGINAL_ID = UUID("00000000-0000-4000-8000-000000000011")
REVERSAL_ID = UUID("00000000-0000-4000-8000-000000000012")
SOURCE_ID = UUID("00000000-0000-4000-8000-000000000020")
TARGET_ID = UUID("00000000-0000-4000-8000-000000000021")
CARD_ID = UUID("00000000-0000-4000-8000-000000000022")
EXPENSE_CLEARING_ID = UUID("00000000-0000-4000-8000-000000000023")
INCOME_CLEARING_ID = UUID("00000000-0000-4000-8000-000000000024")
CATEGORY_ID = UUID("00000000-0000-4000-8000-000000000030")
CATEGORY_VERSION_ID = UUID("00000000-0000-4000-8000-000000000031")
SECOND_CATEGORY_ID = UUID("00000000-0000-4000-8000-000000000032")
SECOND_CATEGORY_VERSION_ID = UUID("00000000-0000-4000-8000-000000000033")
SIDECAR_ID = UUID("00000000-0000-4000-8000-000000000040")
WHEN = datetime(2026, 7, 24, 12, tzinfo=UTC)


ACCOUNTS = {
    SOURCE_ID: _AccountFact(SOURCE_ID, "asset", None, "微信零钱通"),
    TARGET_ID: _AccountFact(TARGET_ID, "asset", None, "工商银行 6184"),
    CARD_ID: _AccountFact(CARD_ID, "liability", None, "工商银行信用卡 1242"),
    EXPENSE_CLEARING_ID: _AccountFact(
        EXPENSE_CLEARING_ID,
        "expense",
        "expense_clearing",
        "Internal Expense Clearing CNY",
    ),
    INCOME_CLEARING_ID: _AccountFact(
        INCOME_CLEARING_ID,
        "income",
        "income_clearing",
        "Internal Income Clearing CNY",
    ),
}
ASSETS = {
    "CNY": _AssetFact("CNY", 2),
    "BTC": _AssetFact("BTC", 8),
}


class FakeSource:
    def __init__(self, *facts: _EntryFacts) -> None:
        self.facts = facts

    def get(
        self,
        book_id: UUID,
        transaction_id: UUID,
        *,
        as_of_book_position: int | None = None,
    ) -> _EntryFacts:
        del book_id, as_of_book_position
        return next(
            value
            for value in self.facts
            if value.journal.transaction_id == transaction_id
        )

    def list(
        self,
        book_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        as_of_book_position: int | None = None,
    ) -> tuple[tuple[_EntryFacts, ...], str | None, int]:
        del book_id, cursor, as_of_book_position
        return self.facts[:limit], None, 9


class FakeDecoder:
    def __init__(
        self,
        result: DecodedTransactionNarrative,
    ) -> None:
        self.result = result
        self.calls = 0

    def decode(
        self,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> dict[UUID, DecodedTransactionNarrative]:
        assert book_id == BOOK_ID
        assert sidecar_ids == (SIDECAR_ID,)
        self.calls += 1
        return {SIDECAR_ID: self.result}


def _posting(
    position: int,
    account_id: UUID,
    side: str,
    units: int,
    *,
    asset_code: str = "CNY",
) -> JournalPosting:
    return JournalPosting(
        posting_id=UUID(int=100 + position),
        position=position,
        account_id=account_id,
        asset_code=asset_code,
        side=side,
        units=units,
    )


def _journal(
    *,
    transaction_id: UUID = TRANSACTION_ID,
    transaction_kind: str = "standard",
    debit_account_id: UUID = EXPENSE_CLEARING_ID,
    credit_account_id: UUID = SOURCE_ID,
    units: int = 5300,
    asset_code: str = "CNY",
    relation: CreditCardRelation | None = None,
    reverses_transaction_id: UUID | None = None,
    reversed_by_transaction_id: UUID | None = None,
    description_ref: UUID | None = None,
) -> JournalItem:
    return JournalItem(
        transaction_id=transaction_id,
        effective_at=WHEN,
        book_position=7,
        transaction_kind=transaction_kind,
        postings=(
            _posting(0, debit_account_id, "debit", units, asset_code=asset_code),
            _posting(1, credit_account_id, "credit", units, asset_code=asset_code),
        ),
        reversed_by_transaction_id=reversed_by_transaction_id,
        reverses_transaction_id=reverses_transaction_id,
        credit_card_relation=relation,
        description_ref=description_ref,
    )


def _reporting(
    *,
    category_id: UUID = CATEGORY_ID,
    category_version_id: UUID = CATEGORY_VERSION_ID,
    path: tuple[str, ...] = ("食品", "外卖"),
    units: int = 5300,
    line_kind: str = "expense",
    asset_code: str = "CNY",
) -> _ReportingFact:
    return _ReportingFact(
        category_id=category_id,
        category_version_id=category_version_id,
        path=path,
        asset_code=asset_code,
        units=units,
        line_kind=line_kind,
    )


def _facts(
    journal: JournalItem,
    *,
    reporting: tuple[_ReportingFact, ...] = (),
    inherited_reporting: tuple[_ReportingFact, ...] = (),
    book_id: UUID = BOOK_ID,
) -> _EntryFacts:
    return _EntryFacts(
        book_id=book_id,
        journal=journal,
        accounts=ACCOUNTS,
        assets=ASSETS,
        reporting=reporting,
        inherited_reporting=inherited_reporting,
    )


def test_expense_and_income_use_semantic_accounts_and_exact_categories() -> None:
    expense = _facts(_journal(), reporting=(_reporting(),))
    income = _facts(
        _journal(
            transaction_id=ORIGINAL_ID,
            debit_account_id=TARGET_ID,
            credit_account_id=INCOME_CLEARING_ID,
            units=1200000,
        ),
        reporting=(
            _reporting(
                path=("工资", "heyrevia"),
                units=1200000,
                line_kind="income",
            ),
        ),
    )

    page = EverydayEntryQueryService(FakeSource(expense, income)).list(
        BOOK_ID,
        limit=10,
    )

    expense_view, income_view = page.items
    assert expense_view.kind is EverydayEntryKind.EXPENSE
    assert expense_view.amount is not None
    assert expense_view.amount.value == "53.00"
    assert expense_view.payment_account is not None
    assert expense_view.payment_account.display_name == "微信零钱通"
    assert expense_view.category_allocations[0].path == ("食品", "外卖")
    assert income_view.kind is EverydayEntryKind.INCOME
    assert income_view.amount is not None
    assert income_view.amount.value == "12000.00"
    assert income_view.target_account is not None
    assert income_view.target_account.display_name == "工商银行 6184"


def test_transfer_and_card_payment_have_no_category_and_keep_account_roles() -> None:
    transfer = _facts(
        _journal(
            transaction_kind="transfer",
            debit_account_id=TARGET_ID,
            credit_account_id=SOURCE_ID,
            units=100000,
        )
    )
    payment_relation = CreditCardRelation(
        intent="payment",
        card_account_id=CARD_ID,
        counter_account_id=SOURCE_ID,
        original_transaction_id=None,
    )
    payment = _facts(
        _journal(
            transaction_id=ORIGINAL_ID,
            transaction_kind="credit_card_payment",
            debit_account_id=CARD_ID,
            credit_account_id=SOURCE_ID,
            units=200000,
            relation=payment_relation,
        )
    )

    transfer_view, payment_view = EverydayEntryQueryService(
        FakeSource(transfer, payment)
    ).list(BOOK_ID, limit=10).items

    assert transfer_view.kind is EverydayEntryKind.TRANSFER
    assert transfer_view.source_account is not None
    assert transfer_view.source_account.account_id == SOURCE_ID
    assert transfer_view.target_account is not None
    assert transfer_view.target_account.account_id == TARGET_ID
    assert transfer_view.category_availability is FieldAvailability.NOT_APPLICABLE
    assert payment_view.kind is EverydayEntryKind.CREDIT_CARD_PAYMENT
    assert payment_view.source_account is not None
    assert payment_view.source_account.account_id == SOURCE_ID
    assert payment_view.target_account is not None
    assert payment_view.target_account.account_id == CARD_ID
    assert payment_view.category_availability is FieldAvailability.NOT_APPLICABLE


def test_refund_and_reversal_keep_relationships_and_inherit_safe_categories() -> None:
    refund_relation = CreditCardRelation(
        intent="refund",
        card_account_id=CARD_ID,
        counter_account_id=EXPENSE_CLEARING_ID,
        original_transaction_id=ORIGINAL_ID,
    )
    refund = _facts(
        _journal(
            transaction_kind="credit_card_refund",
            debit_account_id=CARD_ID,
            credit_account_id=EXPENSE_CLEARING_ID,
            units=1000,
            relation=refund_relation,
        ),
        inherited_reporting=(_reporting(units=5300),),
    )
    reversal = _facts(
        _journal(
            transaction_id=REVERSAL_ID,
            debit_account_id=SOURCE_ID,
            credit_account_id=EXPENSE_CLEARING_ID,
            reverses_transaction_id=ORIGINAL_ID,
        ),
        inherited_reporting=(_reporting(),),
    )

    refund_view, reversal_view = EverydayEntryQueryService(
        FakeSource(refund, reversal)
    ).list(BOOK_ID, limit=10).items

    assert refund_view.kind is EverydayEntryKind.REFUND
    assert refund_view.original_transaction_id == ORIGINAL_ID
    assert refund_view.relationship_availability is FieldAvailability.AVAILABLE
    assert refund_view.target_account is not None
    assert refund_view.target_account.account_id == CARD_ID
    assert refund_view.category_allocations[0].amount.value == "10.00"
    assert reversal_view.kind is EverydayEntryKind.REVERSAL
    assert reversal_view.reverses_transaction_id == ORIGINAL_ID
    assert reversal_view.original_transaction_id == ORIGINAL_ID
    assert reversal_view.relationship_availability is FieldAvailability.AVAILABLE
    assert reversal_view.category_allocations[0].amount.value == "53.00"


def test_non_card_refund_uses_event_sourced_link_or_marks_it_unavailable() -> None:
    refund = _facts(
        _journal(
            transaction_kind="refund",
            debit_account_id=SOURCE_ID,
            credit_account_id=EXPENSE_CLEARING_ID,
            units=1000,
        ),
        reporting=(_reporting(units=1000),),
    )
    linked_refund = replace(
        refund,
        semantic_original_transaction_id=ORIGINAL_ID,
    )

    view = EverydayEntryQueryService(FakeSource(linked_refund)).get(
        BOOK_ID,
        TRANSACTION_ID,
    )
    unavailable = EverydayEntryQueryService(FakeSource(refund)).get(
        BOOK_ID,
        TRANSACTION_ID,
    )

    assert view.kind is EverydayEntryKind.REFUND
    assert view.original_transaction_id == ORIGINAL_ID
    assert view.relationship_availability is FieldAvailability.AVAILABLE
    assert view.category_allocations[0].amount.value == "10.00"
    assert unavailable.original_transaction_id is None
    assert unavailable.relationship_availability is FieldAvailability.UNAVAILABLE


def test_partial_split_refund_is_explicitly_unavailable_instead_of_invented() -> None:
    relation = CreditCardRelation(
        intent="refund",
        card_account_id=CARD_ID,
        counter_account_id=EXPENSE_CLEARING_ID,
        original_transaction_id=ORIGINAL_ID,
    )
    inherited = (
        _reporting(units=3000),
        _reporting(
            category_id=SECOND_CATEGORY_ID,
            category_version_id=SECOND_CATEGORY_VERSION_ID,
            path=("食品", "饮料"),
            units=2300,
        ),
    )
    partial = _facts(
        _journal(
            transaction_kind="credit_card_refund",
            debit_account_id=CARD_ID,
            credit_account_id=EXPENSE_CLEARING_ID,
            units=1000,
            relation=relation,
        ),
        inherited_reporting=inherited,
    )

    view = EverydayEntryQueryService(FakeSource(partial)).get(
        BOOK_ID,
        TRANSACTION_ID,
    )

    assert view.category_allocations == ()
    assert view.category_availability is FieldAvailability.UNAVAILABLE


def test_split_category_and_high_precision_amounts_preserve_exact_scale() -> None:
    split = (
        _reporting(units=3000),
        _reporting(
            category_id=SECOND_CATEGORY_ID,
            category_version_id=SECOND_CATEGORY_VERSION_ID,
            path=("食品", "饮料"),
            units=2300,
        ),
    )
    expense = _facts(_journal(), reporting=split)
    precise = _facts(
        _journal(
            transaction_id=ORIGINAL_ID,
            units=1,
            asset_code="BTC",
        ),
        reporting=(_reporting(units=1, asset_code="BTC"),),
    )

    expense_view, precise_view = EverydayEntryQueryService(
        FakeSource(expense, precise)
    ).list(BOOK_ID, limit=10).items

    assert [
        allocation.amount.value for allocation in expense_view.category_allocations
    ] == ["30.00", "23.00"]
    assert precise_view.amount is not None
    assert precise_view.amount.value == "0.00000001"
    assert precise_view.amount.scale == 8
    assert precise_view.category_allocations[0].amount.value == "0.00000001"


def test_narrative_requires_explicit_authorization_and_a_decoder() -> None:
    facts = _facts(_journal(description_ref=SIDECAR_ID), reporting=(_reporting(),))
    decoded = DecodedTransactionNarrative(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        status=NarrativeStatus.AVAILABLE,
        merchant="美团外卖",
        channel="微信",
    )
    decoder = FakeDecoder(decoded)
    service = EverydayEntryQueryService(
        FakeSource(facts),
        narrative_decoder=decoder,
    )

    redacted = service.get(BOOK_ID, TRANSACTION_ID)
    available = service.get(
        BOOK_ID,
        TRANSACTION_ID,
        narrative_access=NarrativeAccess.OWNER_AUTHORIZED,
    )
    unavailable = EverydayEntryQueryService(FakeSource(facts)).get(
        BOOK_ID,
        TRANSACTION_ID,
        narrative_access=NarrativeAccess.OWNER_AUTHORIZED,
    )

    assert decoder.calls == 1
    assert redacted.narrative.status is NarrativeStatus.REDACTED
    assert redacted.narrative.merchant is None
    assert available.narrative.status is NarrativeStatus.AVAILABLE
    assert available.narrative.merchant == "美团外卖"
    assert available.narrative.channel == "微信"
    assert unavailable.narrative.status is NarrativeStatus.UNAVAILABLE


def test_erased_narrative_is_stable_and_never_exposes_private_fields() -> None:
    facts = _facts(_journal(description_ref=SIDECAR_ID), reporting=(_reporting(),))
    decoder = FakeDecoder(
        DecodedTransactionNarrative(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            status=NarrativeStatus.ERASED,
        )
    )

    view = EverydayEntryQueryService(
        FakeSource(facts),
        narrative_decoder=decoder,
    ).get(
        BOOK_ID,
        TRANSACTION_ID,
        narrative_access=NarrativeAccess.OWNER_AUTHORIZED,
    )

    assert view.narrative.status is NarrativeStatus.ERASED
    assert view.narrative.merchant is None
    assert view.narrative.channel is None
    assert "美团" not in repr(view)


def test_source_and_decoder_cannot_cross_book_authorization_scope() -> None:
    cross_book_facts = _facts(
        _journal(description_ref=SIDECAR_ID),
        reporting=(_reporting(),),
        book_id=OTHER_BOOK_ID,
    )
    with pytest.raises(RuntimeError, match="Book scope"):
        EverydayEntryQueryService(FakeSource(cross_book_facts)).get(
            BOOK_ID,
            TRANSACTION_ID,
        )

    facts = replace(cross_book_facts, book_id=BOOK_ID)
    cross_book_decoder = FakeDecoder(
        DecodedTransactionNarrative(
            book_id=OTHER_BOOK_ID,
            sidecar_id=SIDECAR_ID,
            status=NarrativeStatus.AVAILABLE,
            merchant="must not leak",
        )
    )
    with pytest.raises(RuntimeError, match="authorized scope"):
        EverydayEntryQueryService(
            FakeSource(facts),
            narrative_decoder=cross_book_decoder,
        ).get(
            BOOK_ID,
            TRANSACTION_ID,
            narrative_access=NarrativeAccess.OWNER_AUTHORIZED,
        )
