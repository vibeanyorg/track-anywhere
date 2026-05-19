from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "app"))

SECRET_KEY = os.getenv("TRACK_ANYWHERE_DJANGO_SECRET_KEY", "track-anywhere-django-local-dev")
DEBUG = os.getenv("TRACK_ANYWHERE_DJANGO_DEBUG", "1").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [host.strip() for host in os.getenv("TRACK_ANYWHERE_DJANGO_ALLOWED_HOSTS", "*").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "rest_framework",
    "django_filters",
    "guardian",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.google",
    "backend_django.track_anywhere_django",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "backend_django.track_anywhere_django.middleware.TrackAnywhereAuthBridgeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend_django.config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "backend_django.config.wsgi.application"


def _django_database() -> dict[str, str]:
    raw = os.getenv("TRACK_ANYWHERE_DJANGO_DATABASE_URL") or os.getenv(
        "TRACK_ANYWHERE_DATABASE_URL",
        "sqlite:///./.local/track-anywhere.sqlite3",
    )
    if raw == "sqlite:///:memory:":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    if raw.startswith("sqlite:///"):
        database_path = raw.removeprefix("sqlite:///")
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": str((REPO_ROOT / database_path).resolve()) if database_path.startswith("./") else database_path}
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": str(REPO_ROOT / ".local" / "track-anywhere-django.sqlite3")}


DATABASES = {"default": _django_database()}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_ID = int(os.getenv("TRACK_ANYWHERE_DJANGO_SITE_ID", "1"))

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ANONYMOUS_USER_NAME = None
LOGIN_REDIRECT_URL = "/api/v1/auth/session"
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
SOCIALACCOUNT_ADAPTER = "backend_django.track_anywhere_django.allauth_adapter.TrackAnywhereSocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS: dict[str, dict] = {}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
}
