from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping

from .accounting import legacy_signed_amount_to_debit_credit, posting_balance_delta
from .drafts import DraftTransaction
from .ledger import Account, Transaction


@dataclass(frozen=True)
class PostingSemanticsIssue:
    transaction_id: str
    account_id: str
    account_type: str
    account_subtype: str | None
    currency: str
    legacy_amount: Decimal
    inferred_side: str | None
    inferred_positive_amount: Decimal | None
    legacy_balance_delta: Decimal
    debit_credit_balance_delta: Decimal | None
    amount_semantics: str
    severity: str
    reason: str
    issue_type: str = "posting_semantics"
    position: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload = {
            "issue_type": self.issue_type,
            "transaction_id": self.transaction_id,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "account_subtype": self.account_subtype,
            "currency": self.currency,
            "legacy_amount": str(self.legacy_amount),
            "inferred_side": self.inferred_side,
            "inferred_positive_amount": str(self.inferred_positive_amount) if self.inferred_positive_amount is not None else None,
            "legacy_balance_delta": str(self.legacy_balance_delta),
            "debit_credit_balance_delta": str(self.debit_credit_balance_delta) if self.debit_credit_balance_delta is not None else None,
            "amount_semantics": self.amount_semantics,
            "severity": self.severity,
            "reason": self.reason,
        }
        if self.position is not None:
            payload["position"] = self.position
        return payload


@dataclass(frozen=True)
class PostingRewriteCandidate:
    transaction_id: str
    position: int
    account_id: str
    account_type: str
    account_subtype: str | None
    currency: str
    legacy_amount: Decimal
    amount_semantics: str
    target_side: str
    target_amount: Decimal
    legacy_balance_delta: Decimal
    target_balance_delta: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "position": self.position,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "account_subtype": self.account_subtype,
            "currency": self.currency,
            "legacy_amount": str(self.legacy_amount),
            "amount_semantics": self.amount_semantics,
            "target_side": self.target_side,
            "target_amount": str(self.target_amount),
            "legacy_balance_delta": str(self.legacy_balance_delta),
            "target_balance_delta": str(self.target_balance_delta),
        }


@dataclass(frozen=True)
class LiabilityPostingReviewRecommendation:
    transaction_id: str
    position: int
    account_id: str
    account_subtype: str | None
    currency: str
    legacy_amount: Decimal
    amount_semantics: str
    inferred_side_from_legacy_sign: str
    inferred_positive_amount: Decimal
    recommended_action: str
    recommendation_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "position": self.position,
            "account_id": self.account_id,
            "account_subtype": self.account_subtype,
            "currency": self.currency,
            "legacy_amount": str(self.legacy_amount),
            "amount_semantics": self.amount_semantics,
            "inferred_side_from_legacy_sign": self.inferred_side_from_legacy_sign,
            "inferred_positive_amount": str(self.inferred_positive_amount),
            "recommended_action": self.recommended_action,
            "recommendation_reason": self.recommendation_reason,
            "resolution_options": [
                {
                    "action": "confirm_as_outstanding_liability",
                    "target_side": "credit",
                    "target_amount": str(self.inferred_positive_amount),
                    "meaning": "liability balance increases by this amount",
                },
                {
                    "action": "confirm_as_liability_reduction_or_overpayment",
                    "target_side": "debit",
                    "target_amount": str(self.inferred_positive_amount),
                    "meaning": "liability balance decreases by this amount",
                },
            ],
        }


