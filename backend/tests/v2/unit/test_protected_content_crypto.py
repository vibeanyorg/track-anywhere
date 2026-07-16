from __future__ import annotations

import base64
import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from track_anywhere.application.privacy import (
    ProtectedContentEnvelope,
    TransactionDescription,
)
from track_anywhere.infrastructure.crypto import (
    PROTECTED_CONTENT_ALGORITHM,
    ProtectedContentCipher,
    ProtectedContentConfigurationError,
    ProtectedContentDecryptionError,
    ProtectedContentKeyring,
)


BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
OTHER_BOOK_ID = UUID("b682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
SIDECAR_ID = UUID("11111111-2222-5333-8444-555555555555")
OTHER_SIDECAR_ID = UUID("21111111-2222-5333-8444-555555555555")
MASTER_KEY = bytes(range(32))
PLAINTEXT = b'{"purpose":"coffee"}'
PLAINTEXT_HASH_HEX = (
    "84f0033adb4c622e9a36f4a0485f18303fca5794d88bd10e05e6c1dc07f5e549"
)
FIXED_CIPHERTEXT_HEX = (
    "df1f33d285b0e22cf52a7db89dfb6042b63630a6cdbcc095d8f397b618dcf019"
    "124887d9"
)
KEYRING_ENVIRONMENT_VARIABLE = "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE"


def _keyring(*, include_second_key: bool = False) -> ProtectedContentKeyring:
    keys = {"v1": MASTER_KEY}
    if include_second_key:
        keys["v2"] = MASTER_KEY
    return ProtectedContentKeyring.from_mapping(active_key_ref="v1", keys=keys)


def _fixed_cipher(
    *, keyring: ProtectedContentKeyring | None = None, nonce: bytes = b"n" * 12
) -> ProtectedContentCipher:
    return ProtectedContentCipher(
        keyring or _keyring(),
        nonce_source=lambda size: nonce if size == 12 else b"",
    )


def _fixed_sealed(*, keyring: ProtectedContentKeyring | None = None):
    return _fixed_cipher(keyring=keyring).encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        plaintext=PLAINTEXT,
    )


def _write_keyring_file(path: Path, value: object, *, mode: int = 0o400) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(mode)


def _valid_keyring_document() -> dict[str, object]:
    return {
        "version": 1,
        "active_key_ref": "v1",
        "keys": {"v1": base64.b64encode(MASTER_KEY).decode("ascii")},
    }


def test_application_protected_content_contracts_are_frozen_and_private() -> None:
    description = TransactionDescription(
        purpose="coffee",
        transaction_memo="morning purchase",
        line_memos=("latte", None),
    )
    envelope = ProtectedContentEnvelope(
        kind="transaction_description",
        canonical_plaintext=PLAINTEXT,
    )

    assert description.line_memos == ("latte", None)
    assert envelope.canonical_plaintext == PLAINTEXT
    assert "coffee" not in repr(description)
    assert "morning purchase" not in repr(description)
    assert PLAINTEXT.decode("utf-8") not in repr(envelope)
    with pytest.raises(ValidationError):
        TransactionDescription(
            purpose=123,  # type: ignore[arg-type]
            transaction_memo=None,
            line_memos=(),
        )
    with pytest.raises(ValidationError) as invalid:
        ProtectedContentEnvelope(
            kind="transaction_description",
            canonical_plaintext="private-value",  # type: ignore[arg-type]
        )
    assert "private-value" not in str(invalid.value)
    with pytest.raises(ValidationError):
        ProtectedContentEnvelope(
            kind="unsupported",  # type: ignore[arg-type]
            canonical_plaintext=PLAINTEXT,
        )
    with pytest.raises((ValidationError, FrozenInstanceError)):
        description.purpose = "changed"  # type: ignore[misc]


def test_fixed_vector_round_trip_is_protocol_stable() -> None:
    cipher = _fixed_cipher()

    sealed = cipher.encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        plaintext=PLAINTEXT,
    )

    assert sealed.algorithm == PROTECTED_CONTENT_ALGORITHM
    assert sealed.algorithm == "AES-256-GCM+HKDF-SHA256"
    assert sealed.key_ref == "v1"
    assert sealed.nonce == b"n" * 12
    assert sealed.content_hash.hex() == PLAINTEXT_HASH_HEX
    assert sealed.ciphertext.hex() == FIXED_CIPHERTEXT_HEX
    assert (
        cipher.decrypt(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            kind="transaction_description",
            sealed=sealed,
        )
        == PLAINTEXT
    )


