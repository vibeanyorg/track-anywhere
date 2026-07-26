from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ...serialization.canonical_json import canonical_json_bytes


ProtectedContentKind: TypeAlias = Literal[
    "transaction_description",
    "transaction_narrative_v2",
    "import_archive",
]


PROTECTED_CONTENT_ALGORITHM = "AES-256-GCM+HKDF-SHA256"

_KEYRING_ENVIRONMENT_VARIABLE = "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE"
_KEYRING_FILE_VERSION = 1
_KEYRING_FILE_FIELDS = frozenset({"version", "active_key_ref", "keys"})
_MAX_KEYRING_FILE_BYTES = 64 * 1024
_MASTER_KEY_BYTES = 32
_NONCE_BYTES = 12
_CONTENT_COMMITMENT_VERSION = 1
_IMPORT_ARCHIVE_SEAL_VERSION = 1
_KEY_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", flags=re.ASCII)
_PROTECTED_CONTENT_KINDS = frozenset(
    {"transaction_description", "transaction_narrative_v2", "import_archive"}
)
_IMPORT_ARCHIVE_COUNT_KEYS = frozenset(
    {
        "classification_audit_records",
        "counterparty_records",
        "institution_metadata_records",
        "investment_activities",
        "investment_valuations",
        "omission_records",
        "uncategorized_fx_reporting_facts",
    }
)
_HKDF_SALT = b"track-anywhere:v2:protected-content:hkdf-sha256:v1"


class ProtectedContentError(ValueError):
    pass


class ProtectedContentConfigurationError(ProtectedContentError):
    pass


class ProtectedContentEncryptionError(ProtectedContentError):
    pass


class ProtectedContentDecryptionError(ProtectedContentError):
    pass


