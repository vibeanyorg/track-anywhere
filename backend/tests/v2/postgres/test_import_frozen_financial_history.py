from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tests.v2.postgres.test_frozen_import_catalog import seed_target_baseline
import track_anywhere.application.imports.import_frozen_financial_history as frozen_import
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    plan_sha256,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.journal.events import (
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.privacy import (
    ImportArchiveManifestRecord,
    ProtectedDescriptionSidecarRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    TransactionReversalRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def _fixed_synthetic_plan():
    return build_valid_fixture_plan(
        target_book_id=UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
    )


def _cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": bytes(range(32))},
        )
    )


def test_synthetic_plan_exercises_card_direction_and_usdt_precision() -> None:
    plan = _fixed_synthetic_plan()
    cards = tuple(
        account for account in plan.accounts if account.account_subtype == "credit_card"
    )
    alias = next(account for account in cards if account.close_after_import)
    reviewed_cards = tuple(
        account.expected_natural_units
        for account in cards
        if not account.close_after_import
    )
    usdt_assets = tuple(asset for asset in plan.assets if asset.asset_code == "USDT")
    usdt_postings = tuple(
        posting
        for event in plan.events
        for posting in (
            event.payload.postings
            if type(event.payload) is JournalTransactionPosted
            else event.payload.inverse_postings
            if type(event.payload) is JournalTransactionReversed
            else ()
        )
        if posting.asset_code == "USDT"
    )

    assert len(cards) == 5
    assert alias.expected_natural_units == 0
    assert reviewed_cards == (1, 1, 1, 1)
    assert len(usdt_assets) == 1
    usdt = usdt_assets[0]
    assert (usdt.ledger_scale, usdt.input_scale, usdt.display_scale) == (8, 6, 6)
    assert len(usdt_postings) == 2
    assert {posting.units for posting in usdt_postings} == {"12345678"}


def test_command_request_contains_only_fixed_hashes_and_counts() -> None:
    plan = _fixed_synthetic_plan()

    command = frozen_import.build_frozen_financial_history_command(
        plan,
        expected_plan_hash=plan_sha256(plan),
    )

    payload = command.idempotency_payload()
    encoded = json.dumps(payload, sort_keys=True)
    assert set(payload) == {
        "source_dump_hash",
        "manifest_hash",
        "card_review_hash",
        "plan_hash",
        "expected_terminal_hash",
        "counts",
    }
    assert payload["counts"] == {
        "accounts": 121,
        "archives": 1,
        "assets": 20,
        "categories": 37,
        "category_versions": 37,
        "descriptions": 138,
        "events": 176,
        "journal_transactions": 138,
        "postings": 290,
        "quarantine": 0,
        "reporting_assignments": 38,
        "reporting_lines": 38,
        "reversals": 8,
    }
    assert command.command_id == plan.events[0].command_id
    assert command.book_id == plan.target_book_id
    assert command.operation == frozen_import.FROZEN_IMPORT_OPERATION
    for protected in ("fixture-purpose", "fixture-account", "fixture-category"):
        assert protected not in encoded
        assert protected not in repr(command)


def test_pure_builder_rejects_a_plan_hash_mismatch_before_runtime_composition() -> None:
    plan = _fixed_synthetic_plan()

    with pytest.raises(
        frozen_import.FrozenFinancialHistoryImportError,
        match="fixed contract",
    ):
        frozen_import.build_frozen_financial_history_command(
            plan,
            expected_plan_hash="f" * 64,
        )


def test_runtime_import_has_no_backend_tools_dependency() -> None:
    source = Path(frozen_import.__file__).read_text(encoding="utf-8")

    assert "backend.tools" not in source
    assert "frozen_v1_history" not in source


def test_object_builder_and_runtime_never_reparse_canonical_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _fixed_synthetic_plan()
    parse_calls = 0

    def counted_parse(raw):
        nonlocal parse_calls
        parse_calls += 1
        return plan

    monkeypatch.setattr(
        frozen_import,
        "parse_canonical_plan_bytes",
        counted_parse,
        raising=False,
    )

    frozen_import.build_frozen_financial_history_command(
        plan,
        expected_plan_hash=plan_sha256(plan),
    )
    with pytest.raises(frozen_import.FrozenFinancialHistoryImportError):
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id="offline:wrong"),
            uow_factory=lambda: (_ for _ in ()).throw(AssertionError),
            protected_content_cipher=_cipher(),
        )

    assert parse_calls == 0