def audit_posting_semantics(
    transactions: Iterable[Transaction],
    accounts_by_id: Mapping[str, Account],
) -> dict[str, object]:
    issues: list[PostingSemanticsIssue] = []
    auto_rewrite_candidates: list[PostingRewriteCandidate] = []
    liability_review_recommendations: list[LiabilityPostingReviewRecommendation] = []
    counts = {
        "legacy_signed_postings": 0,
        "debit_credit_postings": 0,
        "legacy_signed_confirmed_postings": 0,
        "legacy_signed_draft_postings": 0,
        "missing_account_postings": 0,
        "invalid_amount_semantics_postings": 0,
        "invalid_debit_credit_postings": 0,
        "mixed_semantics_transactions": 0,
        "unbalanced_transactions": 0,
        "liability_legacy_postings_requiring_review": 0,
        "credit_card_legacy_postings_requiring_review": 0,
        "auto_rewrite_candidates": 0,
        "manual_review_blockers": 0,
    }

    for transaction in transactions:
        transaction_semantics: set[str] = set()
        legacy_totals: dict[str, Decimal] = {}
        debit_credit_totals: dict[str, dict[str, Decimal]] = {}
        for position, posting in enumerate(transaction.postings):
            if posting.amount_semantics in {"legacy_signed", "debit_credit"}:
                transaction_semantics.add(posting.amount_semantics)
                if posting.amount_semantics == "legacy_signed" and posting.amount != Decimal("0"):
                    legacy_totals[posting.currency] = legacy_totals.get(posting.currency, Decimal("0")) + posting.amount
                elif (
                    posting.amount_semantics == "debit_credit"
                    and posting.side in {"debit", "credit"}
                    and posting.amount > Decimal("0")
                ):
                    side_totals = debit_credit_totals.setdefault(posting.currency, {"debit": Decimal("0"), "credit": Decimal("0")})
                    side_totals[posting.side] = side_totals.get(posting.side, Decimal("0")) + posting.amount

            account = accounts_by_id.get(posting.account_id)
            if account is None:
                counts["missing_account_postings"] += 1
                counts["manual_review_blockers"] += 1
                issues.append(
                    PostingSemanticsIssue(
                        transaction_id=transaction.transaction_id,
                        account_id=posting.account_id,
                        account_type="unknown",
                        account_subtype=None,
                        currency=posting.currency,
                        legacy_amount=posting.amount,
                        inferred_side=None,
                        inferred_positive_amount=None,
                        legacy_balance_delta=posting.amount,
                        debit_credit_balance_delta=None,
                        amount_semantics=posting.amount_semantics,
                        severity="error",
                        reason="posting references an account that is not available for semantics audit",
                        issue_type="missing_account",
                        position=position,
                    )
                )
                continue

            if posting.amount_semantics not in {"legacy_signed", "debit_credit"}:
                counts["invalid_amount_semantics_postings"] += 1
                counts["manual_review_blockers"] += 1
                issues.append(
                    PostingSemanticsIssue(
                        transaction_id=transaction.transaction_id,
                        account_id=posting.account_id,
                        account_type=account.type,
                        account_subtype=account.subtype,
                        currency=posting.currency,
                        legacy_amount=posting.amount,
                        inferred_side=posting.side,
                        inferred_positive_amount=posting.amount if posting.amount > Decimal("0") else None,
                        legacy_balance_delta=posting.amount,
                        debit_credit_balance_delta=None,
                        amount_semantics=posting.amount_semantics,
                        severity="error",
                        reason="posting amount_semantics must be legacy_signed or debit_credit",
                        issue_type="invalid_amount_semantics",
                        position=position,
                    )
                )
                continue

            if posting.amount_semantics == "legacy_signed":
                counts["legacy_signed_postings"] += 1
                counts["legacy_signed_confirmed_postings"] += 1
                if posting.amount == Decimal("0"):
                    counts["manual_review_blockers"] += 1
                    issues.append(
                        PostingSemanticsIssue(
                            transaction_id=transaction.transaction_id,
                            account_id=posting.account_id,
                            account_type=account.type,
                            account_subtype=account.subtype,
                            currency=posting.currency,
                            legacy_amount=posting.amount,
                            inferred_side=None,
                            inferred_positive_amount=None,
                            legacy_balance_delta=posting.amount,
                            debit_credit_balance_delta=None,
                            amount_semantics=posting.amount_semantics,
                            severity="error",
                            reason="legacy signed posting amount must not be zero",
                            issue_type="invalid_legacy_signed_shape",
                            position=position,
                        )
                    )
                    continue
                side, positive_amount = legacy_signed_amount_to_debit_credit(account.type, posting.amount)
                debit_credit_delta = posting_balance_delta(
                    account.type,
                    side=side,
                    amount=positive_amount,
                    amount_semantics="debit_credit",
                )
                if account.type == "liability":
                    counts["liability_legacy_postings_requiring_review"] += 1
                    if account.subtype == "credit_card":
                        counts["credit_card_legacy_postings_requiring_review"] += 1
                    counts["manual_review_blockers"] += 1
                    liability_review_recommendations.append(
                        LiabilityPostingReviewRecommendation(
                            transaction_id=transaction.transaction_id,
                            position=position,
                            account_id=posting.account_id,
                            account_subtype=account.subtype,
                            currency=posting.currency,
                            legacy_amount=posting.amount,
                            amount_semantics=posting.amount_semantics,
                            inferred_side_from_legacy_sign=side,
                            inferred_positive_amount=positive_amount,
                            recommended_action=_recommended_liability_review_action(account, posting.amount),
                            recommendation_reason=_liability_review_reason(account, posting.amount),
                        )
                    )
                    issues.append(
                        PostingSemanticsIssue(
                            transaction_id=transaction.transaction_id,
                            account_id=posting.account_id,
                            account_type=account.type,
                            account_subtype=account.subtype,
                            currency=posting.currency,
                            legacy_amount=posting.amount,
                            inferred_side=side,
                            inferred_positive_amount=positive_amount,
                            legacy_balance_delta=posting.amount,
                            debit_credit_balance_delta=debit_credit_delta,
                            amount_semantics=posting.amount_semantics,
                            severity="warning",
                            reason="legacy liability posting changes meaning under natural debit/credit balance semantics and requires economic review",
                            issue_type="legacy_liability_review_required",
                            position=position,
                        )
                    )
                else:
                    counts["auto_rewrite_candidates"] += 1
                    auto_rewrite_candidates.append(
                        PostingRewriteCandidate(
                            transaction_id=transaction.transaction_id,
                            position=position,
                            account_id=posting.account_id,
                            account_type=account.type,
                            account_subtype=account.subtype,
                            currency=posting.currency,
                            legacy_amount=posting.amount,
                            amount_semantics=posting.amount_semantics,
                            target_side=side,
                            target_amount=positive_amount,
                            legacy_balance_delta=posting.amount,
                            target_balance_delta=debit_credit_delta,
                        )
                    )
                continue

            counts["debit_credit_postings"] += 1
            if posting.side not in {"debit", "credit"} or posting.amount <= Decimal("0"):
                counts["invalid_debit_credit_postings"] += 1
                counts["manual_review_blockers"] += 1
                issues.append(
                    PostingSemanticsIssue(
                        transaction_id=transaction.transaction_id,
                        account_id=posting.account_id,
                        account_type=account.type,
                        account_subtype=account.subtype,
                        currency=posting.currency,
                        legacy_amount=posting.amount,
                        inferred_side=posting.side,
                        inferred_positive_amount=posting.amount if posting.amount > Decimal("0") else None,
                        legacy_balance_delta=posting.amount,
                        debit_credit_balance_delta=None,
                        amount_semantics=posting.amount_semantics,
                        severity="error",
                        reason="debit_credit posting must have debit/credit side and positive amount",
                        issue_type="invalid_debit_credit_shape",
                        position=position,
                    )
                )
        _append_transaction_balance_issues(
            transaction.transaction_id,
            transaction_semantics,
            legacy_totals,
            debit_credit_totals,
            counts,
            issues,
        )

    return {
        "counts": counts,
        "issues": [issue.to_dict() for issue in issues],
        "manual_review_blockers": [issue.to_dict() for issue in issues if issue.severity in {"error", "warning"}],
        "auto_rewrite_candidates": [candidate.to_dict() for candidate in auto_rewrite_candidates],
        "manual_review_recommendations": [
            recommendation.to_dict() for recommendation in liability_review_recommendations
        ],
        "auto_rewrite_ready": counts["manual_review_blockers"] == 0,
        "cutover_ready": not issues and counts["legacy_signed_postings"] == 0,
    }


