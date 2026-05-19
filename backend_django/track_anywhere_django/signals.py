from __future__ import annotations

from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_migrate

from .auth_bridge import revoke_browser_session_for_request
from .roles import ensure_role_groups


def install_signal_handlers(sender) -> None:
    post_migrate.connect(_ensure_role_groups, sender=sender, dispatch_uid="track_anywhere.ensure_role_groups")
    user_logged_out.connect(_clear_track_anywhere_session, dispatch_uid="track_anywhere.clear_auth_bridge")


def _ensure_role_groups(**_kwargs) -> None:
    ensure_role_groups()


def _clear_track_anywhere_session(sender, request, user, **_kwargs) -> None:
    if request is not None:
        revoke_browser_session_for_request(request)
