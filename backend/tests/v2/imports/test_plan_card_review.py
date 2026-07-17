from __future__ import annotations

from dataclasses import replace

import pytest

from backend.tools.frozen_v1_history.planner import (
    FrozenPlanCompilationError,
    compile_frozen_financial_history_plan,
)
from backend.tools.frozen_v1_history.namespaces import deterministic_uuid
from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from track_anywhere.domain.journal.events import (
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from track_anywhere.domain.journal.models import PostingSide

from backend.tests.v2.imports.test_plan_archive import (
    approved_plan,
    approved_source_and_review,
)


def test_zero_balance_alias_may_preserve_its_reviewed_historical_postings() -> None:
    plan = build_valid_fixture_plan()
    alias = next(account for account in plan.accounts if account.close_after_import)
    postings = [
        posting
        for event in plan.events
        for posting in (
            event.payload.postings
            if type(event.payload) is JournalTransactionPosted
            else event.payload.inverse_postings
            if type(event.payload) is JournalTransactionReversed
            else ()
        )
        if posting.account_id == alias.account_id
    ]

    assert len(postings) == 4
    assert {posting.side for posting in postings} == {
        PostingSide.DEBIT,
        PostingSide.CREDIT,
    }
    assert alias.expected_natural_units == 0


def test_fixed_plan_applies_exactly_the_reviewed_card_corrections_and_alias() -> None:
    plan = approved_plan()
    corrections = [
        event
        for event in plan.events
        if type(event.payload) is JournalTransactionReversed
        and event.payload.description_ref is not None
        and event.book_position > 130
    ]

    assert len(corrections) == 8
    assert sum(account.close_after_import for account in plan.accounts) == 1
    assert all(
        account.expected_natural_units == 0
        for account in plan.accounts
        if account.close_after_import
    )


def test_fixed_plan_preserves_every_reviewed_source_posting_mapping() -> None:
    source, review = approved_source_and_review()
    plan = compile_frozen_financial_history_plan(source=source, review=review)
    source_facts = {
        posting.posting_id: posting
        for event in plan.events
        for posting in (
            event.payload.postings
            if type(event.payload) is JournalTransactionPosted
            else event.payload.inverse_postings
            if type(event.payload) is JournalTransactionReversed
            else ()
        )
    }
    matched = 0
    for transaction in review.transactions:
        for decision in transaction.postings:
            fact = source_facts.get(
                deterministic_uuid(
                    "posting",
                    transaction.book_id,
                    transaction.source_transaction_id,
                    decision.source_posting_id,
                )
            )
            if (
                fact is not None
                and fact.account_id
                == deterministic_uuid(
                    "account", transaction.book_id, decision.target_account_id
                )
                and fact.side.value == decision.target_side
            ):
                matched += 1

    alias_source_id = review.accounts[0].source_account_id
    reviewed_alias_postings = sum(
        decision.target_account_id == alias_source_id
        for transaction in review.transactions
        for decision in transaction.postings
    )
    alias_target_id = deterministic_uuid(
        "account", review.accounts[0].book_id, alias_source_id
    )
    alias = next(
        account for account in plan.accounts if account.account_id == alias_target_id
    )

    assert matched == review.reviewed_posting_count == 48
    assert reviewed_alias_postings == 2
    assert alias.close_after_import
    assert alias.expected_natural_units == 0


@pytest.mark.parametrize(
    "mutation",
    ("target_side", "target_account", "action", "expected_balance"),
)
def test_planner_revalidates_hand_built_review_objects_and_redacts_failures(
    mutation: str,
) -> None:
    source, review = approved_source_and_review()
    sentinel = "sentinel-private-review-value"
    if mutation in {"target_side", "target_account"}:
        transaction = review.transactions[0]
        posting = transaction.postings[0]
        posting = (
            replace(posting, target_side=sentinel)
            if mutation == "target_side"
            else replace(posting, target_account_id=sentinel)
        )
        transaction = replace(
            transaction,
            postings=(posting,) + transaction.postings[1:],
        )
        review = replace(
            review,
            transactions=(transaction,) + review.transactions[1:],
        )
    elif mutation == "action":
        transaction = review.transactions[0]
        replacement_action = (
            "none"
            if transaction.post_import_action == "exact_reversal"
            else "exact_reversal"
        )
        review = replace(
            review,
            transactions=(replace(transaction, post_import_action=replacement_action),)
            + review.transactions[1:],
        )
    else:
        balance = review.expected_balances[0]
        review = replace(
            review,
            expected_balances=(
                replace(balance, natural_units=balance.natural_units + 1),
            )
            + review.expected_balances[1:],
        )

    with pytest.raises(FrozenPlanCompilationError) as captured:
        compile_frozen_financial_history_plan(source=source, review=review)

    rendered = " ".join(
        (
            str(captured.value),
            repr(captured.value),
            repr(captured.value.args),
            repr(vars(captured.value)),
        )
    )
    assert sentinel not in rendered
