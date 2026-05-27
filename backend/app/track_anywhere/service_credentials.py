from __future__ import annotations

from .service_credential_audit import CredentialAuditUseCases
from .service_credential_issuance import CredentialIssuanceUseCases
from .service_credential_queries import CredentialQueryUseCases
from .service_credential_revocation import CredentialRevocationUseCases


class CredentialUseCases(
    CredentialQueryUseCases,
    CredentialIssuanceUseCases,
    CredentialRevocationUseCases,
    CredentialAuditUseCases,
):
    pass
