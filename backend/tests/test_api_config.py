from __future__ import annotations

from track_anywhere import api


def test_env_config_defaults_to_no_scan_bypass(monkeypatch):
    monkeypatch.delenv("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN", raising=False)
    config = api._deployment_config_from_env()
    assert config.mode == "local"
    assert config.local_dev_no_scan is False
