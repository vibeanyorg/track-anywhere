from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from backend.tests.v2.imports._plan_factory import (
    build_valid_fixture_plan,
    fixture_id,
)
from backend.tests.v2.postgres.test_frozen_import_catalog import (
    BASELINE_ACCOUNT_IDS,
    processing_receipt,
    seed_full_catalog,
    seed_target_baseline,
    stage_processing_receipt,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.imports.contracts import FrozenFinancialHistoryPlan
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.credit_cards.events import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from track_anywhere.domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
)
from track_anywhere.infrastructure.db.repositories.frozen_import import (
    FrozenImportCatalogDrift,
    FrozenImportCatalogRepository,
    ProcessingReceiptIdentity,
)


def _assert_preflight_drift(
    pg_engine,
    plan: FrozenFinancialHistoryPlan,
    *,
    entity_kind: str,
    field_name: str,
    receipt: ProcessingReceiptIdentity | None = None,
    staged_receipt: ProcessingReceiptIdentity | None = None,
    extra_receipts: tuple[ProcessingReceiptIdentity, ...] = (),
    stage_receipt: bool = True,
) -> FrozenImportCatalogDrift:
    with Session(pg_engine) as session:
        transaction = session.begin()
        try:
            if stage_receipt:
                stage_processing_receipt(
                    session,
                    plan,
                    receipt=staged_receipt or processing_receipt(plan),
                )
            for index, extra in enumerate(extra_receipts, start=1):
                stage_processing_receipt(
                    session,
                    plan,
                    receipt=extra,
                    idempotency_key_hash=bytes([index]) * 32,
                )
            before = (
                session.scalar(select(func.count()).select_from(AssetRecord)),
                session.scalar(
                    select(func.count())
                    .select_from(AccountRecord)
                    .where(AccountRecord.book_id == plan.target_book_id)
                ),
                session.scalar(
                    select(func.count())
                    .select_from(CategoryRecord)
                    .where(CategoryRecord.book_id == plan.target_book_id)
                ),
            )
            with pytest.raises(FrozenImportCatalogDrift) as caught:
                FrozenImportCatalogRepository(session).apply_exact_catalog(
                    plan,
                    current_receipt=receipt or processing_receipt(plan),
                )
            after = (
                session.scalar(select(func.count()).select_from(AssetRecord)),
                session.scalar(
                    select(func.count())
                    .select_from(AccountRecord)
                    .where(AccountRecord.book_id == plan.target_book_id)
                ),
                session.scalar(
                    select(func.count())
                    .select_from(CategoryRecord)
                    .where(CategoryRecord.book_id == plan.target_book_id)
                ),
            )
            assert before == after
            assert not session.new
            assert caught.value.entity_kind == entity_kind
            assert caught.value.field_name == field_name
            return caught.value
        finally:
            if transaction.is_active:
                transaction.rollback()


def _force_out_of_band_drift(
    migrated_postgres_database,
    *,
    table_name: str,
    statement: str,
    parameters: dict[str, object],
) -> None:
    assert table_name in {"accounts", "assets", "book_event_heads"}
    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                f'alter table public."{table_name}" disable trigger user'
            )
            connection.execute(text(statement), parameters)
            connection.exec_driver_sql(
                f'alter table public."{table_name}" enable trigger user'
            )
    finally:
        admin_engine.dispose()


@pytest.mark.parametrize(
    ("table_name", "update_sql", "parameters", "entity_kind", "field_name"),
    (
        (
            "assets",
            "update assets set ledger_scale=3 where asset_code='T00'",
            {},
            "asset",
            "ledger_scale",
        ),
        (
            "accounts",
            "update accounts set account_type='liability' "
            "where book_id=:book_id and account_id=:account_id",
            {"account_id": fixture_id(1005)},
            "account",
            "account_type",
        ),
        (
            "accounts",
            "update accounts set status='closed' "
            "where book_id=:book_id and account_id=:account_id",
            {"account_id": next(iter(BASELINE_ACCOUNT_IDS))},
            "account",
            "status",
        ),
    ),
)
def test_asset_and_account_accounting_drift_aborts_before_writes(
    pg_engine,
    migrated_postgres_database,
    table_name: str,
    update_sql: str,
    parameters: dict[str, object],
    entity_kind: str,
    field_name: str,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    _force_out_of_band_drift(
        migrated_postgres_database,
        table_name=table_name,
        statement=update_sql,
        parameters={"book_id": plan.target_book_id, **parameters},
    )

    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind=entity_kind,
        field_name=field_name,
    )


