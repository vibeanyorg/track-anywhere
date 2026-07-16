from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID
from pathlib import Path
from types import MappingProxyType

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from track_anywhere.application.privacy.service import (
    ProtectedContentConflict,
    ProtectedContentService,
)
from track_anywhere.application.privacy.protected_content import TransactionDescription
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentConfigurationError,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ImportArchiveManifestSnapshot,
    ProtectedContentRepository,
    ProtectedContentSnapshot,
)
from track_anywhere.queries.journal import JournalItem, JournalPage
from track_anywhere.queries.protected_content import (
    ImportArchiveExport,
    ImportArchiveMetadata,
    ProtectedContentErased,
    ProtectedContentUnavailable,
)


BOOK_ID = UUID("11111111-1111-4111-8111-111111111111")
TRANSACTION_ID = UUID("22222222-2222-4222-8222-222222222222")
SIDECAR_ID = UUID("33333333-3333-4333-8333-333333333333")
MASTER_KEY = bytes(range(32))
HASHES = {
    "source_dump_hash": b"d" * 32,
    "source_manifest_hash": b"m" * 32,
    "card_review_hash": b"c" * 32,
    "plan_hash": b"p" * 32,
}
RECORD_COUNTS = {
    "classification_audit_records": 43,
    "investment_activities": 6,
    "investment_valuations": 0,
    "uncategorized_fx_reporting_facts": 5,
    "institution_metadata_records": 3,
    "counterparty_records": 2,
    "omission_records": 1,
}


class _SessionSentinel:
    pass


SESSION = _SessionSentinel()


def _cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": MASTER_KEY},
        ),
        nonce_source=lambda size: b"n" * size,
    )


def _snapshot(
    plaintext: bytes,
    *,
    sidecar_id: UUID = SIDECAR_ID,
) -> ProtectedContentSnapshot:
    sealed = _cipher().encrypt(
        book_id=BOOK_ID,
        sidecar_id=sidecar_id,
        kind="transaction_description",
        plaintext=plaintext,
    )
    return ProtectedContentSnapshot(
        book_id=BOOK_ID,
        sidecar_id=sidecar_id,
        kind="transaction_description",
        ciphertext=sealed.ciphertext,
        key_ref=sealed.key_ref,
        nonce=sealed.nonce,
        algorithm=sealed.algorithm,
        content_hash=sealed.content_hash,
        status="active",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        erased_at=None,
    )


def _archive_snapshot(plaintext: bytes) -> ImportArchiveManifestSnapshot:
    cipher = _cipher()
    sealed = cipher.encrypt(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="import_archive",
        plaintext=plaintext,
    )
    sidecar = ProtectedContentSnapshot(
        book_id=BOOK_ID,
        sidecar_id=SIDECAR_ID,
        kind="import_archive",
        ciphertext=sealed.ciphertext,
        key_ref=sealed.key_ref,
        nonce=sealed.nonce,
        algorithm=sealed.algorithm,
        content_hash=sealed.content_hash,
        status="active",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        erased_at=None,
    )
    seal = cipher.commit_archive_seal(
        book_id=BOOK_ID,
        archive_id=SIDECAR_ID,
        key_ref=sealed.key_ref,
        contract_version=1,
        archive_content_commitment=sealed.content_hash,
        record_counts=RECORD_COUNTS,
        **HASHES,
    )
    return ImportArchiveManifestSnapshot(
        book_id=BOOK_ID,
        archive_id=SIDECAR_ID,
        contract_version=1,
        archive_content_commitment=sealed.content_hash,
        seal=seal,
        record_counts=MappingProxyType(RECORD_COUNTS),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        sidecar=sidecar,
        **HASHES,
    )


def _mutate_archive_crypto(
    manifest: ImportArchiveManifestSnapshot,
    field: str,
) -> None:
    current = getattr(manifest.sidecar, field)
    assert isinstance(current, bytes) and current
    mutated = bytes([current[0] ^ 1]) + current[1:]
    object.__setattr__(manifest.sidecar, field, mutated)


