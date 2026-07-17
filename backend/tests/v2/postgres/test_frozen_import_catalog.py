from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.tests.v2.imports._plan_factory import (
    build_valid_fixture_plan,
    fixture_id,
)
from track_anywhere.application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    PlannedCategory,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from track_anywhere.infrastructure.db.repositories.frozen_import import (
    FrozenImportCatalogDrift,
    FrozenImportCatalogRepository,
    ProcessingReceiptIdentity,
)


FROZEN_IMPORT_OPERATION = "imports.frozen-v1-financial-history"
BASELINE_ASSET_CODES = frozenset(f"T{index:02d}" for index in range(16))
BASELINE_ACCOUNT_IDS = frozenset(fixture_id(1000 + index) for index in range(64))
REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3]
    / "app/track_anywhere/infrastructure/db/repositories/frozen_import.py"
)
FENCE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "alembic/versions/v2_0013_frozen_import_fence.py"
)


def _asset_row(asset: PlannedAsset) -> dict[str, object]:
    return {
        "asset_code": asset.asset_code,
        "kind": asset.kind,
        "ledger_scale": asset.ledger_scale,
        "input_scale": asset.input_scale,
        "display_scale": asset.display_scale,
        "current_name": asset.current_name,
        "status": asset.status,
    }


def _account_row(
    book_id: UUID,
    account: PlannedAccount,
) -> dict[str, object]:
    return {
        "book_id": book_id,
        "account_id": account.account_id,
        "asset_code": account.asset_code,
        "account_type": account.account_type,
        "account_subtype": account.account_subtype,
        "system_role": account.system_role,
        "current_name": account.current_name,
        "status": account.status,
    }


def _category_row(
    book_id: UUID,
    category: PlannedCategory,
) -> dict[str, object]:
    return {
        "book_id": book_id,
        "category_id": category.category_id,
        "parent_category_id": category.parent_category_id,
        "current_name": category.current_name,
        "current_version_id": category.current_version_id,
        "status": category.status,
    }


def _category_version_row(
    book_id: UUID,
    category: PlannedCategory,
) -> dict[str, object]:
    return {
        "book_id": book_id,
        "category_id": category.category_id,
        "category_version_id": category.version.category_version_id,
        "parent_category_id": category.version.parent_category_id,
        "name": category.version.name,
        "status": category.version.status,
        "change_reason_code": category.version.change_reason_code,
    }


def processing_receipt(plan: FrozenFinancialHistoryPlan) -> ProcessingReceiptIdentity:
    return ProcessingReceiptIdentity(
        actor_subject_id=plan.events[0].actor_subject_id,
        operation=FROZEN_IMPORT_OPERATION,
        command_id=plan.events[0].command_id,
    )


def stage_processing_receipt(
    session,
    plan: FrozenFinancialHistoryPlan,
    *,
    receipt: ProcessingReceiptIdentity | None = None,
    idempotency_key_hash: bytes = b"k" * 32,
) -> ProcessingReceiptIdentity:
    selected = receipt or processing_receipt(plan)
    session.execute(
        text(
            "insert into command_receipts ("
            "actor_subject_id, book_id, operation, idempotency_key_hash, "
            "request_hash, command_id, status"
            ") values ("
            ":actor_subject_id, :book_id, :operation, :key_hash, "
            ":request_hash, :command_id, 'processing'"
            ")"
        ),
        {
            "actor_subject_id": selected.actor_subject_id,
            "book_id": plan.target_book_id,
            "operation": selected.operation,
            "key_hash": idempotency_key_hash,
            "request_hash": b"r" * 32,
            "command_id": selected.command_id,
        },
    )
    return selected


def complete_processing_receipt(
    session,
    plan: FrozenFinancialHistoryPlan,
    *,
    receipt: ProcessingReceiptIdentity | None = None,
) -> None:
    selected = receipt or processing_receipt(plan)
    result = session.execute(
        text(
            "update command_receipts set "
            "status='completed', response_schema_version=1, result_status=200, "
            "result_body='{}'::jsonb, completed_at=clock_timestamp() "
            "where actor_subject_id=:actor_subject_id and book_id=:book_id "
            "and operation=:operation and command_id=:command_id "
            "and status='processing'"
        ),
        {
            "actor_subject_id": selected.actor_subject_id,
            "book_id": plan.target_book_id,
            "operation": selected.operation,
            "command_id": selected.command_id,
        },
    )
    assert result.rowcount == 1


