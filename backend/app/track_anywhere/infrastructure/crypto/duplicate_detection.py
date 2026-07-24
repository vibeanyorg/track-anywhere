from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import stat
from dataclasses import dataclass, field

_KEY_FILE_ENVIRONMENT_VARIABLE = "TRACK_ANYWHERE_DUPLICATE_DETECTION_KEY_FILE"
_MIN_KEY_BYTES = 32
_MAX_KEY_BYTES = 1024
_MAX_KEY_FILE_BYTES = 2048


class DuplicateDetectionConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class DuplicateDetectionKeyProvider:
    """Own the stable, non-rotating secret for private duplicate evidence.

    Existing digests cannot be queried after changing this key because their
    tables deliberately have no key-version column.
    """

    _key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self._key) is not bytes
            or not _MIN_KEY_BYTES <= len(self._key) <= _MAX_KEY_BYTES
        ):
            raise DuplicateDetectionConfigurationError(
                "duplicate detection key file is invalid"
            )

    @classmethod
    def from_environment(cls) -> DuplicateDetectionKeyProvider:
        path = os.environ.get(_KEY_FILE_ENVIRONMENT_VARIABLE)
        if type(path) is not str or not path:
            raise DuplicateDetectionConfigurationError(
                "duplicate detection key is not configured"
            )
        return cls.from_file(path)

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
    ) -> DuplicateDetectionKeyProvider:
        payload = _read_secure_key_file(path)
        try:
            encoded = payload.decode("ascii")
            key = base64.b64decode(encoded, validate=True)
            if (
                base64.b64encode(key).decode("ascii") != encoded
                or not _MIN_KEY_BYTES <= len(key) <= _MAX_KEY_BYTES
            ):
                raise ValueError
            return cls(_key=key)
        except (binascii.Error, TypeError, UnicodeError, ValueError):
            raise DuplicateDetectionConfigurationError(
                "duplicate detection key file is invalid"
            ) from None

    def external_reference_digest(
        self,
        *,
        provider_code: str,
        reference_kind: str,
        reference: str,
    ) -> bytes:
        return _hmac_external_reference(
            key=self._key,
            provider_code=provider_code,
            reference_kind=reference_kind,
            reference=reference,
        )

    def source_fingerprint_digest(
        self,
        *,
        normalized_parts: tuple[str, ...],
    ) -> bytes:
        return _hmac_source_fingerprint(
            key=self._key,
            normalized_parts=normalized_parts,
        )

    def __repr__(self) -> str:
        return "DuplicateDetectionKeyProvider(configured=True)"


def _hmac_external_reference(
    *,
    key: bytes,
    provider_code: str,
    reference_kind: str,
    reference: str,
) -> bytes:
    return _keyed_digest(
        key=key,
        purpose=b"external-reference",
        parts=(provider_code, reference_kind, reference),
    )


def _hmac_source_fingerprint(
    *,
    key: bytes,
    normalized_parts: tuple[str, ...],
) -> bytes:
    if not normalized_parts:
        raise ValueError("source fingerprint inputs are invalid")
    return _keyed_digest(
        key=key,
        purpose=b"source-fingerprint",
        parts=normalized_parts,
    )


def _keyed_digest(
    *,
    key: bytes,
    purpose: bytes,
    parts: tuple[str, ...],
) -> bytes:
    if type(key) is not bytes or len(key) < _MIN_KEY_BYTES:
        raise ValueError("duplicate-detection key is invalid")
    if any(type(part) is not str or not part for part in parts):
        raise ValueError("duplicate-detection inputs are invalid")
    digest = hmac.new(key, digestmod=hashlib.sha256)
    digest.update(b"track-anywhere:eeg:")
    digest.update(purpose)
    digest.update(b":v1")
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _read_secure_key_file(path: str | os.PathLike[str]) -> bytes:
    descriptor: int | None = None
    try:
        path_value = os.fspath(path)
        if type(path_value) is not str or not path_value:
            raise OSError
        if stat.S_ISLNK(os.lstat(path_value).st_mode):
            raise OSError
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path_value, flags)
        state = os.fstat(descriptor)
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or not 1 <= state.st_size <= _MAX_KEY_FILE_BYTES
        ):
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            payload = stream.read(_MAX_KEY_FILE_BYTES + 1)
        if not payload or len(payload) > _MAX_KEY_FILE_BYTES:
            raise OSError
        return payload
    except (OSError, TypeError, ValueError):
        raise DuplicateDetectionConfigurationError(
            "duplicate detection key file is invalid"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = [
    "DuplicateDetectionConfigurationError",
    "DuplicateDetectionKeyProvider",
]