def _get_session() -> Iterator[Session]:
    yield cast(Session, SESSION)


def _client(
    *,
    cipher: ProtectedContentCipher | None = None,
    authorize_owner=lambda *_args: None,
) -> TestClient:
    from track_anywhere.api.v2.queries import create_query_router

    app = FastAPI()
    app.include_router(
        create_query_router(
            _get_session,
            authorize_book_read=lambda *_args: None,
            authorize_book_owner_read=authorize_owner,
            protected_content_cipher=cipher,
        )
    )
    return TestClient(app)


def _journal_page() -> JournalPage:
    return JournalPage(
        items=(
            SimpleNamespace(
                transaction_id=TRANSACTION_ID,
                effective_at=datetime(2026, 7, 17, tzinfo=UTC),
                book_position=1,
                transaction_kind="standard",
                postings=(),
                reversed_by_transaction_id=None,
                reverses_transaction_id=None,
                credit_card_relation=None,
                description_ref=SIDECAR_ID,
            ),
        ),
        next_cursor=None,
        as_of_book_position=1,
    )


def test_journal_item_keeps_description_reference_internal() -> None:
    from track_anywhere.api.v2.query_routes.journal import (
        JournalItemResponse,
        serialize_journal_item,
    )

    item = JournalItem(
        transaction_id=TRANSACTION_ID,
        effective_at=datetime(2026, 7, 17, tzinfo=UTC),
        book_position=1,
        transaction_kind="standard",
        postings=(),
        reversed_by_transaction_id=None,
        reverses_transaction_id=None,
        description_ref=SIDECAR_ID,
    )

    assert item.description_ref == SIDECAR_ID
    assert "description_ref" not in serialize_journal_item(item).model_dump()
    assert "description" not in JournalItemResponse.model_json_schema()["properties"]


def test_protected_content_service_decrypts_only_active_expected_kind() -> None:
    plaintext = b'{"line_memos":["latte",null],"purpose":"coffee","transaction_memo":null}'
    service = ProtectedContentService(
        cipher=_cipher(),
        repository=ProtectedContentRepository(),
    )

    assert service.decrypt_active(
        _snapshot(plaintext),
        expected_kind="transaction_description",
    ) == plaintext

    wrong_kind = _snapshot(plaintext)
    object.__setattr__(wrong_kind, "kind", "import_archive")
    with pytest.raises(
        ProtectedContentConflict,
        match="^protected content conflicts with existing data$",
    ):
        service.decrypt_active(
            wrong_kind,
            expected_kind="transaction_description",
        )


def test_transaction_descriptions_are_strictly_decoded_in_one_batch() -> None:
    from track_anywhere.queries.protected_content import (
        get_transaction_descriptions,
    )

    other_sidecar_id = UUID("44444444-4444-4444-8444-444444444444")
    first = _snapshot(
        b'{"line_memos":["latte",null],"purpose":"coffee","transaction_memo":null}'
    )
    second = _snapshot(
        b'{"line_memos":[],"purpose":null,"transaction_memo":"transfer"}',
        sidecar_id=other_sidecar_id,
    )

    class Repository:
        def __init__(self) -> None:
            self.calls: list[tuple[UUID, tuple[UUID, ...]]] = []

        def get_active_batch(
            self,
            _session: Session,
            *,
            book_id: UUID,
            sidecar_ids: tuple[UUID, ...],
        ) -> dict[UUID, ProtectedContentSnapshot]:
            self.calls.append((book_id, sidecar_ids))
            return {SIDECAR_ID: first, other_sidecar_id: second}

    repository = Repository()
    descriptions = get_transaction_descriptions(
        cast(Session, SESSION),
        BOOK_ID,
        description_refs=(SIDECAR_ID, other_sidecar_id, SIDECAR_ID),
        cipher=_cipher(),
        repository=cast(ProtectedContentRepository, repository),
    )

    assert descriptions[SIDECAR_ID].purpose == "coffee"
    assert descriptions[SIDECAR_ID].line_memos == ("latte", None)
    assert descriptions[other_sidecar_id].transaction_memo == "transfer"
    assert repository.calls == [
        (BOOK_ID, (SIDECAR_ID, other_sidecar_id)),
    ]


