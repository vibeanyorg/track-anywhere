from __future__ import annotations

from uuid import UUID

from backend.tools.backfill_v1.namespaces import deterministic_uuid


def test_uuid_v5_backfill_namespaces_have_frozen_golden_values() -> None:
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


def test_uuid_inputs_are_typed_and_unambiguous() -> None:
    assert deterministic_uuid("account", "ab", "c") != deterministic_uuid(
        "account", "a", "bc"
    )