def seed_target_baseline(
    pg_engine,
    plan: FrozenFinancialHistoryPlan,
    *,
    seed_receipt: bool = False,
) -> None:
    if seed_receipt:
        raise AssertionError(
            "a processing receipt cannot be committed as baseline data"
        )
    baseline_assets = tuple(
        _asset_row(asset)
        for asset in plan.assets
        if asset.asset_code in BASELINE_ASSET_CODES
    )
    baseline_accounts = tuple(
        _account_row(plan.target_book_id, account)
        for account in plan.accounts
        if account.account_id in BASELINE_ACCOUNT_IDS
    )
    assert len(baseline_assets) == 16
    assert len(baseline_accounts) == 64

    with pg_engine.begin() as connection:
        connection.execute(AssetRecord.__table__.insert(), baseline_assets)
        connection.execute(
            BookRecord.__table__.insert(),
            {
                "book_id": plan.target_book_id,
                "current_name": "Synthetic target",
                "base_asset_code": None,
                "write_state": "active",
            },
        )
        connection.execute(
            text(
                "insert into book_event_heads "
                "(book_id, last_position, last_hash) "
                "values (:book_id, 0, :zero_hash)"
            ),
            {"book_id": plan.target_book_id, "zero_hash": bytes(32)},
        )
        connection.execute(AccountRecord.__table__.insert(), baseline_accounts)


def apply_full_catalog(pg_engine, plan: FrozenFinancialHistoryPlan) -> None:
    with Session(pg_engine) as session, session.begin():
        receipt = stage_processing_receipt(session, plan)
        result = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        assert result.assets_created == 4
        assert result.accounts_created == 57
        assert result.categories_created == 37
        assert result.category_versions_created == 37
        complete_processing_receipt(session, plan, receipt=receipt)


def seed_full_catalog(pg_engine, plan: FrozenFinancialHistoryPlan) -> None:
    missing_assets = tuple(
        _asset_row(asset)
        for asset in plan.assets
        if asset.asset_code not in BASELINE_ASSET_CODES
    )
    missing_accounts = tuple(
        _account_row(plan.target_book_id, account)
        for account in plan.accounts
        if account.account_id not in BASELINE_ACCOUNT_IDS
    )
    category_rows = tuple(
        {**_category_row(plan.target_book_id, category), "current_version_id": None}
        for category in plan.categories
    )
    version_rows = tuple(
        _category_version_row(plan.target_book_id, category)
        for category in plan.categories
    )
    with pg_engine.begin() as connection:
        connection.execute(AssetRecord.__table__.insert(), missing_assets)
        connection.execute(AccountRecord.__table__.insert(), missing_accounts)
        connection.execute(CategoryRecord.__table__.insert(), category_rows)
        connection.execute(CategoryVersionRecord.__table__.insert(), version_rows)
        for category in plan.categories:
            connection.execute(
                text(
                    "update categories set current_version_id=:version_id "
                    "where book_id=:book_id and category_id=:category_id"
                ),
                {
                    "book_id": plan.target_book_id,
                    "category_id": category.category_id,
                    "version_id": category.current_version_id,
                },
            )


def test_catalog_apply_inserts_only_missing_exact_rows_in_callers_transaction(
    pg_engine,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    with pg_engine.connect() as observer:
        assert observer.scalar(text("select count(*) from command_receipts")) == 0

    session = Session(pg_engine)
    transaction = session.begin()
    try:
        receipt = stage_processing_receipt(session, plan)
        assert session.scalar(text("select count(*) from command_receipts")) == 1
        with pg_engine.connect() as observer:
            assert observer.scalar(text("select count(*) from command_receipts")) == 0
        result = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )

        assert result.assets_created == 4
        assert result.accounts_created == 57
        assert result.categories_created == 37
        assert result.category_versions_created == 37
        assert transaction.is_active
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 20
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 121
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CategoryRecord)
                .where(CategoryRecord.book_id == plan.target_book_id)
            )
            == 37
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CategoryVersionRecord)
                .where(CategoryVersionRecord.book_id == plan.target_book_id)
            )
            == 37
        )
    finally:
        transaction.rollback()
        session.close()

    with Session(pg_engine) as verification:
        assert verification.scalar(select(func.count()).select_from(AssetRecord)) == 16
        assert (
            verification.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 64
        )
        assert (
            verification.scalar(
                select(func.count())
                .select_from(CategoryRecord)
                .where(CategoryRecord.book_id == plan.target_book_id)
            )
            == 0
        )


