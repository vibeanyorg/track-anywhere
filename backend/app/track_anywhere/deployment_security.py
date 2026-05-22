from __future__ import annotations

from dataclasses import dataclass

from .errors import SecurityPreconditionFailed


@dataclass(frozen=True)
class DeploymentSecurityConfig:
    mode: str = "local"
    tls_enabled: bool = False
    key_provider_configured: bool = False
    encrypted_volume_documented: bool = False
    backup_encryption_documented: bool = False
    attachment_scanner_available: bool = False
    debug_raw_payload: bool = False
    local_dev_no_scan: bool = False


def validate_startup_security(config: DeploymentSecurityConfig) -> list[str]:
    warnings: list[str] = []
    if config.local_dev_no_scan and config.mode != "local":
        raise SecurityPreconditionFailed("local_dev_no_scan is only allowed in local mode")
    if config.mode == "local":
        if config.debug_raw_payload:
            warnings.append("debug raw payload override enabled in local mode")
        if config.local_dev_no_scan:
            warnings.append("attachment scanner bypass enabled in local mode")
        return warnings
    if not config.tls_enabled:
        raise SecurityPreconditionFailed("non-local deployment requires TLS")
    if not (config.key_provider_configured or config.encrypted_volume_documented):
        raise SecurityPreconditionFailed("non-local deployment requires key provider or encrypted-volume constraint")
    if not config.backup_encryption_documented:
        raise SecurityPreconditionFailed("non-local deployment requires backup encryption/restoration plan")
    if not config.attachment_scanner_available:
        raise SecurityPreconditionFailed("non-local deployment requires attachment scanner")
    if config.debug_raw_payload:
        raise SecurityPreconditionFailed("raw payload debug override is forbidden in non-local mode")
    return warnings
