from __future__ import annotations

from .db_migrations import run_migrations
from .domain_storage_loaders import DomainStorageLoaders
from .domain_storage_writers import DomainStorageWriters
from . import storage_auth_models as _storage_auth_models
from .storage_annotation_writers import AnnotationStorageWriters
from .storage_backoffice_reads import BackofficeReadStorage
from .storage_catalog_reads import CatalogReadStorage
from .storage_changes import StartupMaintenanceChanges
from .storage_counterparties import CounterpartyStorageMixin
from .storage_draft_reads import DraftReadStorage
from .storage_engine import create_database_engine, database_url_from_env
from .storage_json import new_owner_token, to_jsonable
from .storage_ledger_reads import LedgerReadStorage
from .storage_loaders import StorageLoaders
from .storage_payment_instruments import PaymentInstrumentStorageMixin
from .storage_payment_profiles import PaymentProfileStorageMixin
from .storage_partial import PartialStorageWriters
from .storage_password_accounts import StoragePasswordAccountRepository
from .storage_read_cache import StorageReadCache
from .storage_snapshot_loader import StorageSnapshotLoader
from .storage_system import SystemStatusStorageMixin
from .storage_uow import StorageUnitOfWork
from .storage_models import Base
from .storage_writers import StorageWriters
from sqlalchemy.orm import sessionmaker

_storage_auth_models.CredentialRecord


class OrmStorage(
    StorageReadCache,
    PartialStorageWriters,
    CounterpartyStorageMixin,
    PaymentInstrumentStorageMixin,
    PaymentProfileStorageMixin,
    DomainStorageLoaders,
    StorageSnapshotLoader,
    SystemStatusStorageMixin,
    StorageLoaders,
    BackofficeReadStorage,
    CatalogReadStorage,
    DraftReadStorage,
    LedgerReadStorage,
    AnnotationStorageWriters,
    DomainStorageWriters,
    StorageWriters,
):
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or database_url_from_env()
        self.engine = create_database_engine(self.database_url)
        run_migrations(self.engine, Base.metadata)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def unit_of_work(self) -> StorageUnitOfWork:
        return StorageUnitOfWork(self)

    def password_account_repository(self) -> StoragePasswordAccountRepository:
        return StoragePasswordAccountRepository(self.session_factory)

    def save_startup_maintenance(self, changes: StartupMaintenanceChanges) -> None:
        with self.unit_of_work() as uow:
            uow.state.delete_app_state("owner_token")
            uow.books.save(changes.book_changes.books, changes.book_changes.members)
            uow.assets.save(changes.assets)
            uow.categories.save(changes.categories)
            uow.categories.save_history(
                aliases=changes.category_history.aliases,
                versions=changes.category_history.versions,
                events=changes.category_history.events,
            )
            uow.credentials.save(changes.metadata.credentials)
            uow.audit.save_events(changes.metadata.audit_events)
            uow.idempotency.save_receipts(changes.metadata.idempotency_receipts)

__all__ = [
    "Base",
    "OrmStorage",
    "create_database_engine",
    "database_url_from_env",
    "new_owner_token",
    "to_jsonable",
]