def test_runtime_composition_rejects_non_fixed_actor_before_opening_a_uow() -> None:
    plan = _fixed_synthetic_plan()
    opened = False

    def uow_factory():
        nonlocal opened
        opened = True
        raise AssertionError("UoW must not open for an invalid offline actor")

    with pytest.raises(
        frozen_import.FrozenFinancialHistoryImportError,
        match="fixed contract",
    ):
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id="offline:wrong"),
            uow_factory=uow_factory,
            protected_content_cipher=_cipher(),
        )

    assert opened is False


def test_fixed_import_applies_one_current_native_batch_and_safe_sidecars(
    pg_engine,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    calls = {"append_project": 0, "book_lock": 0, "commit": 0, "outer": 0}

    class TracingUnitOfWork(SqlAlchemyUnitOfWork):
        def __enter__(self):
            calls["outer"] += 1
            return super().__enter__()

        def __exit__(self, exc_type, exc, traceback):
            result = super().__exit__(exc_type, exc, traceback)
            if exc_type is None:
                calls["commit"] += 1
            return result

    class TracingCommitter(LedgerCommitter):
        def execute_under_book_lock(self, session, book_id):
            calls["book_lock"] += 1
            return super().execute_under_book_lock(session, book_id)

        def append_and_project(self, *args, **kwargs):
            calls["append_project"] += 1
            return super().append_and_project(*args, **kwargs)

    outcome = frozen_import.import_frozen_financial_history(
        plan,
        expected_plan_hash=plan_sha256(plan),
        raw_key="frozen-import-receipt",
        actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        uow_factory=lambda: TracingUnitOfWork(session_factory),
        protected_content_cipher=_cipher(),
        ledger_committer=TracingCommitter(),
    )

    assert outcome.replayed is False
    assert outcome.result.first_book_position == 1
    assert outcome.result.last_book_position == 176
    assert calls == {"append_project": 1, "book_lock": 1, "commit": 1, "outer": 1}
    assert outcome.result.body["plan_hash"] == plan_sha256(plan)
    assert outcome.result.body["inserted_counts"] == {
        "accounts": 57,
        "archives": 1,
        "assets": 4,
        "categories": 37,
        "category_versions": 37,
        "credit_card_transactions": 0,
        "descriptions": 138,
        "events": 176,
        "journal_transactions": 138,
        "postings": 290,
        "quarantine": 0,
        "reporting_lines": 38,
        "reversals": 8,
    }

    alias = next(account for account in plan.accounts if account.close_after_import)
    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, plan.target_book_id)
        stored_alias = session.get(
            AccountRecord,
            (plan.target_book_id, alias.account_id),
        )
        receipt = session.scalar(select(CommandReceiptRecord))
        stored_events = tuple(
            session.scalars(
                select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
            )
        )
        assert head is not None
        assert head.last_position == 176
        assert head.last_hash.hex() == plan.expected_terminal_hash
        assert stored_alias is not None and stored_alias.status == "closed"
        assert receipt is not None and receipt.status == "completed"
        assert receipt.command_id == plan.events[0].command_id
        assert {event.command_id for event in stored_events} == {
            plan.events[0].command_id
        }
        assert {event.actor_subject_id for event in stored_events} == {
            FROZEN_IMPORT_ACTOR_SUBJECT_ID
        }
        assert tuple(event.event_id for event in stored_events) == tuple(
            event.event_id for event in plan.events
        )
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 20
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 121
        )
        assert session.scalar(select(func.count()).select_from(CategoryRecord)) == 37
        assert (
            session.scalar(select(func.count()).select_from(CategoryVersionRecord))
            == 37
        )
        assert (
            session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 176
        )
        assert (
            session.scalar(select(func.count()).select_from(JournalTransactionRecord))
            == 138
        )
        assert (
            session.scalar(select(func.count()).select_from(JournalPostingRecord))
            == 290
        )
        usdt = session.get(AssetRecord, "USDT")
        usdt_postings = tuple(
            session.scalars(
                select(JournalPostingRecord)
                .where(JournalPostingRecord.asset_code == "USDT")
                .order_by(JournalPostingRecord.posting_id)
            )
        )
        assert usdt is not None
        assert (usdt.ledger_scale, usdt.input_scale, usdt.display_scale) == (8, 6, 6)
        assert len(usdt_postings) == 2
        assert {int(posting.units) for posting in usdt_postings} == {12_345_678}
        assert (
            session.scalar(select(func.count()).select_from(TransactionReversalRecord))
            == 8
        )
        assert (
            session.scalar(select(func.count()).select_from(ReportingLineRecord)) == 38
        )
        assert (
            session.scalar(
                select(func.count()).select_from(CreditCardTransactionRecord)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(ProtectedDescriptionSidecarRecord)
            )
            == 139
        )
        assert (
            session.scalar(
                select(func.count()).select_from(ImportArchiveManifestRecord)
            )
            == 1
        )
