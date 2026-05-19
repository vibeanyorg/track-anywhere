from __future__ import annotations

import os

import pytest

from .api_clients import BackendApiClient, DjangoApiClient, FastApiClient


os.environ.setdefault("TRACK_ANYWHERE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend_django.config.settings")


@pytest.fixture(params=[FastApiClient, DjangoApiClient], ids=["fastapi", "django"])
def backend_client(request) -> BackendApiClient:
    return request.param()
