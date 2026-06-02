from __future__ import annotations

from typing import Any

from track_anywhere.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_AMOUNT_RULE,
    LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL,
    backup_posting_semantics_metadata,
    canonical_posting_semantics_metadata,
)


def posting_semantics_output_guidance(preferred_fields: list[str]) -> dict[str, Any]:
    return {
        **canonical_posting_semantics_metadata(),
        "preferred_fields": preferred_fields,
        "do_not_infer_signed_amounts": True,
    }


def backup_posting_semantics() -> dict[str, str]:
    return backup_posting_semantics_metadata()
