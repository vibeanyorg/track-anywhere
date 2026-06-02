from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .books import DEFAULT_BOOK_ID
from .db_migrations import current_alembic_head
from .errors import PolicyDenied, ValidationError
from .posting_semantics_audit import full_posting_semantics_audit, posting_semantics_cutover_plan
from .posting_semantics import (
    POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
    canonical_posting_semantics_metadata,
)


class SystemStatusUseCases:
    def system_readiness(self) -> dict[str, object]:
        state = self.storage.database_readiness()
        expected_revision = current_alembic_head()
        checks = {
            "database": "ok",
            "migrations": "ok" if state["alembic_revision"] == expected_revision else "error",
        }
        return {
            "status": "ok" if all(value == "ok" for value in checks.values()) else "error",
            "api_version": "v1",
            "database": state["database"],
            "schema": state["schema"],
            "alembic_revision": state["alembic_revision"],
            "expected_revision": expected_revision,
            "checks": checks,
        }

    def system_status(self, token, *, include_counts: bool = False) -> dict[str, object]:
        actor = self.actor_from_token(token, required_scope="ledger:read")
        payload = self.system_readiness()
        payload["actor"] = {
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "scopes": sorted(actor.scopes),
        }
        if include_counts:
            payload["counts"] = self.storage.status_table_counts()
        return payload

    def posting_semantics_audit(self, token, *, book_id: str = DEFAULT_BOOK_ID) -> dict[str, object]:
        self.actor_for_book(token, book_id, "ledger:read")
        self.storage.refresh_read_cache_from_storage()
        accounts = self._list_accounts_from_storage(book_id=book_id)
        transactions = self._list_all_transactions_from_storage(book_id=book_id)
        drafts = self.storage.list_drafts(book_id=book_id)
        return full_posting_semantics_audit(
            transactions,
            {account.account_id: account for account in accounts},
            drafts=drafts,
        )

    def posting_semantics_cutover_plan(self, token, *, book_id: str = DEFAULT_BOOK_ID) -> dict[str, object]:
        self.actor_for_book(token, book_id, "ledger:read")
        self.storage.refresh_read_cache_from_storage()
        accounts = self._list_accounts_from_storage(book_id=book_id)
        transactions = self._list_all_transactions_from_storage(book_id=book_id)
        drafts = self.storage.list_drafts(book_id=book_id)
        return posting_semantics_cutover_plan(
            transactions,
            {account.account_id: account for account in accounts},
            drafts=drafts,
        )

    def rewrite_posting_semantics(self, token, *, idempotency_key: str, book_id: str = DEFAULT_BOOK_ID) -> dict[str, object]:
        actor = self.actor_for_book(token, book_id, "ledger:confirm")
        request_hash = self._hash_request_payload(
            "system.posting_semantics.rewrite",
            extra={"book_id": book_id},
        )

        def run():
            plan = self.posting_semantics_cutover_plan(token, book_id=book_id)
            if plan["cutover_ready"]:
                return {
                    "status": "noop",
                    "book_id": book_id,
                    "reason": "posting semantics already use debit_credit",
                    "posting_semantics": _posting_semantics_write_metadata(),
                    "plan": plan,
                }
            if not plan["auto_rewrite_ready"]:
                raise ValidationError("posting semantics rewrite requires manual review blockers to be resolved first")
            auto_rewrite_candidates = _auto_rewrite_candidates(plan)
            result = self.storage.rewrite_legacy_postings_to_debit_credit(
                book_id=book_id,
                candidates=auto_rewrite_candidates,
            )
            plan_after = self.posting_semantics_cutover_plan(token, book_id=book_id)
            _require_cutover_ready(plan_after, "posting semantics rewrite")
            return {
                "status": "rewritten",
                "book_id": book_id,
                "posting_semantics": _posting_semantics_write_metadata(),
                **result,
                "plan_before": plan,
                "plan_after": plan_after,
            }

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="system.posting_semantics.rewrite",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_idempotency()
        return {**result, "idempotent_replay": replay}

    def resolve_posting_semantics_reviews(
        self,
        token,
        payload: dict[str, object],
        *,
        idempotency_key: str,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> dict[str, object]:
        actor = self.actor_for_book(token, book_id, "ledger:confirm")
        request_hash = self._hash_request_payload(
            "system.posting_semantics.resolve",
            payload,
            extra={"book_id": book_id},
        )

        def run():
            raw_decisions = payload.get("decisions")
            if not isinstance(raw_decisions, list) or not raw_decisions:
                raise ValidationError("posting semantics review resolution requires at least one decision")
            decisions = [_normalize_posting_semantics_review_decision(item) for item in raw_decisions]
            decision_keys = {_posting_semantics_review_decision_key(decision) for decision in decisions}
            if len(decision_keys) != len(decisions):
                raise ValidationError("posting semantics review decisions must not contain duplicate rows")
            plan_before = self.posting_semantics_cutover_plan(token, book_id=book_id)
            expected_recommendations = plan_before.get("manual_review_recommendations")
            manual_blockers = plan_before.get("manual_review_blockers")
            if not isinstance(expected_recommendations, list) or not expected_recommendations:
                raise ValidationError("posting semantics review resolution found no manual review recommendations")
            if _has_hard_posting_semantics_review_blockers(manual_blockers):
                raise ValidationError("posting semantics review has blockers that cannot be resolved by liability review decisions")
            expected_keys = {_posting_semantics_review_recommendation_key(item) for item in expected_recommendations}
            if decision_keys != expected_keys:
                raise ValidationError("posting semantics review decisions must cover every current manual review recommendation exactly once")
            result = self.storage.resolve_legacy_liability_posting_reviews(book_id=book_id, decisions=decisions)
            plan_after_review = self.posting_semantics_cutover_plan(token, book_id=book_id)
            auto_rewrite_result = {}
            if (
                (plan_after_review["auto_rewrite_ready"] or _has_only_transient_mixed_semantics_blockers(plan_after_review))
                and not plan_after_review["cutover_ready"]
                and plan_after_review.get("auto_rewrite_candidates")
            ):
                auto_rewrite_result = self.storage.rewrite_legacy_postings_to_debit_credit(
                    book_id=book_id,
                    candidates=_auto_rewrite_candidates(plan_after_review),
                )
            plan_after = self.posting_semantics_cutover_plan(token, book_id=book_id)
            _require_cutover_ready(plan_after, "posting semantics review resolution")
            return {
                "status": "resolved",
                "book_id": book_id,
                "posting_semantics": _posting_semantics_write_metadata(),
                **result,
                **auto_rewrite_result,
                "plan_before": plan_before,
                "plan_after_review": plan_after_review,
                "plan_after": plan_after,
            }

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="system.posting_semantics.resolve",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_idempotency()
        return {**result, "idempotent_replay": replay}

    def local_dev_session(self) -> dict[str, object]:
        if self.config.mode != "local":
            raise PolicyDenied("dev session is only available in local mode")
        return {
            "credential_token": self.owner_token,
            "identity": {"provider": "local", "subject": "owner", "email": None, "name": "Local Owner"},
        }

    def local_dev_token(self) -> dict[str, object]:
        if self.config.mode != "local":
            raise PolicyDenied("dev token is only available in local mode")
        actor = self.actor_from_token(self.owner_token)
        return {
            "token": self.owner_token,
            "actor": {
                "actor_id": actor.actor_id,
                "actor_type": actor.actor_type,
                "scopes": sorted(actor.scopes),
            },
        }


def _normalize_posting_semantics_review_decision(item: object) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValidationError("posting semantics review decision must be an object")
    allowed = set(POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS)
    unsupported = set(item) - allowed
    if unsupported:
        forbidden = unsupported & set(POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS)
        if forbidden:
            raise ValidationError(
                "posting semantics review decision must not contain raw, derived, or read-only recommendation fields: "
                f"{', '.join(sorted(str(field) for field in forbidden))}"
            )
        raise ValidationError(
            "posting semantics review decision contains unsupported fields: "
            f"{', '.join(sorted(str(field) for field in unsupported))}"
        )
    record_ref = item.get("record_ref") or item.get("transaction_id")
    if item.get("record_ref") and item.get("transaction_id") and item["record_ref"] != item["transaction_id"]:
        raise ValidationError("posting semantics review record_ref and transaction_id must match when both are provided")
    position = item.get("position")
    account_id = item.get("account_id")
    currency = item.get("currency")
    legacy_amount = item.get("legacy_amount")
    action = item.get("action")
    if not all(isinstance(value, str) and value for value in (record_ref, account_id, currency, legacy_amount, action)):
        raise ValidationError("posting semantics review decision is missing required fields")
    if type(position) is not int or position < 0:
        raise ValidationError("posting semantics review decision requires non-negative integer position")
    action_to_side = {
        "confirm_as_outstanding_liability": "credit",
        "confirm_as_liability_reduction_or_overpayment": "debit",
    }
    target_side = action_to_side.get(action)
    if target_side is None:
        raise ValidationError("posting semantics review action is invalid")
    try:
        target_amount = abs(Decimal(legacy_amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("posting semantics review legacy_amount must be a decimal string") from exc
    if target_amount == Decimal("0"):
        raise ValidationError("posting semantics review legacy_amount must not be zero")
    return {
        "record_ref": record_ref,
        "position": str(position),
        "account_id": account_id,
        "currency": currency,
        "legacy_amount": legacy_amount,
        "target_side": target_side,
        "target_amount": str(target_amount),
    }


def _posting_semantics_write_metadata() -> dict[str, object]:
    return {
        **canonical_posting_semantics_metadata(),
        "liability_review_actions": {
            "confirm_as_outstanding_liability": {
                "target_side": "credit",
                "meaning": "liability balance increases",
            },
            "confirm_as_liability_reduction_or_overpayment": {
                "target_side": "debit",
                "meaning": "liability balance decreases",
            },
        },
    }


def _auto_rewrite_candidates(plan: dict[str, object]) -> list[dict[str, object]]:
    candidates = plan.get("auto_rewrite_candidates")
    if not isinstance(candidates, list):
        raise ValidationError("posting semantics cutover plan is missing auto rewrite candidates")
    if not all(isinstance(candidate, dict) for candidate in candidates):
        raise ValidationError("posting semantics cutover plan has invalid auto rewrite candidates")
    counts = plan.get("counts")
    if isinstance(counts, dict):
        expected_count = counts.get("auto_rewrite_candidates")
        if type(expected_count) is int and expected_count != len(candidates):
            raise ValidationError("posting semantics cutover plan auto rewrite candidate count mismatch")
    candidate_keys = {_posting_semantics_rewrite_candidate_key(candidate) for candidate in candidates}
    if len(candidate_keys) != len(candidates):
        raise ValidationError("posting semantics auto rewrite candidates must not contain duplicate rows")
    return candidates


def _has_hard_posting_semantics_review_blockers(blockers: object) -> bool:
    if not isinstance(blockers, list):
        return False
    allowed_transient_issue_types = {"legacy_liability_review_required", "mixed_transaction_semantics"}
    for blocker in blockers:
        if not isinstance(blocker, dict):
            return True
        if blocker.get("issue_type") not in allowed_transient_issue_types:
            return True
    return False


def _has_only_transient_mixed_semantics_blockers(plan: dict[str, object]) -> bool:
    blockers = plan.get("manual_review_blockers")
    if not isinstance(blockers, list) or not blockers:
        return False
    return all(isinstance(blocker, dict) and blocker.get("issue_type") == "mixed_transaction_semantics" for blocker in blockers)


def _require_cutover_ready(plan: dict[str, object], operation: str) -> None:
    if plan.get("cutover_ready") is not True:
        raise ValidationError(f"{operation} did not reach cutover_ready; rerun posting semantics audit")


def _posting_semantics_review_decision_key(decision: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        decision["record_ref"],
        decision["position"],
        decision["account_id"],
        decision["currency"],
        decision["legacy_amount"],
    )


def _posting_semantics_rewrite_candidate_key(item: object) -> tuple[str, str, str, str, str]:
    if not isinstance(item, dict):
        raise ValidationError("posting semantics rewrite candidate must be an object")
    record_ref = item.get("transaction_id")
    position = item.get("position")
    account_id = item.get("account_id")
    currency = item.get("currency")
    legacy_amount = item.get("legacy_amount")
    if not all(isinstance(value, str) and value for value in (record_ref, account_id, currency, legacy_amount)):
        raise ValidationError("posting semantics rewrite candidate is missing required fields")
    if type(position) is not int or position < 0:
        raise ValidationError("posting semantics rewrite candidate is missing position")
    return (record_ref, str(position), account_id, currency, legacy_amount)


def _posting_semantics_review_recommendation_key(item: object) -> tuple[str, str, str, str, str]:
    if not isinstance(item, dict):
        raise ValidationError("posting semantics review recommendation must be an object")
    record_ref = item.get("transaction_id")
    position = item.get("position")
    account_id = item.get("account_id")
    currency = item.get("currency")
    legacy_amount = item.get("legacy_amount")
    if not all(isinstance(value, str) and value for value in (record_ref, account_id, currency, legacy_amount)):
        raise ValidationError("posting semantics review recommendation is missing required fields")
    if type(position) is not int or position < 0:
        raise ValidationError("posting semantics review recommendation is missing position")
    return (record_ref, str(position), account_id, currency, legacy_amount)