def test_default_nonce_source_changes_ciphertext_without_changing_content_hash() -> None:
    cipher = ProtectedContentCipher(_keyring())

    first = cipher.encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        plaintext=PLAINTEXT,
    )
    second = cipher.encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        plaintext=PLAINTEXT,
    )

    assert len(first.nonce) == len(second.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.content_hash == second.content_hash
    assert cipher.decrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        sealed=first,
    ) == cipher.decrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        sealed=second,
    ) == PLAINTEXT


@pytest.mark.parametrize(
    "tampered_field",
    ["book_id", "sidecar_id", "kind", "key_ref", "content_hash"],
)
def test_every_aad_field_change_fails_closed(tampered_field: str) -> None:
    keyring = _keyring(include_second_key=True)
    cipher = _fixed_cipher(keyring=keyring)
    sealed = _fixed_sealed(keyring=keyring)
    decrypt_arguments: dict[str, object] = {
        "book_id": BOOK_ID,
        "sidecar_id": SIDECAR_ID,
        "kind": "transaction_description",
        "sealed": sealed,
    }
    if tampered_field == "book_id":
        decrypt_arguments["book_id"] = OTHER_BOOK_ID
    elif tampered_field == "sidecar_id":
        decrypt_arguments["sidecar_id"] = OTHER_SIDECAR_ID
    elif tampered_field == "kind":
        decrypt_arguments["kind"] = "import_archive"
    elif tampered_field == "key_ref":
        decrypt_arguments["sealed"] = replace(sealed, key_ref="v2")
    else:
        decrypt_arguments["sealed"] = replace(sealed, content_hash=bytes(32))

    with pytest.raises(
        ProtectedContentDecryptionError,
        match="^protected content could not be decrypted$",
    ):
        cipher.decrypt(**decrypt_arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("tampered_field", ["ciphertext", "nonce", "algorithm"])
def test_ciphertext_envelope_tampering_fails_closed(tampered_field: str) -> None:
    cipher = _fixed_cipher()
    sealed = _fixed_sealed()
    if tampered_field == "ciphertext":
        tampered = replace(
            sealed,
            ciphertext=bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:],
        )
    elif tampered_field == "nonce":
        tampered = replace(sealed, nonce=b"o" * 12)
    else:
        tampered = replace(sealed, algorithm="unsupported")

    with pytest.raises(
        ProtectedContentDecryptionError,
        match="^protected content could not be decrypted$",
    ):
        cipher.decrypt(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            kind="transaction_description",
            sealed=tampered,
        )


def test_wrong_or_missing_key_version_fails_closed() -> None:
    sealed = _fixed_sealed()
    wrong_key_cipher = ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": bytes(reversed(range(32)))},
        )
    )
    missing_version_cipher = ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v2",
            keys={"v2": MASTER_KEY},
        )
    )

    for cipher in (wrong_key_cipher, missing_version_cipher):
        with pytest.raises(
            ProtectedContentDecryptionError,
            match="^protected content could not be decrypted$",
        ):
            cipher.decrypt(
                book_id=BOOK_ID,
                sidecar_id=SIDECAR_ID,
                kind="transaction_description",
                sealed=sealed,
            )


def test_keyring_loads_only_from_versioned_secure_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "protected-content-keyring.json"
    _write_keyring_file(path, _valid_keyring_document())
    monkeypatch.setenv(KEYRING_ENVIRONMENT_VARIABLE, os.fspath(path))

    loaded = ProtectedContentKeyring.from_environment()
    sealed = _fixed_cipher(keyring=loaded).encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="transaction_description",
        plaintext=PLAINTEXT,
    )

    assert sealed.key_ref == "v1"
    assert sealed.ciphertext.hex() == FIXED_CIPHERTEXT_HEX
    rendered = repr(loaded)
    assert MASTER_KEY.hex() not in rendered
    assert repr(MASTER_KEY) not in rendered


def test_raw_master_key_environment_variable_is_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KEYRING_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setenv(
        "TRACK_ANYWHERE_PROTECTED_CONTENT_MASTER_KEY",
        base64.b64encode(MASTER_KEY).decode("ascii"),
    )

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring is not configured$",
    ):
        ProtectedContentKeyring.from_environment()