def test_catalog_exact_replay_is_a_noop(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)

    with Session(pg_engine) as session, session.begin():
        receipt = stage_processing_receipt(session, plan)
        created = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        result = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )

        assert created.assets_created == 4
        assert created.accounts_created == 57
        assert created.categories_created == 37
        assert created.category_versions_created == 37
        assert result.assets_created == 0
        assert result.accounts_created == 0
        assert result.categories_created == 0
        assert result.category_versions_created == 0
        assert not session.new
        complete_processing_receipt(session, plan, receipt=receipt)


def test_parent_child_category_apply_and_replay_preserve_circular_fk_exactly(
    pg_engine,
) -> None:
    original = build_valid_fixture_plan()
    parent = original.categories[0]
    child = original.categories[1]
    child_with_parent = child.model_copy(
        update={
            "parent_category_id": parent.category_id,
            "version": child.version.model_copy(
                update={"parent_category_id": parent.category_id}
            ),
        }
    )
    categories = (
        parent,
        child_with_parent,
        *original.categories[2:],
    )
    plan = FrozenFinancialHistoryPlan.model_validate(
        {**original.model_dump(mode="python"), "categories": categories},
        strict=True,
    )
    seed_target_baseline(pg_engine, plan)

    with Session(pg_engine) as session, session.begin():
        receipt = stage_processing_receipt(session, plan)
        created = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        stored_child = session.get(
            CategoryRecord,
            (plan.target_book_id, child.category_id),
        )
        stored_version = session.get(
            CategoryVersionRecord,
            (
                plan.target_book_id,
                child.category_id,
                child.current_version_id,
            ),
        )
        assert created.categories_created == 37
        assert created.category_versions_created == 37
        assert stored_child is not None
        assert stored_child.parent_category_id == parent.category_id
        assert stored_child.current_version_id == child.current_version_id
        assert stored_version is not None
        assert stored_version.parent_category_id == parent.category_id

        replay = FrozenImportCatalogRepository(session).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        assert replay.categories_created == 0
        assert replay.category_versions_created == 0
        complete_processing_receipt(session, plan, receipt=receipt)


def test_same_frozen_account_uuid_in_another_book_is_not_a_target_collision(
    pg_engine,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    missing_account = next(
        account
        for account in plan.accounts
        if account.account_id not in BASELINE_ACCOUNT_IDS
    )
    other_book_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            BookRecord.__table__.insert(),
            {
                "book_id": other_book_id,
                "current_name": "Other book",
                "base_asset_code": None,
                "write_state": "active",
            },
        )
        connection.execute(
            AccountRecord.__table__.insert(),
            _account_row(other_book_id, missing_account),
        )

    apply_full_catalog(pg_engine, plan)

    with Session(pg_engine) as session:
        rows = tuple(
            session.scalars(
                select(AccountRecord)
                .where(AccountRecord.account_id == missing_account.account_id)
                .order_by(AccountRecord.book_id)
            )
        )
    assert {row.book_id for row in rows} == {other_book_id, plan.target_book_id}


