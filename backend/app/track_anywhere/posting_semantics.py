from __future__ import annotations


POSTING_CANONICAL_MODEL = "debit_credit"
DEBIT_CREDIT_AMOUNT_RULE = "posting amount is positive; side carries debit/credit direction"
DEBIT_CREDIT_SIDE_RULE = "posting side is the only persisted debit/credit direction; do not infer direction from amount sign"
LEGACY_SIGNED_SCOPE = "historical migration and posting-semantics audit only"
LEGACY_SIGNED_AMOUNT_RULE = (
    "legacy signed amount is old signed balance delta; do not treat it as canonical posting semantics"
)
POSTING_AMOUNT_FIELD = "postings.amount"
POSTING_SIDE_FIELD = "postings.side"
POSTING_AMOUNT_SEMANTICS_FIELD = "postings.amount_semantics"
PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS = (
    "postings",
    "side",
    "amount_semantics",
    "signed_amount",
    "raw_amount",
)
POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS = (
    "record_ref",
    "transaction_id",
    "position",
    "account_id",
    "currency",
    "legacy_amount",
    "action",
)
POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS = (
    "target_side",
    "target_amount",
)
POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS = (
    "amount_semantics",
    *POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
)
POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS = tuple(
    dict.fromkeys(
        (
            *PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS,
            *POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS,
        )
    )
)


def canonical_posting_semantics_metadata() -> dict[str, str]:
    return {
        "canonical_model": POSTING_CANONICAL_MODEL,
        "debit_credit_amount_rule": DEBIT_CREDIT_AMOUNT_RULE,
        "debit_credit_side_rule": DEBIT_CREDIT_SIDE_RULE,
        "posting_amount_field": POSTING_AMOUNT_FIELD,
        "posting_side_field": POSTING_SIDE_FIELD,
        "posting_amount_semantics_field": POSTING_AMOUNT_SEMANTICS_FIELD,
        "legacy_signed_scope": LEGACY_SIGNED_SCOPE,
    }


def backup_posting_semantics_metadata() -> dict[str, str]:
    return {
        **canonical_posting_semantics_metadata(),
        "side_field": POSTING_SIDE_FIELD,
        "amount_semantics_field": POSTING_AMOUNT_SEMANTICS_FIELD,
        "legacy_signed_amount_rule": LEGACY_SIGNED_AMOUNT_RULE,
    }


def public_write_posting_semantics_schema_extra() -> dict[str, object]:
    return {
        "x-posting-semantics": {
            **canonical_posting_semantics_metadata(),
            "scope": "public_write_raw_posting_input_guard",
            "request_schema_rule": (
                "public write schemas reject raw posting internals; this guard does not mean every command writes postings"
            ),
            "forbidden_input_fields": list(PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS),
        }
    }


def posting_semantics_review_decision_schema_extra() -> dict[str, object]:
    return {
        "x-posting-semantics": {
            **canonical_posting_semantics_metadata(),
            "scope": "posting_semantics_review_decision_input_guard",
            "allowed_input_fields": list(POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS),
            "recommendation_read_only_fields": list(POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS),
            "forbidden_input_fields": list(POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS),
        }
    }
