from __future__ import annotations

from .api_config import allowed_origins_from_env, auth_cookie_secure_from_env, deployment_config_from_env
from .auth_oauth import auth_settings_from_env, build_oauth_registry


deployment_config = deployment_config_from_env()
ALLOWED_ORIGINS = allowed_origins_from_env()
auth_settings = auth_settings_from_env(mode=deployment_config.mode)
oauth_registry = build_oauth_registry(auth_settings)


def auth_cookie_secure() -> bool:
    return auth_cookie_secure_from_env(mode=deployment_config.mode)