@dataclass(frozen=True, slots=True)
class SealedProtectedContent:
    key_ref: str
    algorithm: str
    content_hash: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ProtectedContentKeyring:
    _active_key_ref: str
    _keys: Mapping[str, bytes]

    @classmethod
    def from_mapping(
        cls,
        *,
        active_key_ref: str,
        keys: Mapping[str, bytes],
    ) -> ProtectedContentKeyring:
        """Construct a test keyring; runtime composition must use the secret file."""

        try:
            if not _valid_key_ref(active_key_ref):
                raise ValueError
            if not isinstance(keys, Mapping) or not keys:
                raise ValueError
            copied: dict[str, bytes] = {}
            for key_ref, master_key in keys.items():
                if not _valid_key_ref(key_ref):
                    raise ValueError
                if (
                    type(master_key) is not bytes
                    or len(master_key) != _MASTER_KEY_BYTES
                ):
                    raise ValueError
                copied[key_ref] = master_key
            if active_key_ref not in copied:
                raise ValueError
        except (TypeError, ValueError):
            raise ProtectedContentConfigurationError(
                "protected content keyring is invalid"
            ) from None
        return cls(
            _active_key_ref=active_key_ref,
            _keys=MappingProxyType(copied),
        )

    @classmethod
    def from_environment(cls) -> ProtectedContentKeyring:
        path = os.environ.get(_KEYRING_ENVIRONMENT_VARIABLE)
        if type(path) is not str or not path:
            raise ProtectedContentConfigurationError(
                "protected content keyring is not configured"
            )
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> ProtectedContentKeyring:
        payload = _read_secure_keyring_file(path)
        try:
            document = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
            if type(document) is not dict or set(document) != _KEYRING_FILE_FIELDS:
                raise ValueError
            if (
                type(document["version"]) is not int
                or document["version"] != _KEYRING_FILE_VERSION
            ):
                raise ValueError
            active_key_ref = document["active_key_ref"]
            encoded_keys = document["keys"]
            if type(encoded_keys) is not dict:
                raise ValueError
            decoded_keys: dict[str, bytes] = {}
            for key_ref, encoded_key in encoded_keys.items():
                if type(key_ref) is not str or type(encoded_key) is not str:
                    raise ValueError
                decoded = base64.b64decode(encoded_key, validate=True)
                if base64.b64encode(decoded).decode("ascii") != encoded_key:
                    raise ValueError
                decoded_keys[key_ref] = decoded
            return cls.from_mapping(
                active_key_ref=active_key_ref,
                keys=decoded_keys,
            )
        except (
            binascii.Error,
            json.JSONDecodeError,
            ProtectedContentConfigurationError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            raise ProtectedContentConfigurationError(
                "protected content keyring is invalid"
            ) from None

    @property
    def active_key_ref(self) -> str:
        return self._active_key_ref

    def _active_key(self) -> bytes:
        return self._keys[self._active_key_ref]

    def _key_for(self, key_ref: str) -> bytes:
        try:
            return self._keys[key_ref]
        except (KeyError, TypeError):
            raise ProtectedContentConfigurationError(
                "protected content key is unavailable"
            ) from None

    def __repr__(self) -> str:
        return (
            "ProtectedContentKeyring("
            f"active_key_ref={self._active_key_ref!r}, key_count={len(self._keys)})"
        )


class ProtectedContentCipher:
    __slots__ = ("_keyring", "_nonce_source")

    def __init__(
        self,
        keyring: ProtectedContentKeyring,
        nonce_source: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        if type(keyring) is not ProtectedContentKeyring or not callable(nonce_source):
            raise ProtectedContentConfigurationError(
                "protected content cipher configuration is invalid"
            )
        self._keyring = keyring
        self._nonce_source = nonce_source

    def encrypt(
        self,
        *,
        book_id: UUID,
        sidecar_id: UUID,
        kind: ProtectedContentKind,
        plaintext: bytes,
    ) -> SealedProtectedContent:
        try:
            _validate_coordinates(book_id=book_id, sidecar_id=sidecar_id, kind=kind)
            if type(plaintext) is not bytes:
                raise TypeError
            key_ref = self._keyring.active_key_ref
            book_key = _derive_book_key(self._keyring._active_key(), book_id)
            content_hash = _content_commitment(
                book_key=book_key,
                sidecar_id=sidecar_id,
                kind=kind,
                plaintext=plaintext,
            )
            try:
                nonce = self._nonce_source(_NONCE_BYTES)
            except Exception:
                raise ProtectedContentConfigurationError(
                    "protected content nonce source is invalid"
                ) from None
            if type(nonce) is not bytes or len(nonce) != _NONCE_BYTES:
                raise ProtectedContentConfigurationError(
                    "protected content nonce source is invalid"
                )
            ciphertext = AESGCM(book_key).encrypt(
                nonce,
                plaintext,
                _aad(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    kind=kind,
                    key_ref=key_ref,
                    content_hash=content_hash,
                ),
            )
            return SealedProtectedContent(
                key_ref=key_ref,
                algorithm=PROTECTED_CONTENT_ALGORITHM,
                content_hash=content_hash,
                ciphertext=ciphertext,
                nonce=nonce,
            )
        except ProtectedContentConfigurationError:
            raise
        except (OverflowError, TypeError, ValueError):
            raise ProtectedContentEncryptionError(
                "protected content could not be encrypted"
            ) from None

    def decrypt(
        self,
        *,
        book_id: UUID,
        sidecar_id: UUID,
        kind: ProtectedContentKind,
        sealed: SealedProtectedContent,
    ) -> bytes:
        try:
            _validate_coordinates(book_id=book_id, sidecar_id=sidecar_id, kind=kind)
            if type(sealed) is not SealedProtectedContent:
                raise TypeError
            if sealed.algorithm != PROTECTED_CONTENT_ALGORITHM:
                raise ValueError
            if not _valid_key_ref(sealed.key_ref):
                raise ValueError
            if type(sealed.content_hash) is not bytes or len(sealed.content_hash) != 32:
                raise ValueError
            if type(sealed.nonce) is not bytes or len(sealed.nonce) != _NONCE_BYTES:
                raise ValueError
            if type(sealed.ciphertext) is not bytes or len(sealed.ciphertext) < 16:
                raise ValueError
            book_key = _derive_book_key(
                self._keyring._key_for(sealed.key_ref), book_id
            )
            plaintext = AESGCM(book_key).decrypt(
                sealed.nonce,
                sealed.ciphertext,
                _aad(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    kind=kind,
                    key_ref=sealed.key_ref,
                    content_hash=sealed.content_hash,
                ),
            )
            if not hmac.compare_digest(
                _content_commitment(
                    book_key=book_key,
                    sidecar_id=sidecar_id,
                    kind=kind,
                    plaintext=plaintext,
                ),
                sealed.content_hash,
            ):
                raise ValueError
            return plaintext
        except (
            InvalidTag,
            OverflowError,
            ProtectedContentConfigurationError,
            TypeError,
            ValueError,
        ):
            raise ProtectedContentDecryptionError(
                "protected content could not be decrypted"
            ) from None

    def commit_archive_seal(
        self,
        *,
        book_id: UUID,
        archive_id: UUID,
        key_ref: str,
        contract_version: int,
        source_dump_hash: bytes,
        source_manifest_hash: bytes,
        card_review_hash: bytes,
        plan_hash: bytes,
        archive_content_commitment: bytes,
        record_counts: Mapping[str, int],
    ) -> bytes:
        """Seal the public import manifest without exposing a generic MAC API."""

        try:
            if type(book_id) is not UUID or type(archive_id) is not UUID:
                raise TypeError
            if not _valid_key_ref(key_ref) or type(contract_version) is not int:
                raise TypeError
            if contract_version != _IMPORT_ARCHIVE_SEAL_VERSION:
                raise ValueError
            hashes_by_name = {
                "archive_content_commitment": archive_content_commitment,
                "card_review_hash": card_review_hash,
                "plan_hash": plan_hash,
                "source_dump_hash": source_dump_hash,
                "source_manifest_hash": source_manifest_hash,
            }
            if any(
                type(value) is not bytes or len(value) != 32
                for value in hashes_by_name.values()
            ):
                raise ValueError
            if (
                type(record_counts) is not dict
                or set(record_counts) != _IMPORT_ARCHIVE_COUNT_KEYS
                or any(type(value) is not int or value < 0 for value in record_counts.values())
            ):
                raise ValueError
            book_key = _derive_book_key(self._keyring._key_for(key_ref), book_id)
            material = canonical_json_bytes(
                {
                    "archive_content_commitment": archive_content_commitment.hex(),
                    "archive_id": str(archive_id),
                    "book_id": str(book_id),
                    "card_review_hash": card_review_hash.hex(),
                    "context": "import-archive-seal",
                    "contract_version": contract_version,
                    "plan_hash": plan_hash.hex(),
                    "record_counts": dict(record_counts),
                    "source_dump_hash": source_dump_hash.hex(),
                    "source_manifest_hash": source_manifest_hash.hex(),
                    "version": _IMPORT_ARCHIVE_SEAL_VERSION,
                }
            )
            return hmac.new(book_key, material, hashlib.sha256).digest()
        except ProtectedContentConfigurationError:
            raise
        except (TypeError, ValueError):
            raise ProtectedContentEncryptionError(
                "import archive could not be sealed"
            ) from None


def _read_secure_keyring_file(path: str | os.PathLike[str]) -> bytes:
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
        if not stat.S_ISREG(state.st_mode):
            raise OSError
        if state.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ProtectedContentConfigurationError(
                "protected content keyring file permissions are insecure"
            )
        if state.st_size < 1 or state.st_size > _MAX_KEYRING_FILE_BYTES:
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            payload = stream.read(_MAX_KEYRING_FILE_BYTES + 1)
        if not payload or len(payload) > _MAX_KEYRING_FILE_BYTES:
            raise OSError
        return payload
    except ProtectedContentConfigurationError:
        raise
    except (OSError, TypeError, ValueError):
        raise ProtectedContentConfigurationError(
            "protected content keyring file is invalid"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _valid_key_ref(value: object) -> bool:
    return type(value) is str and _KEY_REF.fullmatch(value) is not None


def _validate_coordinates(
    *,
    book_id: UUID,
    sidecar_id: UUID,
    kind: ProtectedContentKind,
) -> None:
    if type(book_id) is not UUID or type(sidecar_id) is not UUID:
        raise TypeError
    if type(kind) is not str or kind not in _PROTECTED_CONTENT_KINDS:
        raise ValueError


def _derive_book_key(master_key: bytes, book_id: UUID) -> bytes:
    info = canonical_json_bytes(
        {
            "book_id": str(book_id),
            "context": "book-protected-content-key",
            "version": 1,
        }
    )
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=info,
    ).derive(master_key)


def _aad(
    *,
    book_id: UUID,
    sidecar_id: UUID,
    kind: ProtectedContentKind,
    key_ref: str,
    content_hash: bytes,
) -> bytes:
    return canonical_json_bytes(
        {
            "book_id": str(book_id),
            "content_hash": content_hash.hex(),
            "key_ref": key_ref,
            "kind": kind,
            "sidecar_id": str(sidecar_id),
        }
    )


def _content_commitment(
    *,
    book_key: bytes,
    sidecar_id: UUID,
    kind: ProtectedContentKind,
    plaintext: bytes,
) -> bytes:
    message = canonical_json_bytes(
        {
            "context": "protected-content-commitment",
            "kind": kind,
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "sidecar_id": str(sidecar_id),
            "version": _CONTENT_COMMITMENT_VERSION,
        }
    )
    return hmac.new(book_key, message, hashlib.sha256).digest()


__all__ = [
    "PROTECTED_CONTENT_ALGORITHM",
    "ProtectedContentCipher",
    "ProtectedContentConfigurationError",
    "ProtectedContentDecryptionError",
    "ProtectedContentEncryptionError",
    "ProtectedContentError",
    "ProtectedContentKeyring",
    "SealedProtectedContent",
]