def test_erased_transaction_description_is_distinct_from_unavailable() -> None:
    from track_anywhere.queries.protected_content import (
        get_transaction_descriptions,
    )

    erased = _snapshot(
        b'{"line_memos":[],"purpose":"private","transaction_memo":null}'
    )
    object.__setattr__(erased, "status", "erased")
    object.__setattr__(erased, "ciphertext", None)
    object.__setattr__(erased, "key_ref", None)
    object.__setattr__(erased, "nonce", None)

    class Repository:
        def get_active_batch(self, *_args, **_kwargs):
            return {}

        def get_batch(self, *_args, **_kwargs):
            return {SIDECAR_ID: erased}

    with pytest.raises(
        ProtectedContentErased,
        match="^protected content was erased$",
    ):
        get_transaction_descriptions(
            cast(Session, SESSION),
            BOOK_ID,
            description_refs=(SIDECAR_ID,),
            cipher=_cipher(),
            repository=cast(ProtectedContentRepository, Repository()),
        )


@pytest.mark.parametrize(
    "plaintext",
    (
        b'{"line_memos":[],"purpose":"coffee","purpose":"duplicate","transaction_memo":null}',
        b'{"extra":1,"line_memos":[],"purpose":"coffee","transaction_memo":null}',
        b'{"line_memos":[],"purpose":1,"transaction_memo":null}',
        b'{ "line_memos":[],"purpose":"coffee","transaction_memo":null}',
        b"\xff",
    ),
)
def test_transaction_description_rejects_noncanonical_or_invalid_plaintext(
    plaintext: bytes,
) -> None:
    from track_anywhere.queries.protected_content import (
        get_transaction_descriptions,
    )

    snapshot = _snapshot(plaintext)

    class Repository:
        def get_active_batch(self, *_args, **_kwargs):
            return {SIDECAR_ID: snapshot}

    with pytest.raises(
        ProtectedContentUnavailable,
        match="^protected content is unavailable$",
    ):
        get_transaction_descriptions(
            cast(Session, SESSION),
            BOOK_ID,
            description_refs=(SIDECAR_ID,),
            cipher=_cipher(),
            repository=cast(ProtectedContentRepository, Repository()),
        )


def test_journal_default_omits_description_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    monkeypatch.setattr(journal_api, "list_journal", lambda *_args, **_kwargs: _journal_page())
    monkeypatch.setattr(
        journal_api,
        "get_transaction_descriptions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("default journal read must not decrypt")
        ),
    )

    response = _client().get(f"/api/v2/books/{BOOK_ID}/journal")

    assert response.status_code == 200
    assert "description" not in response.json()["items"][0]


