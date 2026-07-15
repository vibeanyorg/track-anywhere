from __future__ import annotations

from dataclasses import dataclass

from .click_common import Requester
from .interaction import Interaction, inform
from .oauth_login import (
    BrowserLoginRequest,
    OAuthMetadata,
    create_browser_login_request,
    register_public_client,
)
from .pkce_callback import BrowserCallbackListener


@dataclass(frozen=True)
class BrowserCallbackCapture:
    request: BrowserLoginRequest
    callback_value: str


def capture_browser_callback(
    *,
    metadata: OAuthMetadata,
    client_id: str | None,
    scope: str,
    interaction: Interaction,
    requester: Requester,
) -> BrowserCallbackCapture:
    with BrowserCallbackListener() as listener:
        effective_client_id = client_id or register_public_client(
            metadata=metadata,
            redirect_uri=listener.redirect_uri,
            scope=scope,
            requester=requester,
        )
        request = create_browser_login_request(
            metadata=metadata,
            client_id=effective_client_id,
            scope=scope,
            redirect_uri=listener.redirect_uri,
        )
        listener.expect_state(request.state)
        interaction.open_url(request.auth_url)
        inform(interaction, "Open this URL to authorize Track Anywhere CLI:")
        inform(interaction, request.auth_url)
        inform(
            interaction,
            f"Waiting for browser callback on {listener.redirect_uri} ...",
        )
        callback_value = listener.wait_for_callback()
    return BrowserCallbackCapture(request=request, callback_value=callback_value)