def _append_transaction_balance_issues(
    transaction_id: str,
    transaction_semantics: set[str],
    legacy_totals: dict[str, Decimal],
    debit_credit_totals: dict[str, dict[str, Decimal]],
    counts: dict[str, int],
    issues: list[PostingSemanticsIssue],
) -> None:
    if len(transaction_semantics) > 1:
        counts["mixed_semantics_transactions"] += 1
        counts["manual_review_blockers"] += 1
        issues.append(
            _transaction_issue(
                transaction_id,
                currency="",
                delta=Decimal("0"),
                reason="transaction postings must not mix legacy signed and debit/credit semantics",
                issue_type="mixed_transaction_semantics",
                amount_semantics="mixed_or_unknown",
            )
        )
        return
    if transaction_semantics == {"legacy_signed"}:
        for currency, total in legacy_totals.items():
            if total != Decimal("0"):
                counts["unbalanced_transactions"] += 1
                counts["manual_review_blockers"] += 1
                issues.append(
                    _transaction_issue(
                        transaction_id,
                        currency=currency,
                        delta=total,
                        reason="legacy signed transaction postings do not balance by currency",
                        issue_type="unbalanced_legacy_signed_transaction",
                        amount_semantics="legacy_signed",
                    )
                )
        return
    if transaction_semantics == {"debit_credit"}:
        for currency, side_totals in debit_credit_totals.items():
            delta = side_totals.get("debit", Decimal("0")) - side_totals.get("credit", Decimal("0"))
            if delta != Decimal("0"):
                counts["unbalanced_transactions"] += 1
                counts["manual_review_blockers"] += 1
                issues.append(
                    _transaction_issue(
                        transaction_id,
                        currency=currency,
                        delta=delta,
                        reason="debit/credit transaction postings do not balance by currency",
                        issue_type="unbalanced_debit_credit_transaction",
                        amount_semantics="debit_credit",
                    )
                )