@pytest.mark.parametrize(
    ("entity_kind", "field_name"),
    (
        ("category", "parent_category_id"),
        ("category_version", "change_reason_code"),
        ("category", "status"),
    ),
)
def test_category_parent_version_or_status_drift_aborts_before_writes(
    pg_engine,
    entity_kind: str,
    field_name: str,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    category = plan.categories[0]
    parent = plan.categories[1]
    with pg_engine.begin() as connection:
        if field_name == "parent_category_id":
            _insert_category(connection, plan.target_book_id, parent)
        category_parent_id = (
            parent.category_id if field_name == "parent_category_id" else None
        )
        category_status = "archived" if field_name == "status" else category.status
        version_reason = (
            "drifted"
            if field_name == "change_reason_code"
            else category.version.change_reason_code
        )
        _insert_category(
            connection,
            plan.target_book_id,
            category,
            parent_category_id=category_parent_id,
            status=category_status,
            change_reason_code=version_reason,
        )

    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind=entity_kind,
        field_name=field_name,
    )


def _insert_category(
    connection,
    book_id: UUID,
    category,
    *,
    parent_category_id: UUID | None = None,
    status: str | None = None,
    change_reason_code: str | None = None,
) -> None:
    selected_status = status or category.status
    connection.execute(
        text(
            "insert into categories ("
            "book_id, category_id, parent_category_id, current_name, "
            "current_version_id, status"
            ") values ("
            ":book_id, :category_id, :parent_id, :name, null, :status"
            ")"
        ),
        {
            "book_id": book_id,
            "category_id": category.category_id,
            "parent_id": parent_category_id,
            "name": category.current_name,
            "status": selected_status,
        },
    )
    connection.execute(
        text(
            "insert into category_versions ("
            "book_id, category_id, category_version_id, parent_category_id, "
            "name, status, change_reason_code"
            ") values ("
            ":book_id, :category_id, :version_id, :parent_id, :name, "
            ":status, :reason"
            ")"
        ),
        {
            "book_id": book_id,
            "category_id": category.category_id,
            "version_id": category.current_version_id,
            "parent_id": parent_category_id,
            "name": category.current_name,
            "status": selected_status,
            "reason": change_reason_code or category.version.change_reason_code,
        },
    )
    connection.execute(
        text(
            "update categories set current_version_id=:version_id "
            "where book_id=:book_id and category_id=:category_id"
        ),
        {
            "book_id": book_id,
            "category_id": category.category_id,
            "version_id": category.current_version_id,
        },
    )


def test_unexpected_asset_account_or_category_aborts(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    unexpected_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, account_subtype, "
                "system_role, current_name, status"
                ") values ("
                ":book_id, :account_id, 'T00', 'asset', null, null, "
                "'Unexpected', 'active'"
                ")"
            ),
            {"book_id": plan.target_book_id, "account_id": unexpected_id},
        )

    drift = _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="account",
        field_name="unexpected",
    )
    assert drift.entity_id == unexpected_id


def test_inactive_book_and_nonzero_head_abort(
    pg_engine,
    migrated_postgres_database,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    with pg_engine.begin() as connection:
        connection.execute(
            text("update books set write_state='paused_integrity' where book_id=:id"),
            {"id": plan.target_book_id},
        )
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="book",
        field_name="write_state",
    )

    with pg_engine.begin() as connection:
        connection.execute(
            text("update books set write_state='active' where book_id=:id"),
            {"id": plan.target_book_id},
        )
    _force_out_of_band_drift(
        migrated_postgres_database,
        table_name="book_event_heads",
        statement=(
            "update book_event_heads set last_position=1, last_hash=:hash "
            "where book_id=:id"
        ),
        parameters={"id": plan.target_book_id, "hash": b"h" * 32},
    )
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="book_head",
        field_name="last_position",
    )


