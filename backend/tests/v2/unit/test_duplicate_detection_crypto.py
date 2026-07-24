from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from track_anywhere.infrastructure.crypto import (
    DuplicateDetectionConfigurationError,
    DuplicateDetectionKeyProvider,
)
from track_anywhere.infrastructure.db.repositories.entries import (
    hmac_external_reference,
    hmac_source_fingerprint,
)


KEY_FILE_ENVIRONMENT_VARIABLE = "TRACK_ANYWHERE_DUPLICATE_DETECTION_KEY_FILE"
KEY = bytes(range(32))


def _write_key_file(
    path: Path,
    key: bytes = KEY,
    *,
    mode: int = 0o400,
) -> None:
    path.write_text(base64.b64encode(key).decode("ascii"), encoding="ascii")
    path.chmod(mode)


def test_provider_loads_only_from_secure_key_file_and_hides_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "duplicate-detection.key"
    _write_key_file(path)
    monkeypatch.setenv(KEY_FILE_ENVIRONMENT_VARIABLE, os.fspath(path))

    provider = DuplicateDetectionKeyProvider.from_environment()

    external = provider.external_reference_digest(
        provider_code="merchant",
        reference_kind="provider_order",
        reference="private-order-123",
    )
    fingerprint = provider.source_fingerprint_digest(
        normalized_parts=("merchant", "private-order-123", "66000", "CNY"),
    )
    assert external == hmac_external_reference(
        key=KEY,
        provider_code="merchant",
        reference_kind="provider_order",
        reference="private-order-123",
    )
    assert fingerprint == hmac_source_fingerprint(
        key=KEY,
        normalized_parts=("merchant", "private-order-123", "66000", "CNY"),
    )
    assert external != fingerprint
    assert KEY.hex() not in repr(provider)
    assert repr(KEY) not in repr(provider)
    assert base64.b64encode(KEY).decode("ascii") not in repr(provider)
    assert not hasattr(provider, "key")


def test_missing_file_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KEY_FILE_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setenv(
        "TRACK_ANYWHERE_DUPLICATE_DETECTION_KEY",
        base64.b64encode(KEY).decode("ascii"),
    )

    with pytest.raises(
        DuplicateDetectionConfigurationError,
        match="^duplicate detection key is not configured$",
    ):
        DuplicateDetectionKeyProvider.from_environment()


@pytest.mark.parametrize(
    "payload",
    (
        b"not-base64",
        base64.b64encode(bytes(31)),
        base64.b64encode(KEY) + b"\n",
        base64.b64encode(bytes(1025)),
        b"A" * 2049,
    ),
)
def test_invalid_or_noncanonical_key_files_fail_with_redacted_error(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "invalid.key"
    path.write_bytes(payload)
    path.chmod(0o400)

    with pytest.raises(
        DuplicateDetectionConfigurationError,
        match="^duplicate detection key file is invalid$",
    ) as failure:
        DuplicateDetectionKeyProvider.from_file(path)

    assert payload.decode("ascii", errors="ignore") not in repr(failure.value)


@pytest.mark.parametrize("mode", (0o440, 0o604, 0o644))
def test_group_or_world_accessible_key_files_are_rejected(
    tmp_path: Path,
    mode: int,
) -> None:
    path = tmp_path / f"insecure-{mode:o}.key"
    _write_key_file(path, mode=mode)

    with pytest.raises(
        DuplicateDetectionConfigurationError,
        match="^duplicate detection key file is invalid$",
    ):
        DuplicateDetectionKeyProvider.from_file(path)


def test_nonregular_and_symlink_key_paths_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    target = tmp_path / "target.key"
    _write_key_file(target)
    symlink = tmp_path / "symlink.key"
    symlink.symlink_to(target)

    for path in (directory, symlink):
        with pytest.raises(
            DuplicateDetectionConfigurationError,
            match="^duplicate detection key file is invalid$",
        ):
            DuplicateDetectionKeyProvider.from_file(path)
