from __future__ import annotations

import os

from .attachments import ClamAVScanner
from .errors import SecurityPreconditionFailed
from .security import DeploymentSecurityConfig


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def deployment_config_from_env() -> DeploymentSecurityConfig:
    mode = os.getenv("TRACK_ANYWHERE_MODE", "local")
    scanner_host = os.getenv("TRACK_ANYWHERE_CLAMAV_HOST", "").strip()
    return DeploymentSecurityConfig(
        mode=mode,
        tls_enabled=env_bool("TRACK_ANYWHERE_TLS"),
        key_provider_configured=env_bool("TRACK_ANYWHERE_KEY_PROVIDER"),
        encrypted_volume_documented=env_bool("TRACK_ANYWHERE_ENCRYPTED_VOLUME"),
        backup_encryption_documented=env_bool("TRACK_ANYWHERE_BACKUP_DOC"),
        attachment_scanner_available=bool(scanner_host),
        debug_raw_payload=env_bool("TRACK_ANYWHERE_DEBUG_RAW_PAYLOAD"),
        local_dev_no_scan=env_bool("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN"),
    )


def attachment_scanner_from_env() -> ClamAVScanner | None:
    host = os.getenv("TRACK_ANYWHERE_CLAMAV_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.getenv("TRACK_ANYWHERE_CLAMAV_PORT", "3310"))
    except ValueError as exc:
        raise SecurityPreconditionFailed("TRACK_ANYWHERE_CLAMAV_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SecurityPreconditionFailed("TRACK_ANYWHERE_CLAMAV_PORT must be between 1 and 65535")
    return ClamAVScanner(host, port)


def allowed_origins_from_env() -> tuple[str, ...]:
    raw = os.getenv("TRACK_ANYWHERE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return origins or ("http://localhost:3000",)


def auth_cookie_secure_from_env(*, mode: str) -> bool:
    return env_bool("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", default=mode != "local")
