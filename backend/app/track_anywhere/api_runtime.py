from __future__ import annotations

import os

from .auth_oauth import auth_settings_from_env, build_oauth_registry
from .security import BrowserSessionStore, DeploymentSecurityConfig
from .service import FinanceService


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _deployment_config_from_env() -> DeploymentSecurityConfig:
    mode = os.getenv("TRACK_ANYWHERE_MODE", "local")
    return DeploymentSecurityConfig(
        mode=mode,
        tls_enabled=_env_bool("TRACK_ANYWHERE_TLS"),
        key_provider_configured=_env_bool("TRACK_ANYWHERE_KEY_PROVIDER"),
        encrypted_volume_documented=_env_bool("TRACK_ANYWHERE_ENCRYPTED_VOLUME"),
        backup_encryption_documented=_env_bool("TRACK_ANYWHERE_BACKUP_DOC"),
        attachment_scanner_available=_env_bool(
            "TRACK_ANYWHERE_ATTACHMENT_SCANNER",
            default=mode == "local",
        ),
        debug_raw_payload=_env_bool("TRACK_ANYWHERE_DEBUG_RAW_PAYLOAD"),
        local_dev_no_scan=_env_bool("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN"),
    )


def _allowed_origins() -> tuple[str, ...]:
    raw = os.getenv("TRACK_ANYWHERE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return origins or ("http://localhost:3000",)


service = FinanceService(_deployment_config_from_env(), persist_on_initialize=False)
browser_sessions = BrowserSessionStore()
password_accounts = service.create_password_account_store()
ALLOWED_ORIGINS = _allowed_origins()
auth_settings = auth_settings_from_env(mode=service.config.mode)
oauth_registry = build_oauth_registry(auth_settings)
platform_key_exchange = service.platform_key_exchange


def auth_cookie_secure() -> bool:
    return _env_bool("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", default=service.config.mode != "local")
