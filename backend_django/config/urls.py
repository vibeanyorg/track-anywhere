from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from backend_django.track_anywhere_django.api import api
from backend_django.track_anywhere_django.urls import router as backoffice_router


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", api.urls),
    path("api/v1/backoffice/", include(backoffice_router.urls)),
]
