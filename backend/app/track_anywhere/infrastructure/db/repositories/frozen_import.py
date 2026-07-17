from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Never
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ....application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    PlannedCategory,
)
from ....serialization.canonical_json import canonical_json_bytes
from ..models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
    CategoryRecord,
    CategoryVersionRecord,
)


@dataclass(frozen=True, slots=True)
class ProcessingReceiptIdentity:
    actor_subject_id: str
    operation: str
    command_id: UUID

    def __post_init__(self) -> None:
        if (
            type(self.actor_subject_id) is not str
            or not self.actor_subject_id
            or len(self.actor_subject_id) > 128
            or type(self.operation) is not str
            or not self.operation
            or len(self.operation) > 96
            or type(self.command_id) is not UUID
        ):
            raise ValueError("processing receipt identity is invalid")


class FrozenImportCatalogDrift(RuntimeError):
    def __init__(
        self,
        *,
        entity_kind: str,
        entity_id: UUID | str,
        field_name: str,
    ) -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.field_name = field_name
        super().__init__(f"frozen import drift: {entity_kind}/{entity_id}/{field_name}")


@dataclass(frozen=True, slots=True)
class FrozenImportCatalogApplyResult:
    assets_created: int
    accounts_created: int
    categories_created: int
    category_versions_created: int


@dataclass(frozen=True, slots=True)
class _CatalogPreflight:
    missing_assets: tuple[PlannedAsset, ...]
    missing_accounts: tuple[PlannedAccount, ...]
    missing_categories: tuple[PlannedCategory, ...]


_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