def _transaction_issue(
    transaction_id: str,
    *,
    currency: str,
    delta: Decimal,
    reason: str,
    issue_type: str,
    amount_semantics: str,
) -> PostingSemanticsIssue:
    return PostingSemanticsIssue(
        transaction_id=transaction_id,
        account_id="",
        account_type="transaction",
        account_subtype=None,
        currency=currency,
        legacy_amount=delta,
        inferred_side=None,
        inferred_positive_amount=None,
        legacy_balance_delta=delta,
        debit_credit_balance_delta=None,
        amount_semantics=amount_semantics,
        severity="error",
        reason=reason,
        issue_type=issue_type,
    )


def posting_semantics_cutover_plan(
    transactions: Iterable[Transaction],
    accounts_by_id: Mapping[str, Account],
    *,
    drafts: Iterable[DraftTransaction] = (),
) -> dict[str, object]:
    audit = full_posting_semantics_audit(transactions, accounts_by_id, drafts=drafts)
    counts = audit["counts"]
    if audit["cutover_ready"]:
        next_action = "complete"
    elif audit["auto_rewrite_ready"]:
        next_action = "auto_rewrite_legacy_signed_postings"
    else:
        next_action = "manual_review_required"
    return {
        **audit,
        "next_action": next_action,
        "positive_only_rewrite_allowed": bool(audit["auto_rewrite_ready"]) and counts["legacy_signed_postings"] > 0,
    }


def full_posting_semantics_audit(
    transactions: Iterable[Transaction],
    accounts_by_id: Mapping[str, Account],
    *,
    drafts: Iterable[DraftTransaction] = (),
) -> dict[str, object]:
    confirmed_audit = audit_posting_semantics(transactions, accounts_by_id)
    draft_audit = _audit_draft_posting_semantics(drafts, accounts_by_id)
    return _merge_audits(confirmed_audit, draft_audit)


def _audit_draft_posting_semantics(
    drafts: Iterable[DraftTransaction],
    accounts_by_id: Mapping[str, Account],
) -> dict[str, object]:
    draft_records = [
        _DraftPostingAuditRecord(transaction_id=f"draft:{draft.draft_id}", postings=draft.proposed_postings)
        for draft in drafts
    ]
    audit = audit_posting_semantics(draft_records, accounts_by_id)  # type: ignore[arg-type]
    counts = audit["counts"]
    counts["legacy_signed_draft_postings"] = counts["legacy_signed_postings"]
    counts["legacy_signed_confirmed_postings"] = 0
    return audit


@dataclass(frozen=True)
class _DraftPostingAuditRecord:
    transaction_id: str
    postings: object


def _merge_audits(*audits: dict[str, object]) -> dict[str, object]:
    counts: dict[str, int] = {}
    issues = []
    manual_review_blockers = []
    auto_rewrite_candidates = []
    manual_review_recommendations = []
    for audit in audits:
        for key, value in audit["counts"].items():  # type: ignore[union-attr]
            counts[key] = counts.get(key, 0) + int(value)
        issues.extend(audit["issues"])  # type: ignore[arg-type]
        manual_review_blockers.extend(audit["manual_review_blockers"])  # type: ignore[arg-type]
        auto_rewrite_candidates.extend(audit["auto_rewrite_candidates"])  # type: ignore[arg-type]
        manual_review_recommendations.extend(audit["manual_review_recommendations"])  # type: ignore[arg-type]
    return {
        "counts": counts,
        "issues": issues,
        "manual_review_blockers": manual_review_blockers,
        "auto_rewrite_candidates": auto_rewrite_candidates,
        "manual_review_recommendations": manual_review_recommendations,
        "auto_rewrite_ready": counts.get("manual_review_blockers", 0) == 0,
        "cutover_ready": not issues and counts.get("legacy_signed_postings", 0) == 0,
    }


def _recommended_liability_review_action(account: Account, legacy_amount: Decimal) -> str:
    if account.subtype == "credit_card":
        return "manual_review_required_credit_card_semantics"
    if legacy_amount > Decimal("0"):
        return "manual_review_required_possible_opening_liability"
    return "manual_review_required_possible_liability_payment_or_charge"


def _liability_review_reason(account: Account, legacy_amount: Decimal) -> str:
    if account.subtype == "credit_card":
        return "credit-card signs were ambiguous under legacy signed postings; choose whether this row represents a charge/outstanding liability or a payment/overpayment"
    if legacy_amount > Decimal("0"):
        return "legacy positive liability amount may represent an intended outstanding balance, but debit/credit target side must be confirmed"
    return "legacy negative liability amount may represent either a debt increase under old balancing signs or a liability reduction under natural balance semantics"
