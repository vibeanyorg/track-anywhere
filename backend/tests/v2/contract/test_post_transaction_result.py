from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind


def _posting(*, side: PostingSide, amount: object = "12.34") -> PostTransactionPosting:
    return PostTransactionPosting(
        posting_id=uuid4(),
        account_id=uuid4(),
        asset_code="USD",
        side=side,
        amount=amount,  # type: ignore[arg-type]
    )


def _command(**changes: object) -> PostTransactionCommand:
    values: dict[str, object] = {
        "book_id": uuid4(),
        "command_id": uuid4(),
        "transaction_id": uuid4(),
        "expected_stream_version": 0,
        "kind": TransactionKind.STANDARD,
        "postings": (
            _posting(side=PostingSide.DEBIT),
            _posting(side=PostingSide.CREDIT),
        ),
        "effective_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
        "description_ref": uuid4(),
    }
    values.update(changes)
    return PostTransactionCommand(**values)  # type: ignore[arg-type]


def test_command_contract_is_immutable_and_hashes_every_financial_input() -> None:
    command = _command()

    assert command.operation == "journal.post"
    assert command.idempotency_payload() == {
        "description_ref": str(command.description_ref),
        "effective_at": "2026-07-14T12:30:00.000000Z",
        "expected_stream_version": 0,
        "external_references": [],
        "kind": "standard",
        "postings": [
            {
                "account_id": str(posting.account_id),
                "amount": "12.34",
                "asset_code": "USD",
                "posting_id": str(posting.posting_id),
                "side": posting.side.value,
            }
            for posting in command.postings
        ],
        "transaction_id": str(command.transaction_id),
    }

    with pytest.raises((AttributeError, TypeError)):
        command.kind = TransactionKind.OPENING  # type: ignore[misc]


@pytest.mark.parametrize("amount", [1, 1.0, True, None, "", " 1", "+1", "-1"])
def test_posting_contract_requires_a_nonempty_decimal_string(amount: object) -> None:
    with pytest.raises(ValueError):
        _posting(side=PostingSide.DEBIT, amount=amount)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("book_id", "not-a-uuid"),
        ("command_id", "not-a-uuid"),
        ("transaction_id", "not-a-uuid"),
        ("expected_stream_version", -1),
        ("expected_stream_version", True),
        ("kind", "standard"),
        ("postings", []),
        ("effective_at", datetime(2026, 7, 14)),
        ("description_ref", "not-a-uuid"),
    ],
)
def test_command_contract_rejects_ambiguous_runtime_shapes(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _command(**{field: value})


def test_command_ids_remain_uuid_objects() -> None:
    command = _command()

    assert type(command.book_id) is UUID
    assert type(command.command_id) is UUID
    assert type(command.transaction_id) is UUID


@pytest.mark.parametrize(
    "kind",
    [TransactionKind.FX, TransactionKind.INVESTMENT_CASH],
)
def test_general_post_contract_cannot_bypass_specialized_financial_commands(
    kind: TransactionKind,
) -> None:
    with pytest.raises(ValueError):
        _command(kind=kind)
