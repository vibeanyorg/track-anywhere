from __future__ import annotations

import os

from .security import DeploymentSecurityConfig


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def deployment_config_from_env() -> DeploymentSecurityConfig:
    mode = os.getenv("TRACK_ANYWHERE_MODE", "local")
    return DeploymentSecurityConfig(
        mode=mode,
        tls_enabled=env_bool("TRACK_ANYWHERE_TLS"),
        key_provider_configured=env_bool("TRACK_ANYWHERE_KEY_PROVIDER"),
        encrypted_volume_documented=env_bool("TRACK_ANYWHERE_ENCRYPTED_VOLUME"),
        backup_encryption_documented=env_bool("TRACK_ANYWHERE_BACKUP_DOC"),
        attachment_scanner_available=env_bool(
            "TRACK_ANYWHERE_ATTACHMENT_SCANNER",
            default=mode == "local",
        ),
        debug_raw_payload=env_bool("TRACK_ANYWHERE_DEBUG_RAW_PAYLOAD"),
        local_dev_no_scan=env_bool("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN"),
    )


def allowed_origins_from_env() -> tuple[str, ...]:
    raw = os.getenv("TRACK_ANYWHERE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return origins or ("http://localhost:3000",)


def auth_cookie_secure_from_env(*, mode: str) -> bool:
    return env_bool("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", default=mode != "local")