def test_catalog_drift_is_structured_and_does_not_expose_values(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    changed = next(
        asset for asset in plan.assets if asset.asset_code in BASELINE_ASSET_CODES
    )
    protected_value = "private drift value"
    with pg_engine.begin() as connection:
        connection.execute(
            text("update assets set current_name=:value where asset_code=:asset_code"),
            {"value": protected_value, "asset_code": changed.asset_code},
        )

    with Session(pg_engine) as session:
        transaction = session.begin()
        try:
            receipt = stage_processing_receipt(session, plan)
            with pytest.raises(FrozenImportCatalogDrift) as caught:
                FrozenImportCatalogRepository(session).apply_exact_catalog(
                    plan,
                    current_receipt=receipt,
                )

            assert caught.value.entity_kind == "asset"
            assert caught.value.entity_id == changed.asset_code
            assert caught.value.field_name == "current_name"
            assert protected_value not in str(caught.value)
            assert protected_value not in repr(caught.value)
            assert not session.new
        finally:
            transaction.rollback()


def test_repository_has_only_catalog_orm_and_caller_session_boundaries() -> None:
    source = REPOSITORY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert any(module.endswith("models.catalog") for module in imported_modules)
    assert not any(
        module.endswith(
            (
                "models.event_store",
                "models.projections",
                "models.credit_cards",
                "models.privacy",
                "models.outbox",
                "models.async_projection",
            )
        )
        for module in imported_modules
    )
    assert "UnitOfWork" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    for table_name in (
        "book_event_heads",
        "ledger_events",
        "journal_transactions",
        "journal_postings",
        "reporting_lines",
        "credit_card_transactions",
        "protected_description_sidecars",
        "import_archive_manifests",
        "command_receipts",
    ):
        assert table_name in source


def test_security_definer_fence_is_fixed_owner_scoped_and_least_privilege() -> None:
    migration = FENCE_MIGRATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(migration.split()).casefold()

    revision = "v2_0013_frozen_import_fence"
    assert len(revision) <= 32
    assert f'revision = "{revision}"' in migration
    assert 'down_revision = "v2_0012_protected_content"' in migration
    assert (
        "create function public.v2_acquire_frozen_import_catalog_fence() "
        "returns void language plpgsql security definer "
        "set search_path = pg_catalog, public" in normalized
    )
    assert (
        "lock table public.assets, public.accounts, public.categories, "
        "public.category_versions in share row exclusive mode" in normalized
    )
    assert "revoke all privileges on function" in normalized
    assert "from public" in normalized
    assert "grant execute on function" in normalized
    assert "grant update" not in normalized
    assert "grant maintain" not in normalized
    downgrade = normalized[normalized.index("def downgrade()") :]
    assert "runtime = _runtime_role()" in downgrade
    assert "revoke all privileges on function" in downgrade
    assert "from public, {runtime}" in downgrade
    assert "drop function {_function}" in downgrade
    assert "irreversible" not in downgrade


def test_repository_calls_the_fence_before_exact_scans() -> None:
    source = REPOSITORY_PATH.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).casefold()
    preflight = source[
        source.index("def _preflight(") : source.index("def _validate_plan_boundary(")
    ]

    assert "select public.v2_acquire_frozen_import_catalog_fence()" in normalized
    assert "lock table" not in normalized
    assert ".with_for_update()" in source
    assert preflight.index("_CATALOG_FENCE_SQL") < preflight.index(
        "_lock_and_validate_book"
    )


def test_runtime_can_execute_only_the_security_definer_fence(
    pg_engine,
    migrated_postgres_database,
) -> None:
    with Session(pg_engine) as session, session.begin():
        function = (
            session.execute(
                text(
                    "select p.oid, p.prosecdef, p.proconfig, "
                    "pg_get_userbyid(p.proowner) as owner_name, p.proacl "
                    "from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                    "where n.nspname='public' "
                    "and p.proname='v2_acquire_frozen_import_catalog_fence' "
                    "and p.pronargs=0"
                )
            )
            .mappings()
            .one()
        )
        assert function["prosecdef"] is True
        assert function["proconfig"] == ["search_path=pg_catalog, public"]
        assert function["owner_name"] == migrated_postgres_database.owner_role
        assert (
            session.scalar(
                text("select has_function_privilege(current_user, :oid, 'EXECUTE')"),
                {"oid": function["oid"]},
            )
            is True
        )
        assert (
            session.scalar(
                text(
                    "select coalesce(bool_or(grantee=0 and privilege_type='EXECUTE'), false) "
                    "from pg_proc p cross join lateral aclexplode(p.proacl) "
                    "where p.oid=:oid"
                ),
                {"oid": function["oid"]},
            )
            is False
        )
        assert (
            session.scalar(
                text(
                    "select has_table_privilege("
                    "current_user, 'public.category_versions', 'UPDATE')"
                )
            )
            is False
        )
        assert (
            session.scalar(
                text(
                    "select has_table_privilege("
                    "current_user, 'public.category_versions', 'MAINTAIN')"
                )
            )
            is False
        )
        session.execute(text("select public.v2_acquire_frozen_import_catalog_fence()"))


def _expect_lock_timeout(
    session: Session, statement: str, values: dict[str, object]
) -> None:
    session.execute(text("set local lock_timeout = '100ms'"))
    with pytest.raises(DBAPIError) as caught:
        session.execute(text(statement), values)
    assert getattr(caught.value.orig, "sqlstate", "") == "55P03"


