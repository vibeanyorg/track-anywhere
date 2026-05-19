from __future__ import annotations

from .auth_bridge import clear_browser_session_cookies, ensure_browser_session_for_django_user, set_browser_session_cookies
from .runtime import service


class TrackAnywhereAuthBridgeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, "_track_anywhere_clear_auth_cookies", False):
            clear_browser_session_cookies(response)
            return response

        if _should_bridge_response(request):
            browser_session = ensure_browser_session_for_django_user(request)
            if browser_session is not None:
                session_id, csrf_token = browser_session
                set_browser_session_cookies(
                    response,
                    session_id=session_id,
                    csrf_token=csrf_token,
                    secure=service.config.mode != "local",
                )
        return response


def _should_bridge_response(request) -> bool:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    return request.path.startswith(("/accounts/", "/api/"))
