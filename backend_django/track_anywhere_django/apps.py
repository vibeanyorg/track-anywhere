from __future__ import annotations

from django.apps import AppConfig


class TrackAnywhereDjangoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend_django.track_anywhere_django"
    verbose_name = "Track Anywhere"

    def ready(self) -> None:
        from .signals import install_signal_handlers

        install_signal_handlers(self)