def _wait_until_importer_is_blocked_on_accounts_fence(
    pg_engine,
    importer_pid: int,
) -> None:
    deadline = monotonic() + 5
    with pg_engine.connect() as observer:
        while monotonic() < deadline:
            waiting = observer.scalar(
                text(
                    "select exists ("
                    "select 1 from pg_locks "
                    "where pid=:pid "
                    "and relation='public.accounts'::regclass "
                    "and mode='ShareRowExclusiveLock' "
                    "and not granted)"
                ),
                {"pid": importer_pid},
            )
            if waiting:
                return
            sleep(0.01)
    raise AssertionError("importer did not wait on the accounts catalog fence")


def test_catalog_fence_precedes_book_lock_in_dangerous_fk_interleaving(
    pg_engine,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    unexpected_account_id = uuid4()
    importer_pid: Queue[int] = Queue(maxsize=1)

    def run_import() -> None:
        with Session(pg_engine) as importer, importer.begin():
            importer.execute(text("set local statement_timeout = '10s'"))
            importer_pid.put(importer.scalar(text("select pg_backend_pid()")))
            receipt = stage_processing_receipt(importer, plan)
            FrozenImportCatalogRepository(importer).apply_exact_catalog(
                plan,
                current_receipt=receipt,
            )

    contender = Session(pg_engine)
    contender_transaction = contender.begin()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = None
        try:
            contender.execute(text("lock table accounts in row exclusive mode"))
            future = executor.submit(run_import)
            _wait_until_importer_is_blocked_on_accounts_fence(
                pg_engine,
                importer_pid.get(timeout=5),
            )
            contender.execute(
                text(
                    "insert into accounts ("
                    "book_id, account_id, asset_code, account_type, "
                    "account_subtype, system_role, current_name, status"
                    ") values ("
                    ":book_id, :account_id, 'T00', 'asset', null, null, "
                    "'Concurrent', 'active'"
                    ")"
                ),
                {
                    "book_id": plan.target_book_id,
                    "account_id": unexpected_account_id,
                },
            )
            contender_transaction.commit()
        finally:
            if contender_transaction.is_active:
                contender_transaction.rollback()
            contender.close()

        assert future is not None
        with pytest.raises(FrozenImportCatalogDrift) as caught:
            future.result(timeout=10)
        assert caught.value.entity_kind == "account"
        assert caught.value.entity_id == unexpected_account_id
        assert caught.value.field_name == "unexpected"


def test_common_fence_blocks_ordinary_asset_and_account_writes_until_rollback(
    pg_engine,
) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    importer = Session(pg_engine)
    importer_transaction = importer.begin()
    try:
        receipt = stage_processing_receipt(importer, plan)
        FrozenImportCatalogRepository(importer).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        for statement, values in (
            (
                "insert into assets ("
                "asset_code, kind, ledger_scale, input_scale, display_scale, "
                "current_name, status"
                ") values ('ZZZ', 'synthetic', 2, 2, 2, 'Concurrent', 'active')",
                {},
            ),
            (
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, account_subtype, "
                "system_role, current_name, status"
                ") values ("
                ":book_id, :account_id, 'T00', 'asset', null, null, "
                "'Concurrent', 'active'"
                ")",
                {"book_id": plan.target_book_id, "account_id": uuid4()},
            ),
        ):
            contender = Session(pg_engine)
            try:
                _expect_lock_timeout(contender, statement, values)
            finally:
                contender.rollback()
                contender.close()
    finally:
        importer_transaction.rollback()
        importer.close()

    with Session(pg_engine) as session:
        assert session.get(AssetRecord, "ZZZ") is None
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 16
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 64
        )


def test_common_fence_freezes_book_write_state_until_import_commit(pg_engine) -> None:
    plan = build_valid_fixture_plan()
    seed_target_baseline(pg_engine, plan)
    importer = Session(pg_engine)
    importer_transaction = importer.begin()
    try:
        receipt = stage_processing_receipt(importer, plan)
        FrozenImportCatalogRepository(importer).apply_exact_catalog(
            plan,
            current_receipt=receipt,
        )
        contender = Session(pg_engine)
        try:
            _expect_lock_timeout(
                contender,
                "update books set write_state='paused_integrity' where book_id=:id",
                {"id": plan.target_book_id},
            )
        finally:
            contender.rollback()
            contender.close()
        complete_processing_receipt(importer, plan, receipt=receipt)
        importer_transaction.commit()
    finally:
        if importer_transaction.is_active:
            importer_transaction.rollback()
        importer.close()

    with Session(pg_engine) as session:
        book = session.get(BookRecord, plan.target_book_id)
        assert book is not None and book.write_state == "active"
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 20
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 121
        )
