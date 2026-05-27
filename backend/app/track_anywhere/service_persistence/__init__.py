from __future__ import annotations

from .catalog import ServiceCatalogPersistence
from .collectors import ServiceChangeSetCollectors
from .directory import ServiceDirectoryPersistence
from .finance import ServiceFinancePersistence
from .ledger import ServiceLedgerPersistence
from .metadata import ServiceMetadataPersistence
from .startup import ServiceStartupPersistence
from .workflow import ServiceProfilePersistence, ServiceWorkflowPersistence


class ServicePersistenceMixin(
    ServiceStartupPersistence,
    ServiceMetadataPersistence,
    ServiceCatalogPersistence,
    ServiceLedgerPersistence,
    ServiceDirectoryPersistence,
    ServiceWorkflowPersistence,
    ServiceFinancePersistence,
    ServiceProfilePersistence,
    ServiceChangeSetCollectors,
):
    pass