def test_explicit_journal_description_fails_closed_without_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    monkeypatch.setattr(journal_api, "list_journal", lambda *_args, **_kwargs: _journal_page())

    response = _client().get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={"include_description": "true"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Protected content is unavailable"}


def test_owner_can_explicitly_include_journal_descriptions_in_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    owner_calls: list[tuple[object, UUID]] = []
    decrypt_calls: list[tuple[UUID, ...]] = []

    def authorize_owner(
        session: Session,
        _request: Request,
        book_id: UUID,
    ) -> None:
        owner_calls.append((session, book_id))

    def descriptions(*_args, description_refs: tuple[UUID, ...], **_kwargs):
        decrypt_calls.append(description_refs)
        return {
            SIDECAR_ID: TransactionDescription(
                purpose="coffee",
                transaction_memo=None,
                line_memos=("latte", None),
            )
        }

    monkeypatch.setattr(journal_api, "list_journal", lambda *_args, **_kwargs: _journal_page())
    monkeypatch.setattr(
        journal_api,
        "get_transaction_descriptions",
        descriptions,
        raising=False,
    )

    response = _client(cipher=_cipher(), authorize_owner=authorize_owner).get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={"include_description": "true"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["description"] == {
        "purpose": "coffee",
        "transaction_memo": None,
        "line_memos": ["latte", None],
    }
    assert owner_calls == [(SESSION, BOOK_ID)]
    assert decrypt_calls == [(SIDECAR_ID,)]


def test_viewer_can_read_default_journal_but_cannot_include_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    monkeypatch.setattr(journal_api, "list_journal", lambda *_args, **_kwargs: _journal_page())

    def deny_owner(*_args) -> None:
        raise HTTPException(status_code=403, detail="Book read access is denied")

    client = _client(cipher=_cipher(), authorize_owner=deny_owner)
    ordinary = client.get(f"/api/v2/books/{BOOK_ID}/journal")
    protected = client.get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={"include_description": "true"},
    )

    assert ordinary.status_code == 200
    assert protected.status_code == 403
    assert protected.json() == {"detail": "Book read access is denied"}


@pytest.mark.parametrize(
    ("role", "allowed"),
    (("owner", True), ("viewer", False), ("admin", False)),
)
def test_owner_authorizer_requires_exact_owner_role(
    role: str,
    allowed: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import authorization as authorization

    monkeypatch.setattr(
        authorization,
        "authenticate_request_actor",
        lambda *_args: SimpleNamespace(
            credential_book_id=BOOK_ID,
            scopes=frozenset({"ledger:read"}),
            command_actor=SimpleNamespace(subject_id="human:reader"),
        ),
    )

    class Repository:
        def __init__(self, _session: Session) -> None:
            pass

        def get_membership(self, *_args):
            return SimpleNamespace(
                role=role,
                status="active",
                revoked_at=None,
                scopes=("ledger:read",),
            )

    monkeypatch.setattr(authorization, "AuthRepository", Repository)
    call = lambda: authorization.authorize_book_owner_read(
        cast(Session, SESSION),
        cast(Request, object()),
        BOOK_ID,
    )

    if allowed:
        assert call() is None
    else:
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 403


def test_explicit_description_is_null_when_transaction_has_no_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    page = _journal_page()
    object.__setattr__(page.items[0], "description_ref", None)
    monkeypatch.setattr(journal_api, "list_journal", lambda *_args, **_kwargs: page)
    monkeypatch.setattr(
        journal_api,
        "get_transaction_descriptions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no description reference must not query sidecars")
        ),
    )

    response = _client(cipher=_cipher()).get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={"include_description": "true"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["description"] is None


def test_explicit_transaction_show_decrypts_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import journal as journal_api

    item = _journal_page().items[0]
    monkeypatch.setattr(
        journal_api,
        "get_journal_transaction",
        lambda *_args, **_kwargs: item,
    )
    monkeypatch.setattr(
        journal_api,
        "get_transaction_descriptions",
        lambda *_args, **_kwargs: {
            SIDECAR_ID: TransactionDescription(
                purpose="coffee",
                transaction_memo=None,
                line_memos=(),
            )
        },
    )

    response = _client(cipher=_cipher()).get(
        f"/api/v2/books/{BOOK_ID}/journal/transactions/{TRANSACTION_ID}",
        params={"include_description": "true"},
    )

    assert response.status_code == 200
    assert response.json()["description"] == {
        "purpose": "coffee",
        "transaction_memo": None,
        "line_memos": [],
    }


def test_create_app_accepts_an_explicit_protected_content_cipher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api import create_app

    monkeypatch.delenv("TRACK_ANYWHERE_DATABASE_URL", raising=False)
    engine = create_engine(
        "postgresql+psycopg://track_anywhere_runtime:test@127.0.0.1:9/contract"
    )
    cipher = _cipher()
    try:
        application = create_app(
            engine=engine,
            expected_runtime_role="track_anywhere_runtime",
            protected_content_cipher=cipher,
            cookie_secure=False,
        )
    finally:
        engine.dispose()

    assert application.state.runtime_dependencies.protected_content_cipher is cipher


def test_runtime_fails_closed_when_configured_keyring_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.dependencies import build_runtime_dependencies

    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text("{}", encoding="utf-8")
    keyring_file.chmod(0o400)
    monkeypatch.setenv(
        "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE",
        str(keyring_file),
    )

    with pytest.raises(
        ProtectedContentConfigurationError,
        match="^protected content keyring is invalid$",
    ):
        build_runtime_dependencies(
            "postgresql+psycopg://track_anywhere_runtime:test@127.0.0.1:9/contract"
        )


def test_archive_manifest_seal_is_verified_before_metadata_or_export() -> None:
    service = ProtectedContentService(
        cipher=_cipher(),
        repository=ProtectedContentRepository(),
    )
    canonical_ndjson = b'{"record":1}\n'
    manifest = _archive_snapshot(canonical_ndjson)

    assert service.verify_archive_manifest(
        manifest,
        include_content=False,
    ) is None
    assert service.verify_archive_manifest(
        manifest,
        include_content=True,
    ) == canonical_ndjson

    object.__setattr__(manifest, "seal", b"x" * 32)
    with pytest.raises(
        ProtectedContentConflict,
        match="^protected content conflicts with existing data$",
    ):
        service.verify_archive_manifest(manifest, include_content=False)


def test_archive_metadata_rejects_incomplete_active_sidecar() -> None:
    service = ProtectedContentService(
        cipher=_cipher(),
        repository=ProtectedContentRepository(),
    )
    manifest = _archive_snapshot(b'{"record":1}\n')
    object.__setattr__(manifest.sidecar, "ciphertext", None)

    with pytest.raises(
        ProtectedContentConflict,
        match="^protected content conflicts with existing data$",
    ):
        service.verify_archive_manifest(manifest, include_content=False)


def test_import_archive_reads_fail_closed_without_keyring() -> None:
    response = _client().get(f"/api/v2/books/{BOOK_ID}/import-archives")

    assert response.status_code == 503
    assert response.json() == {"detail": "Protected content is unavailable"}


def test_owner_archive_list_and_export_expose_only_reviewed_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import protected_content as route

    metadata = ImportArchiveMetadata(
        archive_id=SIDECAR_ID,
        contract_version=1,
        content_commitment=b"h" * 32,
        seal=b"s" * 32,
        record_counts=MappingProxyType(RECORD_COUNTS),
        created_at=datetime(2026, 7, 17, 1, 2, 3, 4, tzinfo=UTC),
        **HASHES,
    )
    canonical_ndjson = b'{"record":1}\n'
    monkeypatch.setattr(
        route,
        "list_import_archives",
        lambda *_args, **_kwargs: (metadata,),
    )
    monkeypatch.setattr(
        route,
        "export_import_archive",
        lambda *_args, **_kwargs: ImportArchiveExport(
            archive_id=SIDECAR_ID,
            content_commitment=b"h" * 32,
            seal=b"s" * 32,
            canonical_ndjson=canonical_ndjson,
        ),
    )
    client = _client(cipher=_cipher())

    listed = client.get(f"/api/v2/books/{BOOK_ID}/import-archives")
    exported = client.get(
        f"/api/v2/books/{BOOK_ID}/import-archives/{SIDECAR_ID}/export"
    )

    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item == {
        "archive_id": str(SIDECAR_ID),
        "contract_version": 1,
        "source_dump_hash": HASHES["source_dump_hash"].hex(),
        "source_manifest_hash": HASHES["source_manifest_hash"].hex(),
        "card_review_hash": HASHES["card_review_hash"].hex(),
        "plan_hash": HASHES["plan_hash"].hex(),
        "content_commitment": (b"h" * 32).hex(),
        "seal": (b"s" * 32).hex(),
        "record_counts": RECORD_COUNTS,
        "created_at": "2026-07-17T01:02:03.000004Z",
    }
    assert not {
        "ciphertext",
        "key_ref",
        "nonce",
        "ndjson",
    }.intersection(item)
    assert exported.status_code == 200
    assert exported.json() == {
        "archive_id": str(SIDECAR_ID),
        "content_type": "application/x-ndjson",
        "content_commitment": (b"h" * 32).hex(),
        "seal": (b"s" * 32).hex(),
        "ndjson": canonical_ndjson.decode("utf-8"),
    }


def test_archive_queries_verify_seal_and_canonical_export() -> None:
    from track_anywhere.queries.protected_content import (
        export_import_archive,
        list_import_archives,
    )

    canonical_ndjson = b'{"record":1}\n'
    manifest = _archive_snapshot(canonical_ndjson)

    class Repository:
        def list_archive_manifests(self, *_args, **_kwargs):
            return (manifest,)

        def get_archive_manifest(self, *_args, **_kwargs):
            return manifest

    repository = cast(ProtectedContentRepository, Repository())
    listed = list_import_archives(
        cast(Session, SESSION),
        BOOK_ID,
        cipher=_cipher(),
        repository=repository,
    )
    exported = export_import_archive(
        cast(Session, SESSION),
        BOOK_ID,
        SIDECAR_ID,
        cipher=_cipher(),
        repository=repository,
    )

    assert listed[0].archive_id == SIDECAR_ID
    assert exported.canonical_ndjson == canonical_ndjson

    object.__setattr__(manifest, "plan_hash", b"x" * 32)
    with pytest.raises(
        ProtectedContentUnavailable,
        match="^protected content is unavailable$",
    ):
        list_import_archives(
            cast(Session, SESSION),
            BOOK_ID,
            cipher=_cipher(),
            repository=repository,
        )


@pytest.mark.parametrize("field", ("ciphertext", "nonce"))
def test_archive_metadata_query_authenticates_encrypted_payload(field: str) -> None:
    from track_anywhere.queries.protected_content import list_import_archives

    manifest = _archive_snapshot(b'{"private":"metadata-must-authenticate"}\n')
    _mutate_archive_crypto(manifest, field)

    class Repository:
        def list_archive_manifests(self, *_args, **_kwargs):
            return (manifest,)

    with pytest.raises(
        ProtectedContentUnavailable,
        match="^protected content is unavailable$",
    ) as unavailable:
        list_import_archives(
            cast(Session, SESSION),
            BOOK_ID,
            cipher=_cipher(),
            repository=cast(ProtectedContentRepository, Repository()),
        )
    assert "metadata-must-authenticate" not in str(unavailable.value)


@pytest.mark.parametrize("field", ("ciphertext", "nonce"))
def test_archive_metadata_rest_fails_closed_for_crypto_mutation(
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.queries import protected_content as query

    manifest = _archive_snapshot(b'{"private":"metadata-must-authenticate"}\n')
    _mutate_archive_crypto(manifest, field)

    class Repository:
        def list_archive_manifests(self, *_args, **_kwargs):
            return (manifest,)

    monkeypatch.setattr(query, "ProtectedContentRepository", Repository)

    response = _client(cipher=_cipher()).get(
        f"/api/v2/books/{BOOK_ID}/import-archives"
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Protected content is unavailable"}
    assert "metadata-must-authenticate" not in response.text


def test_archive_routes_hide_absence_and_enforce_owner_before_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2.query_routes import protected_content as route

    def deny_owner(*_args) -> None:
        raise HTTPException(status_code=403, detail="Book read access is denied")

    denied = _client(authorize_owner=deny_owner).get(
        f"/api/v2/books/{BOOK_ID}/import-archives"
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "Book read access is denied"}

    monkeypatch.setattr(
        route,
        "export_import_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LookupError("database detail")
        ),
    )
    missing = _client(cipher=_cipher()).get(
        f"/api/v2/books/{BOOK_ID}/import-archives/{SIDECAR_ID}/export"
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Import archive not found"}