def test_only_the_exact_current_processing_receipt_is_allowed(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    wrong = ProcessingReceiptIdentity(
        actor_subject_id=processing_receipt(plan).actor_subject_id,
        operation=processing_receipt(plan).operation,
        command_id=uuid4(),
    )
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="receipt",
        field_name="current_processing",
        staged_receipt=wrong,
    )

    other = ProcessingReceiptIdentity(
        actor_subject_id="offline:other",
        operation="other.operation",
        command_id=uuid4(),
    )
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="receipt",
        field_name="current_processing",
        extra_receipts=(other,),
    )


def test_missing_current_processing_receipt_aborts(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)

    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="receipt",
        field_name="current_processing",
        stage_receipt=False,
    )


def _seed_sidecar(pg_engine, plan: FrozenFinancialHistoryPlan) -> UUID:
    sidecar_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into protected_description_sidecars ("
                "book_id, sidecar_id, kind, ciphertext, key_ref, nonce, "
                "algorithm, content_hash, status, erased_at"
                ") values ("
                ":book_id, :sidecar_id, 'import_archive', :ciphertext, 'v1', "
                ":nonce, 'AES-256-GCM+HKDF-SHA256', :content_hash, "
                "'active', null"
                ")"
            ),
            {
                "book_id": plan.target_book_id,
                "sidecar_id": sidecar_id,
                "ciphertext": b"c" * 16,
                "nonce": b"n" * 12,
                "content_hash": b"a" * 32,
            },
        )
    return sidecar_id


def test_sidecar_and_archive_occupancy_abort_with_specific_table(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    sidecar_id = _seed_sidecar(pg_engine, plan)
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="occupancy",
        field_name="protected_description_sidecars",
    )

    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into import_archive_manifests ("
                "book_id, archive_id, contract_version, source_dump_hash, "
                "source_manifest_hash, card_review_hash, plan_hash, "
                "archive_content_commitment, seal, record_counts"
                ") values ("
                ":book_id, :archive_id, 1, :source_hash, :manifest_hash, "
                ":review_hash, :plan_hash, :commitment, :seal, '{}'::jsonb"
                ")"
            ),
            {
                "book_id": plan.target_book_id,
                "archive_id": sidecar_id,
                "source_hash": b"s" * 32,
                "manifest_hash": b"m" * 32,
                "review_hash": b"r" * 32,
                "plan_hash": b"p" * 32,
                "commitment": b"a" * 32,
                "seal": b"z" * 32,
            },
        )
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="occupancy",
        field_name="import_archive_manifests",
    )


def _pending_standard(plan: FrozenFinancialHistoryPlan) -> PendingEvent:
    first, second = plan.accounts[5:7]
    transaction_id = uuid4()
    return PendingEvent(
        event_id=uuid4(),
        stream_type="journal_transaction",
        stream_id=transaction_id,
        payload=JournalTransactionPosted(
            transaction_id=transaction_id,
            kind=TransactionKind.STANDARD,
            postings=(
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=0,
                    account_id=first.account_id,
                    asset_code=first.asset_code,
                    side=PostingSide.DEBIT,
                    units="1",
                ),
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=1,
                    account_id=second.account_id,
                    asset_code=second.asset_code,
                    side=PostingSide.CREDIT,
                    units="1",
                ),
            ),
            description_ref=None,
            external_references=(),
        ),
        command_id=uuid4(),
        actor_subject_id="offline:occupancy-test",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _append(
    pg_engine, plan: FrozenFinancialHistoryPlan, events: tuple[PendingEvent, ...]
) -> None:
    expected = {event.stream_key: 0 for event in events}
    with Session(pg_engine) as session, session.begin():
        committer = LedgerCommitter()
        locked = committer.execute_under_book_lock(session, plan.target_book_id)
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions=expected,
            events=events,
        )


