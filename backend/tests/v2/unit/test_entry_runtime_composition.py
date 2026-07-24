from __future__ import annotations

import base64

from sqlalchemy import create_engine

from track_anywhere.api import create_app
from track_anywhere.api.dependencies import (
    DUPLICATE_DETECTION_KEY_FILE_ENV,
    _configured_duplicate_detection_key_provider,
)
from track_anywhere.infrastructure.crypto import DuplicateDetectionKeyProvider


def test_duplicate_provider_configuration_is_optional_and_fails_closed(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv(DUPLICATE_DETECTION_KEY_FILE_ENV, raising=False)
    assert _configured_duplicate_detection_key_provider() is None

    monkeypatch.setenv(
        DUPLICATE_DETECTION_KEY_FILE_ENV,
        str(tmp_path / "missing-key"),
    )
    assert _configured_duplicate_detection_key_provider() is None

    key_file = tmp_path / "duplicate-key"
    key_file.write_bytes(base64.b64encode(bytes(range(32))))
    key_file.chmod(0o400)
    monkeypatch.setenv(DUPLICATE_DETECTION_KEY_FILE_ENV, str(key_file))
    configured = _configured_duplicate_detection_key_provider()
    assert isinstance(configured, DuplicateDetectionKeyProvider)
    assert "range(32)" not in repr(configured)


def test_create_app_preserves_explicit_duplicate_provider_override() -> None:
    engine = create_engine(
        "postgresql+psycopg://track_anywhere_runtime:test@127.0.0.1:9/contract"
    )
    provider = DuplicateDetectionKeyProvider(bytes(range(32)))
    try:
        application = create_app(
            engine=engine,
            expected_runtime_role="track_anywhere_runtime",
            duplicate_detection_key_provider=provider,
        )
        runtime = application.state.runtime_dependencies
        assert runtime is not None
        assert runtime.duplicate_detection_key_provider is provider
    finally:
        engine.dispose()
