from __future__ import annotations

from fastapi import Response


SESSION_COOKIE = "ta_session"
CSRF_COOKIE = "ta_csrf"


def set_browser_session_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    secure: bool,
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
    )


def clear_browser_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, httponly=False, samesite="strict")


__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "clear_browser_session_cookies",
    "set_browser_session_cookies",
]