def test_journal_and_reporting_occupancy_abort_with_specific_table(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    seed_full_catalog(pg_engine, plan)
    posted = _pending_standard(plan)
    _append(pg_engine, plan, (posted,))
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="occupancy",
        field_name="journal_transactions",
    )

    reporting = PendingEvent(
        event_id=uuid4(),
        stream_type="reporting_lines",
        stream_id=posted.stream_id,
        payload=ReportingLinesAssigned(
            transaction_id=posted.stream_id,
            classification_revision=1,
            lines=(
                ReportingLine(
                    line_id=uuid4(),
                    line_version_id=uuid4(),
                    catalog_id=plan.categories[0].current_version_id,
                    position=0,
                    asset_code="T00",
                    units="1",
                    line_kind=ReportingLineKind.EXPENSE,
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=plan.categories[0].category_id,
                    description_ref=None,
                ),
            ),
        ),
        command_id=uuid4(),
        actor_subject_id="offline:occupancy-test",
        correlation_id=uuid4(),
        causation_event_id=posted.event_id,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _append(pg_engine, plan, (reporting,))
    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="occupancy",
        field_name="reporting_lines",
    )


def test_typed_card_occupancy_aborts_with_specific_table(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    seed_full_catalog(pg_engine, plan)
    card = next(
        account
        for account in plan.accounts
        if account.account_subtype == "credit_card" and not account.close_after_import
    )
    counter = plan.accounts[5]
    transaction_id = uuid4()
    pending = PendingEvent(
        event_id=uuid4(),
        stream_type="journal_transaction",
        stream_id=transaction_id,
        payload=CreditCardTransactionRecorded(
            intent=CreditCardIntent.PAYMENT,
            transaction_id=transaction_id,
            card_account_id=card.account_id,
            counter_account_id=counter.account_id,
            original_transaction_id=None,
            postings=(
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=0,
                    account_id=card.account_id,
                    asset_code=card.asset_code,
                    side=PostingSide.DEBIT,
                    units="1",
                ),
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=1,
                    account_id=counter.account_id,
                    asset_code=counter.asset_code,
                    side=PostingSide.CREDIT,
                    units="1",
                ),
            ),
            description_ref=None,
            external_references=(),
        ),
        command_id=uuid4(),
        actor_subject_id="offline:occupancy-test",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _append(pg_engine, plan, (pending,))

    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="occupancy",
        field_name="credit_card_transactions",
    )


def test_preflight_rejects_nonzero_retired_alias_target_balance(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    seed_full_catalog(pg_engine, plan)
    alias = next(account for account in plan.accounts if account.close_after_import)
    assert alias.expected_natural_units == 0

    # A real posted balance necessarily creates financial occupancy too. The
    # alias-specific check is required to run before the generic occupancy check.
    counter = plan.accounts[5]
    transaction_id = uuid4()
    pending = PendingEvent(
        event_id=uuid4(),
        stream_type="journal_transaction",
        stream_id=transaction_id,
        payload=JournalTransactionPosted(
            transaction_id=transaction_id,
            kind=TransactionKind.STANDARD,
            postings=(
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=0,
                    account_id=alias.account_id,
                    asset_code=alias.asset_code,
                    side=PostingSide.DEBIT,
                    units="1",
                ),
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=1,
                    account_id=counter.account_id,
                    asset_code=counter.asset_code,
                    side=PostingSide.CREDIT,
                    units="1",
                ),
            ),
            description_ref=None,
            external_references=(),
        ),
        command_id=uuid4(),
        actor_subject_id="offline:alias-preflight-test",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _append(pg_engine, plan, (pending,))

    _assert_preflight_drift(
        pg_engine,
        plan,
        entity_kind="account",
        field_name="current_balance_units",
    )


def test_drift_path_never_autoflushes_or_stages_catalog_rows(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    with pg_engine.begin() as connection:
        connection.execute(
            text("update assets set display_scale=1 where asset_code='T00'")
        )

    flushes = 0

    def count_flushes(*_args) -> None:
        nonlocal flushes
        flushes += 1

    with Session(pg_engine) as session:
        transaction = session.begin()
        event.listen(session, "before_flush", count_flushes)
        try:
            receipt = stage_processing_receipt(session, plan)
            with pytest.raises(FrozenImportCatalogDrift):
                FrozenImportCatalogRepository(session).apply_exact_catalog(
                    plan,
                    current_receipt=receipt,
                )
            assert flushes == 0
            assert not session.new
        finally:
            event.remove(session, "before_flush", count_flushes)
            transaction.rollback()
