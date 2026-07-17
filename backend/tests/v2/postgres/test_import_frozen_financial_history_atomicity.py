from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.postgres.test_frozen_import_catalog import (
    BASELINE_ACCOUNT_IDS,
    BASELINE_ASSET_CODES,
    _account_row,
    seed_target_baseline,
)
from backend.tests.v2.postgres.test_import_frozen_financial_history import (
    _cipher,
    _fixed_synthetic_plan,
)
import track_anywhere.application.imports.import_frozen_financial_history as frozen_import
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    plan_sha256,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.privacy.service import ProtectedContentService
from track_anywhere.infrastructure.db.command_receipts import (
    CommandReceiptRepository,
)
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
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
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.repositories.frozen_import import (
    FrozenImportCatalogDrift,
    FrozenImportCatalogRepository,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.synchronous import (
    SynchronousProjector,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


class InjectedImportFailure(RuntimeError):
    pass


@pytest.mark.parametrize("baseline_accounts", (63, 65))
def test_wrong_exact_catalog_baseline_aborts_and_rolls_back_before_sidecars(
    pg_engine,
    migrated_postgres_database,
    baseline_accounts: int,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        with admin_engine.begin() as connection:
            if baseline_accounts == 63:
                removed_id = min(BASELINE_ACCOUNT_IDS, key=str)
                result = connection.execute(
                    AccountRecord.__table__.delete().where(
                        AccountRecord.book_id == plan.target_book_id,
                        AccountRecord.account_id == removed_id,
                    )
                )
                assert result.rowcount == 1
            else:
                extra = next(
                    account
                    for account in plan.accounts
                    if account.account_id not in BASELINE_ACCOUNT_IDS
                    and account.asset_code in BASELINE_ASSET_CODES
                )
                connection.execute(
                    AccountRecord.__table__.insert(),
                    _account_row(plan.target_book_id, extra),
                )
    finally:
        admin_engine.dispose()

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(
        frozen_import.FrozenFinancialHistoryImportError,
        match="fixed contract",
    ):
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            protected_content_cipher=_cipher(),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, plan.target_book_id)
        assert head is not None
        assert (head.last_position, head.last_hash) == (0, bytes(32))
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == baseline_accounts
        )
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 16
        for model in (
            CategoryRecord,
            LedgerEventRecord,
            JournalTransactionRecord,
            ProtectedDescriptionSidecarRecord,
            ImportArchiveManifestRecord,
            CommandReceiptRecord,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("substitution", ("account", "asset"))
def test_equal_count_catalog_identity_substitution_aborts_atomically(
    pg_engine,
    migrated_postgres_database,
    monkeypatch: pytest.MonkeyPatch,
    substitution: str,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    expected_identity = {
        "account_ids": [
            str(account_id) for account_id in sorted(BASELINE_ACCOUNT_IDS, key=str)
        ],
        "asset_codes": sorted(BASELINE_ASSET_CODES),
        "target_book_id": str(plan.target_book_id),
    }
    monkeypatch.setattr(
        frozen_import,
        "FROZEN_IMPORT_PLAN_HASH",
        plan_sha256(plan),
    )
    monkeypatch.setattr(
        frozen_import,
        "FROZEN_IMPORT_CATALOG_IDENTITY_SHA256",
        hashlib.sha256(canonical_json_bytes(expected_identity)).hexdigest(),
    )

    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        with admin_engine.begin() as connection:
            if substitution == "account":
                removed_id = min(BASELINE_ACCOUNT_IDS, key=str)
                extra = next(
                    account
                    for account in plan.accounts
                    if account.account_id not in BASELINE_ACCOUNT_IDS
                    and account.asset_code in BASELINE_ASSET_CODES
                )
                deleted = connection.execute(
                    AccountRecord.__table__.delete().where(
                        AccountRecord.book_id == plan.target_book_id,
                        AccountRecord.account_id == removed_id,
                    )
                )
                assert deleted.rowcount == 1
                connection.execute(
                    AccountRecord.__table__.insert(),
                    _account_row(plan.target_book_id, extra),
                )
            else:
                removed_code = min(
                    (
                        asset_code
                        for asset_code in BASELINE_ASSET_CODES
                        if asset_code
                        not in {
                            account.asset_code
                            for account in plan.accounts
                            if account.account_id in BASELINE_ACCOUNT_IDS
                        }
                    ),
                )
                extra = next(
                    asset
                    for asset in plan.assets
                    if asset.asset_code not in BASELINE_ASSET_CODES
                )
                deleted = connection.execute(
                    AssetRecord.__table__.delete().where(
                        AssetRecord.asset_code == removed_code
                    )
                )
                assert deleted.rowcount == 1
                connection.execute(
                    AssetRecord.__table__.insert(),
                    {
                        "asset_code": extra.asset_code,
                        "kind": extra.kind,
                        "ledger_scale": extra.ledger_scale,
                        "input_scale": extra.input_scale,
                        "display_scale": extra.display_scale,
                        "current_name": extra.current_name,
                        "status": extra.status,
                    },
                )
    finally:
        admin_engine.dispose()

    with Session(pg_engine) as session:
        initial_assets = tuple(
            session.scalars(
                select(AssetRecord.asset_code).order_by(AssetRecord.asset_code)
            )
        )
        initial_accounts = tuple(
            session.scalars(
                select(AccountRecord.account_id)
                .where(AccountRecord.book_id == plan.target_book_id)
                .order_by(AccountRecord.account_id)
            )
        )
    assert len(initial_assets) == 16
    assert len(initial_accounts) == 64

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(FrozenImportCatalogDrift) as caught:
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            protected_content_cipher=_cipher(),
        )
    assert caught.value.entity_kind == "catalog"
    assert caught.value.entity_id == plan.target_book_id
    assert caught.value.field_name == "baseline_identity"

    with Session(pg_engine) as session:
        assert (
            tuple(
                session.scalars(
                    select(AssetRecord.asset_code).order_by(AssetRecord.asset_code)
                )
            )
            == initial_assets
        )
        assert (
            tuple(
                session.scalars(
                    select(AccountRecord.account_id)
                    .where(AccountRecord.book_id == plan.target_book_id)
                    .order_by(AccountRecord.account_id)
                )
            )
            == initial_accounts
        )
        for model in (
            CategoryRecord,
            LedgerEventRecord,
            JournalTransactionRecord,
            ProtectedDescriptionSidecarRecord,
            ImportArchiveManifestRecord,
            CommandReceiptRecord,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(
    "failure_point",
    (
        "catalog",
        "sidecar",
        "archive_create",
        "event_store",
        "projector",
        "finalizer",
        "archive_verify",
        "receipt_complete",
    ),
)
def test_every_import_failure_rolls_back_to_the_exact_baseline(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    alias = next(account for account in plan.accounts if account.close_after_import)

    if failure_point == "catalog":
        original = FrozenImportCatalogRepository.apply_exact_catalog

        def fail_after_catalog(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InjectedImportFailure("catalog")

        monkeypatch.setattr(
            FrozenImportCatalogRepository,
            "apply_exact_catalog",
            fail_after_catalog,
        )
    elif failure_point == "sidecar":
        original = ProtectedContentService.create_or_exact_verify
        calls = 0

        def fail_on_sidecar(self, *args, **kwargs):
            nonlocal calls
            result = original(self, *args, **kwargs)
            calls += 1
            if calls == 17:
                raise InjectedImportFailure("sidecar")
            return result

        monkeypatch.setattr(
            ProtectedContentService,
            "create_or_exact_verify",
            fail_on_sidecar,
        )
    elif failure_point == "archive_create":
        original = ProtectedContentService.create_or_exact_verify_archive

        def fail_after_archive(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InjectedImportFailure("archive_create")

        monkeypatch.setattr(
            ProtectedContentService,
            "create_or_exact_verify_archive",
            fail_after_archive,
        )
    elif failure_point == "event_store":
        original = PostgresEventStore._append_batch

        def fail_after_append(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InjectedImportFailure("event_store")

        monkeypatch.setattr(PostgresEventStore, "_append_batch", fail_after_append)
    elif failure_point == "projector":
        original = SynchronousProjector.apply_stored
        calls = 0

        def fail_on_projector(self, *args, **kwargs):
            nonlocal calls
            result = original(self, *args, **kwargs)
            calls += 1
            if calls == 57:
                raise InjectedImportFailure("projector")
            return result

        monkeypatch.setattr(SynchronousProjector, "apply_stored", fail_on_projector)
    elif failure_point == "finalizer":
        original = frozen_import._verify_card_balances

        def fail_after_card_verification(*args, **kwargs):
            original(*args, **kwargs)
            raise InjectedImportFailure("finalizer")

        monkeypatch.setattr(
            frozen_import,
            "_verify_card_balances",
            fail_after_card_verification,
        )
    elif failure_point == "archive_verify":
        original = ProtectedContentService.verify_archive_manifest

        def fail_after_archive_verification(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InjectedImportFailure("archive_verify")

        monkeypatch.setattr(
            ProtectedContentService,
            "verify_archive_manifest",
            fail_after_archive_verification,
        )
    else:
        original = CommandReceiptRepository.complete

        def fail_after_receipt_complete(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise InjectedImportFailure("receipt_complete")

        monkeypatch.setattr(
            CommandReceiptRepository,
            "complete",
            fail_after_receipt_complete,
        )

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(InjectedImportFailure, match=failure_point):
        frozen_import.import_frozen_financial_history(
            plan,
            expected_plan_hash=plan_sha256(plan),
            raw_key="frozen-import-receipt",
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            protected_content_cipher=_cipher(),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, plan.target_book_id)
        stored_alias = session.get(
            AccountRecord,
            (plan.target_book_id, alias.account_id),
        )
        assert head is not None
        assert (head.last_position, head.last_hash) == (0, bytes(32))
        assert stored_alias is not None and stored_alias.status == "active"
        assert session.scalar(select(func.count()).select_from(AssetRecord)) == 16
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
            )
            == 64
        )
        for model in (
            CategoryRecord,
            LedgerEventRecord,
            JournalTransactionRecord,
            ProtectedDescriptionSidecarRecord,
            ImportArchiveManifestRecord,
            CommandReceiptRecord,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0
