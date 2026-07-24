from __future__ import annotations

from enum import StrEnum


class EntryErrorCode(StrEnum):
    """Stable, transport-independent Everyday Entry Gateway error codes."""

    INVALID_INPUT = "entry_invalid_input"
    AMOUNT_INVALID = "entry_amount_invalid"
    DENOMINATION_UNSUPPORTED = "entry_denomination_unsupported"
    AMOUNT_SOURCE_MISMATCH = "entry_amount_source_mismatch"
    ACCOUNT_NOT_FOUND = "entry_account_not_found"
    ACCOUNT_AMBIGUOUS = "entry_account_ambiguous"
    ACCOUNT_INELIGIBLE = "entry_account_ineligible"
    ACCOUNT_CLOSED = "entry_account_closed"
    SAME_ACCOUNT = "entry_same_account"
    CATEGORY_NOT_FOUND = "entry_category_not_found"
    CATEGORY_AMBIGUOUS = "entry_category_ambiguous"
    CATEGORY_INELIGIBLE = "entry_category_ineligible"
    CATEGORY_ALLOCATION_MISMATCH = "entry_category_allocation_mismatch"
    ORIGINAL_TRANSACTION_NOT_FOUND = "entry_original_transaction_not_found"
    REFUND_ALLOCATION_REQUIRED = "entry_refund_allocation_required"
    DUPLICATE_SUSPECTED = "entry_duplicate_suspected"
    UNSUPPORTED = "entry_unsupported"
    INTENT_NOT_FOUND = "entry_intent_not_found"
    INTENT_EXPIRED = "entry_intent_expired"
    INTENT_NOT_READY = "entry_intent_not_ready"
    COMMIT_TOKEN_INVALID = "entry_commit_token_invalid"
    INTENT_STALE = "entry_intent_stale"
    REQUEST_CONFLICT = "entry_request_conflict"
    BOOK_WRITE_BLOCKED = "entry_book_write_blocked"
    COMMIT_OUTCOME_UNKNOWN = "entry_commit_outcome_unknown"


class EntryGatewayError(RuntimeError):
    """Safe application error that adapters may map without inspecting text."""

    def __init__(
        self,
        code: EntryErrorCode,
        message: str,
        *,
        field: str | None = None,
        retryable: bool = False,
    ) -> None:
        if type(code) is not EntryErrorCode:
            raise TypeError("code must be an EntryErrorCode")
        if type(message) is not str or not message or len(message) > 512:
            raise ValueError("message must be nonblank and at most 512 characters")
        if field is not None and (
            type(field) is not str or not field or len(field) > 128
        ):
            raise ValueError("field must be nonblank and at most 128 characters")
        if type(retryable) is not bool:
            raise TypeError("retryable must be a bool")
        self.code = code
        self.field = field
        self.retryable = retryable
        super().__init__(message)


__all__ = ["EntryErrorCode", "EntryGatewayError"]
