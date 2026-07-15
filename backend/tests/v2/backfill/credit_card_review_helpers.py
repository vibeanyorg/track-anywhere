from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.tools.backfill_v1.credit_card_review import (
    CreditCardSemanticReview,
    build_credit_card_review_document,
    calculate_reviewed_card_balances,
    parse_credit_card_review,
)
from backend.tools.backfill_v1.manifest import FrozenSourceManifest


def approved_mechanical_review(
    _tmp_path: Path,
    *,
    manifest: FrozenSourceManifest,
    rows: dict[str, list[dict[str, object]]],
    posting_overrides: dict[str, tuple[str, str]] | None = None,
    neutralized_transaction_ids: frozenset[str] = frozenset(),
    closed_account_ids: frozenset[str] = frozenset(),
) -> CreditCardSemanticReview:
    """Create an explicit test-fixture approval; production has no such default."""

    transactions = {
        str(row["transaction_id"]): str(row["book_id"])
        for row in rows.get("transactions", [])
    }
    card_accounts = {
        (str(row["book_id"]), str(row["account_id"]))
        for row in rows.get("accounts", [])
        if str(row.get("type")) == "liability"
        and str(row.get("subtype")) in {"credit_card", "legacy_credit_card"}
    }
    scoped_transaction_ids = {
        str(row["transaction_id"])
        for row in rows.get("postings", [])
        if (
            transactions[str(row["transaction_id"])],
            str(row["account_id"]),
        )
        in card_accounts
    }
    decisions: dict[str, tuple[str, str]] = {}
    for row in rows.get("postings", []):
        if str(row["transaction_id"]) not in scoped_transaction_ids:
            continue
        amount = str(row["amount"])
        if row.get("amount_semantics") == "debit_credit":
            side = str(row["side"])
        else:
            side = "debit" if not amount.startswith("-") else "credit"
        decisions[str(row["id"])] = (str(row["account_id"]), side)
    decisions.update(posting_overrides or {})
    expected = calculate_reviewed_card_balances(
        rows_by_table=rows,
        posting_decisions=decisions,
        neutralized_transaction_ids=neutralized_transaction_ids,
    )
    document = build_credit_card_review_document(
        manifest=manifest,
        rows_by_table=rows,
        posting_decisions=decisions,
        neutralized_transaction_ids=neutralized_transaction_ids,
        closed_account_ids=closed_account_ids,
        expected_balances=expected,
        reviewer="test:explicit-fixture-reviewer",
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return parse_credit_card_review(
        document,
        manifest=manifest,
        rows_by_table=rows,
    )


__all__ = ["approved_mechanical_review"]
