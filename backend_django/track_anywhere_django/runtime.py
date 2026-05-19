from __future__ import annotations

import os

from track_anywhere.auth_oauth import auth_settings_from_env
from track_anywhere.platform_auth import PlatformKeyExchange
from track_anywhere.security import BrowserSessionStore, DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def deployment_config_from_env() -> DeploymentSecurityConfig:
    mode = os.getenv("TRACK_ANYWHERE_MODE", "local")
    return DeploymentSecurityConfig(
        mode=mode,
        tls_enabled=_env_bool("TRACK_ANYWHERE_TLS"),
        key_provider_configured=_env_bool("TRACK_ANYWHERE_KEY_PROVIDER"),
        encrypted_volume_documented=_env_bool("TRACK_ANYWHERE_ENCRYPTED_VOLUME"),
        backup_encryption_documented=_env_bool("TRACK_ANYWHERE_BACKUP_DOC"),
        attachment_scanner_available=_env_bool("TRACK_ANYWHERE_ATTACHMENT_SCANNER", default=mode == "local"),
        debug_raw_payload=_env_bool("TRACK_ANYWHERE_DEBUG_RAW_PAYLOAD"),
        local_dev_no_scan=_env_bool("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN"),
    )


def allowed_origins() -> tuple[str, ...]:
    raw = os.getenv("TRACK_ANYWHERE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return origins or ("http://localhost:3000",)


service = FinanceService(deployment_config_from_env())
browser_sessions = BrowserSessionStore()
ALLOWED_ORIGINS = allowed_origins()
auth_settings = auth_settings_from_env(mode=service.config.mode)
platform_key_exchange = PlatformKeyExchange()
