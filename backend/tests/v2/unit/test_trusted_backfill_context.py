from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from track_anywhere.application.idempotency import (
    CommandActor,
    IdempotencyValidationError,
)
from track_anywhere.application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    _execute_trusted_v1_backfill_assign_reporting_lines,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    _execute_trusted_v1_backfill_post_transaction,
)
from track_anywhere.domain.journal import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
)


_MANIFEST_HASH = "a" * 64
_ACTOR = CommandActor(subject_id=f"backfill:{_MANIFEST_HASH[:32]}")


def _unexpected_uow():
    raise AssertionError("invalid trusted context reached the UnitOfWork")


def _post_command() -> PostTransactionCommand:
    return PostTransactionCommand(
        book_id=uuid4(),
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        kind=TransactionKind.STANDARD,
        postings=(
            PostTransactionPosting(
                posting_id=uuid4(),
                account_id=uuid4(),
                asset_code="CNY",
                side=PostingSide.DEBIT,
                amount="1.00",
            ),
            PostTransactionPosting(
                posting_id=uuid4(),
                account_id=uuid4(),
                asset_code="CNY",
                side=PostingSide.CREDIT,
                amount="1.00",
            ),
        ),
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _reporting_command() -> AssignReportingLinesCommand:
    return AssignReportingLinesCommand(
        book_id=uuid4(),
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_revision=0,
        lines=(
            ReportingLineInput(
                line_id=uuid4(),
                line_version_id=uuid4(),
                catalog_id=uuid4(),
                asset_code="CNY",
                units="100",
                line_kind=ReportingLineKind.EXPENSE,
                dimension=ReportingDimension.CATEGORY,
                dimension_id=uuid4(),
            ),
        ),
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"actor": CommandActor(subject_id="human-user")},
            id="non-backfill-actor",
        ),
        pytest.param(
            {"actor": CommandActor(subject_id=f"backfill:{'b' * 32}")},
            id="actor-snapshot-mismatch",
        ),
        pytest.param(
            {"raw_key": f"online:sha256:{_MANIFEST_HASH}:journal:txn-1"},
            id="non-backfill-key",
        ),
        pytest.param(
            {"raw_key": f"backfill:sha256:{_MANIFEST_HASH}:reporting:txn-1"},
            id="wrong-operation-context",
        ),
        pytest.param({"max_attempts": 3}, id="retrying-context"),
        pytest.param({"authorize": lambda *_args, **_kwargs: None}, id="auth-bypass"),
        pytest.param({"ledger_committer": object()}, id="committer-bypass"),
    ],
)
def test_trusted_post_path_rejects_non_backfill_invocations(overrides) -> None:
    arguments = {
        "raw_key": f"backfill:sha256:{_MANIFEST_HASH}:journal:txn-1",
        "actor": _ACTOR,
        "uow_factory": _unexpected_uow,
        "max_attempts": 1,
        **overrides,
    }

    with pytest.raises(IdempotencyValidationError, match="trusted V1 backfill"):
        _execute_trusted_v1_backfill_post_transaction(
            _post_command(),
            **arguments,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"actor": CommandActor(subject_id="human-user")},
            id="non-backfill-actor",
        ),
        pytest.param(
            {"raw_key": f"backfill:sha256:{_MANIFEST_HASH}:journal:txn-1"},
            id="wrong-operation-context",
        ),
        pytest.param(
            {"raw_key": f"backfill:sha256:{'b' * 64}:reporting:txn-1"},
            id="actor-snapshot-mismatch",
        ),
        pytest.param({"max_attempts": 3}, id="retrying-context"),
    ],
)
def test_trusted_reporting_path_rejects_non_backfill_invocations(overrides) -> None:
    arguments = {
        "raw_key": f"backfill:sha256:{_MANIFEST_HASH}:reporting:txn-1",
        "actor": _ACTOR,
        "uow_factory": _unexpected_uow,
        "max_attempts": 1,
        **overrides,
    }

    with pytest.raises(IdempotencyValidationError, match="trusted V1 backfill"):
        _execute_trusted_v1_backfill_assign_reporting_lines(
            _reporting_command(),
            **arguments,
        )
