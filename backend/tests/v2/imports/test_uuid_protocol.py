from __future__ import annotations

from uuid import UUID

import pytest

from backend.tools.frozen_v1_history.namespaces import (
    ENTITY_KINDS,
    deterministic_uuid,
)


def test_uuid_v5_protocol_preserves_frozen_golden_values() -> None:
    assert deterministic_uuid("book", "household") == UUID(
        "2ded542b-3687-585c-b6e3-44f7b63835ff"
    )
    assert deterministic_uuid("transaction", "book-a", "tx-001") == UUID(
        "86a777d8-5971-5260-bf7a-3f2f657bcdc8"
    )
    assert deterministic_uuid("posting", "book-a", "tx-001", "2") == UUID(
        "b6fabee3-60f6-5410-b32e-c4ad4dbb1eb5"
    )
    assert deterministic_uuid("category", "book-a", "cat-food") == UUID(
        "8c078ee6-be6e-520b-bcd8-6a0a8165588d"
    )
    assert deterministic_uuid("category_version", "book-a", "catv-food-1") == UUID(
        "b7f7f0b5-a2db-5e40-9f78-d015770e0eef"
    )
    assert deterministic_uuid("line", "book-a", "tx-1", "line-1") == UUID(
        "2947c5e5-ab2c-5d07-b922-c4617030fa23"
    )
    assert deterministic_uuid("line_version", "book-a", "tx-1", "line-1", "1") == UUID(
        "7020f7cf-702b-5878-873c-733c5bc0f6b3"
    )


def test_account_identity_uses_frozen_source_book_not_target_book() -> None:
    assert deterministic_uuid(
        "account", "source-book", "legacy-account-001"
    ) == UUID("29ed81c6-ca76-5256-8212-9163ac4dd4cb")
    assert deterministic_uuid(
        "account",
        "target-book",
        "legacy-account-001",
    ) == UUID("c79aff12-5beb-58c6-b4a4-be6304f9d11d")


def test_uuid_protocol_has_explicit_future_import_kinds_and_typed_parts() -> None:
    assert {
        "account",
        "archive",
        "category",
        "category_version",
        "command",
        "description",
        "event",
        "line",
        "line_version",
        "posting",
        "reporting",
        "transaction",
    } <= ENTITY_KINDS
    assert deterministic_uuid("account", "ab", "c") != deterministic_uuid(
        "account", "a", "bc"
    )
    with pytest.raises(ValueError, match="unknown deterministic UUID kind"):
        deterministic_uuid("typo", "source")
    for invalid in ((), ("",), ("   ",), (1,)):
        with pytest.raises(ValueError, match="nonblank strings"):
            deterministic_uuid("account", *invalid)  # type: ignore[arg-type]
