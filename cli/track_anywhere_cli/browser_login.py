from __future__ import annotations

from dataclasses import dataclass

from .interaction import Interaction, inform
from .oauth_login import BrowserLoginRequest, create_browser_login_request
from .pkce_callback import BrowserCallbackListener, CallbackTimeout


@dataclass(frozen=True)
class BrowserCallbackCapture:
    request: BrowserLoginRequest
    callback_value: str


def capture_browser_callback(
    *,
    web_url: str,
    client_id: str,
    scope: str,
    callback_value: str | None,
    interaction: Interaction,
) -> BrowserCallbackCapture:
    if callback_value:
        request = create_browser_login_request(web_url=web_url, client_id=client_id, scope=scope)
        return BrowserCallbackCapture(request=request, callback_value=callback_value)

    try:
        with BrowserCallbackListener() as listener:
            request = create_browser_login_request(web_url=web_url, client_id=client_id, scope=scope, redirect_uri=listener.redirect_uri)
            interaction.open_url(request.auth_url)
            inform(interaction, "Open this URL to authorize Track Anywhere CLI:")
            inform(interaction, request.auth_url)
            inform(interaction, f"Waiting for browser callback on {listener.redirect_uri} ...")
            return BrowserCallbackCapture(request=request, callback_value=listener.wait_for_callback())
    except (OSError, CallbackTimeout) as exc:
        inform(interaction, f"Local callback listener unavailable: {exc}")

    request = create_browser_login_request(web_url=web_url, client_id=client_id, scope=scope)
    interaction.open_url(request.auth_url)
    inform(interaction, "Open this URL to authorize Track Anywhere CLI:")
    inform(interaction, request.auth_url)
    return BrowserCallbackCapture(request=request, callback_value=interaction.prompt("Paste the callback URL"))
