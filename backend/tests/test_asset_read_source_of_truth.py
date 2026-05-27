from __future__ import annotations

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_visible_assets_use_storage_truth_when_memory_mirror_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    service.create_account(
        token,
        {"name": "Visible Asset Account", "type": "asset", "currency": "VISASSET"},
        idempotency_key="visible-asset-account",
    )
    viewer_login = service.login_oauth_identity(
        OAuthIdentity(
            provider="test",
            subject="asset-viewer",
            email="asset-viewer@example.test",
            email_verified=True,
            name="Asset Viewer",
            picture=None,
        ),
        role="viewer",
    )
    service.ledger.accounts.clear()
    service.ledger.transactions.clear()

    viewer_assets = {asset.asset_code for asset in service.list_assets(viewer_login["credential_token"], status=None)}

    assert "VISASSET" in viewer_assets