@pytest.mark.parametrize(
    "document",
    [
        "not-json",
        {"version": 2, "active_key_ref": "v1", "keys": {}},
        {"version": 1, "active_key_ref": "v1", "keys": {"v1": "%%%"}},
        {
            "version": 1,
            "active_key_ref": "v1",
            "keys": {"v1": base64.b64encode(bytes(31)).decode("ascii")},
        },
        {
            "version": 1,
            "active_key_ref": "missing",
            "keys": {"v1": base64.b64encode(MASTER_KEY).decode("ascii")},
        },
        {
            "version": 1,
            "active_key_ref": "v1",
            "keys": {"v1": base64.b64encode(MASTER_KEY).decode("ascii")},
            "raw_master_key": "must-not-be-accepted",
        },
    ],
)
def test_malformed_keyring_documents_fail_with_nonsensitive_error(
    tmp_path: Path, document: object
) -> None:
    path = tmp_path / "bad-keyring.json"
    if type(document) is str:
        path.write_text(document, encoding="utf-8")
        path.chmod(0o400)
    else:
        _write_keyring_file(path, document)

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring is invalid$",
    ) as failure:
        ProtectedContentKeyring.from_file(path)

    rendered = repr(failure.value)
    assert "must-not-be-accepted" not in rendered
    assert MASTER_KEY.hex() not in rendered


def test_duplicate_json_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-keyring.json"
    encoded = base64.b64encode(MASTER_KEY).decode("ascii")
    path.write_text(
        '{"version":1,"version":1,"active_key_ref":"v1",'
        f'"keys":{{"v1":"{encoded}"}}}}',
        encoding="utf-8",
    )
    path.chmod(0o400)

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring is invalid$",
    ):
        ProtectedContentKeyring.from_file(path)


@pytest.mark.parametrize("mode", [0o440, 0o604, 0o644])
def test_group_or_world_accessible_keyring_is_rejected(
    tmp_path: Path, mode: int
) -> None:
    path = tmp_path / f"insecure-{mode:o}.json"
    _write_keyring_file(path, _valid_keyring_document(), mode=mode)

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring file permissions are insecure$",
    ):
        ProtectedContentKeyring.from_file(path)


def test_nonregular_and_symlink_keyring_paths_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    target = tmp_path / "target.json"
    _write_keyring_file(target, _valid_keyring_document())
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(target)

    for path in (directory, symlink):
        with pytest.raises(
            ProtectedContentConfigurationError,
            match="^protected content keyring file is invalid$",
        ):
            ProtectedContentKeyring.from_file(path)


def test_invalid_mapping_and_nonce_source_fail_without_secret_material() -> None:
    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring is invalid$",
    ) as invalid_mapping:
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": b"private-but-too-short"},
        )
    cipher = ProtectedContentCipher(_keyring(), nonce_source=lambda _size: b"short")

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content nonce source is invalid$",
    ) as invalid_nonce:
        cipher.encrypt(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            kind="transaction_description",
            plaintext=PLAINTEXT,
        )

    rendered = repr(invalid_mapping.value) + repr(invalid_nonce.value)
    assert "private-but-too-short" not in rendered
    assert "short" not in rendered


def test_nonce_source_exception_is_normalized_without_its_message() -> None:
    def failing_nonce_source(_size: int) -> bytes:
        raise RuntimeError("private nonce provider detail")

    cipher = ProtectedContentCipher(_keyring(), nonce_source=failing_nonce_source)

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content nonce source is invalid$",
    ) as failure:
        cipher.encrypt(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            kind="transaction_description",
            plaintext=PLAINTEXT,
        )

    assert "private nonce provider detail" not in repr(failure.value)


def test_sealed_repr_and_decryption_errors_do_not_leak_protected_material() -> None:
    cipher = _fixed_cipher()
    sealed = _fixed_sealed()
    tampered = replace(sealed, ciphertext=bytes(len(sealed.ciphertext)))

    rendered_sealed = repr(sealed)
    assert PLAINTEXT.decode("utf-8") not in rendered_sealed
    assert sealed.ciphertext.hex() not in rendered_sealed
    assert repr(sealed.ciphertext) not in rendered_sealed
    assert sealed.nonce.hex() not in rendered_sealed
    assert repr(sealed.nonce) not in rendered_sealed

    with pytest.raises(ProtectedContentDecryptionError) as failure:
        cipher.decrypt(
            book_id=BOOK_ID,
            sidecar_id=SIDECAR_ID,
            kind="transaction_description",
            sealed=tampered,
        )
    rendered_failure = repr(failure.value)
    for protected_value in (
        PLAINTEXT.decode("utf-8"),
        sealed.ciphertext.hex(),
        repr(sealed.ciphertext),
        sealed.nonce.hex(),
        repr(sealed.nonce),
        MASTER_KEY.hex(),
        repr(MASTER_KEY),
    ):
        assert protected_value not in rendered_failure