def catalog_identity_sha256(
    book_id: UUID,
    *,
    asset_codes: tuple[str, ...],
    account_ids: tuple[UUID, ...],
) -> str:
    identity = {
        "account_ids": [str(account_id) for account_id in account_ids],
        "asset_codes": list(asset_codes),
        "target_book_id": str(book_id),
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


_OCCUPANCY_SQL = text(
    """
    select occupied.table_name
      from (
        select 10 as priority, 'import_archive_manifests' as table_name
         where exists (
            select 1 from import_archive_manifests where book_id = :book_id
         )
        union all
        select 20, 'protected_description_sidecars'
         where exists (
            select 1 from protected_description_sidecars where book_id = :book_id
         )
        union all
        select 30, 'credit_card_transactions'
         where exists (
            select 1 from credit_card_transactions where book_id = :book_id
         )
        union all
        select 40, 'reporting_lines'
         where exists (select 1 from reporting_lines where book_id = :book_id)
        union all
        select 50, 'journal_transactions'
         where exists (select 1 from journal_transactions where book_id = :book_id)
        union all
        select 60, 'journal_postings'
         where exists (select 1 from journal_postings where book_id = :book_id)
        union all
        select 70, 'transaction_reversals'
         where exists (select 1 from transaction_reversals where book_id = :book_id)
        union all
        select 80, 'transaction_external_references'
         where exists (
            select 1 from transaction_external_references where book_id = :book_id
         )
        union all
        select 90, 'account_balances'
         where exists (
            select 1
              from account_balances
             where book_id = :book_id
               and not (account_id = :alias_id and balance_units = 0)
         )
        union all
        select 100, 'synchronous_projection_applied_events'
         where exists (
            select 1
              from synchronous_projection_applied_events
             where book_id = :book_id
         )
        union all
        select 110, 'investment_lots'
         where exists (select 1 from investment_lots where book_id = :book_id)
        union all
        select 120, 'investment_lot_allocations'
         where exists (
            select 1 from investment_lot_allocations where book_id = :book_id
         )
        union all
        select 130, 'monthly_category_summaries'
         where exists (
            select 1 from monthly_category_summaries where book_id = :book_id
         )
        union all
        select 140, 'projection_checkpoints'
         where exists (
            select 1 from projection_checkpoints where book_id = :book_id
         )
        union all
        select 150, 'projection_generations'
         where exists (
            select 1 from projection_generations where book_id = :book_id
         )
        union all
        select 160, 'projection_dirty_periods'
         where exists (
            select 1 from projection_dirty_periods where book_id = :book_id
         )
        union all
        select 170, 'projection_failures'
         where exists (
            select 1 from projection_failures where book_id = :book_id
         )
        union all
        select 180, 'outbox_messages'
         where exists (select 1 from outbox_messages where book_id = :book_id)
        union all
        select 190, 'event_stream_heads'
         where exists (select 1 from event_stream_heads where book_id = :book_id)
        union all
        select 200, 'ledger_events'
         where exists (select 1 from ledger_events where book_id = :book_id)
      ) occupied
     order by occupied.priority
     limit 1
    """
)

_CATALOG_FENCE_SQL = text("select public.v2_acquire_frozen_import_catalog_fence()")


class FrozenImportCatalogRepository:
    """Exact catalog admission for the one-shot frozen-history command."""

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session):
            raise TypeError("a caller-owned SQLAlchemy Session is required")
        self._session = session

    def apply_exact_catalog(
        self,
        plan: FrozenFinancialHistoryPlan,
        *,
        current_receipt: ProcessingReceiptIdentity,
        expected_baseline_identity_sha256: str | None = None,
    ) -> FrozenImportCatalogApplyResult:
        if type(plan) is not FrozenFinancialHistoryPlan:
            self._drift("plan", "frozen-financial-history", "contract")
        if type(current_receipt) is not ProcessingReceiptIdentity:
            self._drift("receipt", plan.target_book_id, "current_processing")
        if expected_baseline_identity_sha256 is not None and (
            type(expected_baseline_identity_sha256) is not str
            or _LOWER_SHA256.fullmatch(expected_baseline_identity_sha256) is None
        ):
            self._drift("catalog", plan.target_book_id, "baseline_identity")
        if self._session.get_transaction() is None:
            self._drift("session", plan.target_book_id, "transaction")
        if self._session.new or self._session.dirty or self._session.deleted:
            self._drift("session", plan.target_book_id, "pending_state")

        with self._session.no_autoflush:
            preflight = self._preflight(
                plan,
                current_receipt=current_receipt,
                expected_baseline_identity_sha256=(expected_baseline_identity_sha256),
            )

        asset_records = [
            AssetRecord(
                asset_code=asset.asset_code,
                kind=asset.kind,
                ledger_scale=asset.ledger_scale,
                input_scale=asset.input_scale,
                display_scale=asset.display_scale,
                current_name=asset.current_name,
                status=asset.status,
            )
            for asset in preflight.missing_assets
        ]
        if asset_records:
            self._session.add_all(asset_records)
            self._session.flush(asset_records)

        account_records = [
            AccountRecord(
                book_id=plan.target_book_id,
                account_id=account.account_id,
                asset_code=account.asset_code,
                account_type=account.account_type,
                account_subtype=account.account_subtype,
                system_role=account.system_role,
                current_name=account.current_name,
                status=account.status,
            )
            for account in preflight.missing_accounts
        ]
        if account_records:
            self._session.add_all(account_records)
            self._session.flush(account_records)

        category_records = [
            CategoryRecord(
                book_id=plan.target_book_id,
                category_id=category.category_id,
                parent_category_id=category.parent_category_id,
                current_name=category.current_name,
                current_version_id=None,
                status=category.status,
            )
            for category in preflight.missing_categories
        ]
        if category_records:
            self._session.add_all(category_records)
            self._session.flush(category_records)
            version_records = [
                CategoryVersionRecord(
                    book_id=plan.target_book_id,
                    category_id=category.category_id,
                    category_version_id=category.version.category_version_id,
                    parent_category_id=category.version.parent_category_id,
                    name=category.version.name,
                    status=category.version.status,
                    change_reason_code=category.version.change_reason_code,
                )
                for category in preflight.missing_categories
            ]
            self._session.add_all(version_records)
            self._session.flush(version_records)
            current_versions = {
                category.category_id: category.current_version_id
                for category in preflight.missing_categories
            }
            for record in category_records:
                record.current_version_id = current_versions[record.category_id]
            self._session.flush(category_records)

        return FrozenImportCatalogApplyResult(
            assets_created=len(asset_records),
            accounts_created=len(account_records),
            categories_created=len(category_records),
            category_versions_created=len(category_records),
        )

    def _preflight(
        self,
        plan: FrozenFinancialHistoryPlan,
        *,
        current_receipt: ProcessingReceiptIdentity,
        expected_baseline_identity_sha256: str | None,
    ) -> _CatalogPreflight:
        self._validate_plan_boundary(plan, current_receipt=current_receipt)
        self._session.execute(_CATALOG_FENCE_SQL)
        self._lock_and_validate_book(plan.target_book_id)
        catalog = self._preflight_catalog(
            plan,
            expected_baseline_identity_sha256=(expected_baseline_identity_sha256),
        )
        self._validate_receipt(plan, current_receipt=current_receipt)
        alias = next(account for account in plan.accounts if account.close_after_import)
        self._validate_alias_target_balance(plan.target_book_id, alias)
        self._validate_financial_occupancy(plan.target_book_id, alias.account_id)
        self._validate_zero_head(plan.target_book_id)
        return catalog

    def _validate_plan_boundary(
        self,
        plan: FrozenFinancialHistoryPlan,
        *,
        current_receipt: ProcessingReceiptIdentity,
    ) -> None:
        command_ids = {event.command_id for event in plan.events}
        actor_ids = {event.actor_subject_id for event in plan.events}
        if command_ids != {current_receipt.command_id}:
            self._drift("receipt", current_receipt.command_id, "command_id")
        if actor_ids != {current_receipt.actor_subject_id}:
            self._drift("receipt", current_receipt.command_id, "actor_subject_id")
        aliases = tuple(
            account for account in plan.accounts if account.close_after_import
        )
        if len(aliases) != 1:
            self._drift("plan", plan.target_book_id, "close_after_import")
        if aliases[0].expected_natural_units != 0:
            self._drift("account", aliases[0].account_id, "expected_natural_units")

    def _lock_and_validate_book(self, book_id: UUID) -> None:
        book = self._session.execute(
            select(BookRecord).where(BookRecord.book_id == book_id).with_for_update()
        ).scalar_one_or_none()
        if book is None:
            self._drift("book", book_id, "missing")
        if book.write_state != "active":
            self._drift("book", book_id, "write_state")

    def _preflight_catalog(
        self,
        plan: FrozenFinancialHistoryPlan,
        *,
        expected_baseline_identity_sha256: str | None,
    ) -> _CatalogPreflight:
        planned_assets = {asset.asset_code: asset for asset in plan.assets}
        existing_assets = tuple(
            self._session.scalars(select(AssetRecord).order_by(AssetRecord.asset_code))
        )
        for record in existing_assets:
            planned = planned_assets.get(record.asset_code)
            if planned is None:
                self._drift("asset", record.asset_code, "unexpected")
            self._compare_asset(record, planned)

        planned_accounts = {account.account_id: account for account in plan.accounts}
        existing_accounts = tuple(
            self._session.scalars(
                select(AccountRecord)
                .where(AccountRecord.book_id == plan.target_book_id)
                .order_by(AccountRecord.account_id)
            )
        )
        if expected_baseline_identity_sha256 is not None:
            actual_identity_sha256 = catalog_identity_sha256(
                plan.target_book_id,
                asset_codes=tuple(record.asset_code for record in existing_assets),
                account_ids=tuple(record.account_id for record in existing_accounts),
            )
            if not hmac.compare_digest(
                actual_identity_sha256,
                expected_baseline_identity_sha256,
            ):
                self._drift("catalog", plan.target_book_id, "baseline_identity")
        for record in existing_accounts:
            planned = planned_accounts.get(record.account_id)
            if planned is None:
                self._drift("account", record.account_id, "unexpected")
            self._compare_account(record, planned)

        planned_categories = {
            category.category_id: category for category in plan.categories
        }
        existing_categories = tuple(
            self._session.scalars(
                select(CategoryRecord)
                .where(CategoryRecord.book_id == plan.target_book_id)
                .order_by(CategoryRecord.category_id)
            )
        )
        for record in existing_categories:
            planned = planned_categories.get(record.category_id)
            if planned is None:
                self._drift("category", record.category_id, "unexpected")
            self._compare_category(record, planned)

        planned_versions = {
            (category.category_id, category.version.category_version_id): category
            for category in plan.categories
        }
        existing_versions = tuple(
            self._session.scalars(
                select(CategoryVersionRecord)
                .where(CategoryVersionRecord.book_id == plan.target_book_id)
                .order_by(
                    CategoryVersionRecord.category_id,
                    CategoryVersionRecord.category_version_id,
                )
            )
        )
        for record in existing_versions:
            planned = planned_versions.get(
                (record.category_id, record.category_version_id)
            )
            if planned is None:
                self._drift(
                    "category_version",
                    record.category_version_id,
                    "unexpected",
                )
            self._compare_category_version(record, planned)

        existing_asset_codes = {record.asset_code for record in existing_assets}
        existing_account_ids = {record.account_id for record in existing_accounts}
        existing_category_ids = {record.category_id for record in existing_categories}
        existing_version_keys = {
            (record.category_id, record.category_version_id)
            for record in existing_versions
        }
        missing_categories = tuple(
            category
            for category in plan.categories
            if category.category_id not in existing_category_ids
        )
        if any(
            (category.category_id, category.current_version_id)
            not in existing_version_keys
            for category in plan.categories
            if category.category_id in existing_category_ids
        ):
            self._drift("category_version", plan.target_book_id, "missing")

        return _CatalogPreflight(
            missing_assets=tuple(
                asset
                for asset in plan.assets
                if asset.asset_code not in existing_asset_codes
            ),
            missing_accounts=tuple(
                account
                for account in plan.accounts
                if account.account_id not in existing_account_ids
            ),
            missing_categories=missing_categories,
        )

    def _validate_receipt(
        self,
        plan: FrozenFinancialHistoryPlan,
        *,
        current_receipt: ProcessingReceiptIdentity,
    ) -> None:
        rows = tuple(
            self._session.execute(
                text(
                    "select actor_subject_id, operation, command_id, status "
                    "from command_receipts where book_id=:book_id"
                ),
                {"book_id": plan.target_book_id},
            ).mappings()
        )
        if len(rows) != 1:
            self._drift("receipt", current_receipt.command_id, "current_processing")
        row = rows[0]
        if (
            row["actor_subject_id"] != current_receipt.actor_subject_id
            or row["operation"] != current_receipt.operation
            or row["command_id"] != current_receipt.command_id
            or row["status"] != "processing"
        ):
            self._drift("receipt", current_receipt.command_id, "current_processing")

    def _validate_alias_target_balance(
        self,
        book_id: UUID,
        alias: PlannedAccount,
    ) -> None:
        balance = self._session.scalar(
            text(
                "select balance_units from account_balances "
                "where book_id=:book_id and account_id=:account_id "
                "and asset_code=:asset_code"
            ),
            {
                "book_id": book_id,
                "account_id": alias.account_id,
                "asset_code": alias.asset_code,
            },
        )
        if balance is not None and int(balance) != 0:
            self._drift("account", alias.account_id, "current_balance_units")

    def _validate_financial_occupancy(self, book_id: UUID, alias_id: UUID) -> None:
        occupied = self._session.scalar(
            _OCCUPANCY_SQL,
            {"book_id": book_id, "alias_id": alias_id},
        )
        if occupied is not None:
            self._drift("occupancy", book_id, str(occupied))

    def _validate_zero_head(self, book_id: UUID) -> None:
        head = self._session.execute(
            text(
                "select last_position, last_hash from book_event_heads "
                "where book_id=:book_id"
            ),
            {"book_id": book_id},
        ).one_or_none()
        if head is None:
            self._drift("book_head", book_id, "missing")
        if head.last_position != 0:
            self._drift("book_head", book_id, "last_position")
        if bytes(head.last_hash) != bytes(32):
            self._drift("book_head", book_id, "last_hash")

    def _compare_asset(self, record: AssetRecord, planned: PlannedAsset) -> None:
        for field_name in (
            "kind",
            "ledger_scale",
            "input_scale",
            "display_scale",
            "current_name",
            "status",
        ):
            if getattr(record, field_name) != getattr(planned, field_name):
                self._drift("asset", record.asset_code, field_name)

    def _compare_account(self, record: AccountRecord, planned: PlannedAccount) -> None:
        for field_name in (
            "asset_code",
            "account_type",
            "account_subtype",
            "system_role",
            "current_name",
            "status",
        ):
            if getattr(record, field_name) != getattr(planned, field_name):
                self._drift("account", record.account_id, field_name)

    def _compare_category(
        self,
        record: CategoryRecord,
        planned: PlannedCategory,
    ) -> None:
        for field_name in (
            "parent_category_id",
            "current_name",
            "current_version_id",
            "status",
        ):
            if getattr(record, field_name) != getattr(planned, field_name):
                self._drift("category", record.category_id, field_name)

    def _compare_category_version(
        self,
        record: CategoryVersionRecord,
        planned: PlannedCategory,
    ) -> None:
        for field_name in (
            "parent_category_id",
            "name",
            "status",
            "change_reason_code",
        ):
            if getattr(record, field_name) != getattr(planned.version, field_name):
                self._drift(
                    "category_version",
                    record.category_version_id,
                    field_name,
                )

    @staticmethod
    def _drift(
        entity_kind: str,
        entity_id: UUID | str,
        field_name: str,
    ) -> Never:
        raise FrozenImportCatalogDrift(
            entity_kind=entity_kind,
            entity_id=entity_id,
            field_name=field_name,
        )


__all__ = [
    "FrozenImportCatalogApplyResult",
    "FrozenImportCatalogDrift",
    "FrozenImportCatalogRepository",
    "ProcessingReceiptIdentity",
]
