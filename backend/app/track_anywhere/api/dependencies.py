from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import os

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..application.ledger_committer import LedgerCommitter
from ..infrastructure.crypto import (
    DuplicateDetectionConfigurationError,
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from ..infrastructure.db.engine import create_v2_engine
from ..infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


DATABASE_URL_ENV = "TRACK_ANYWHERE_DATABASE_URL"
PROTECTED_CONTENT_KEYRING_FILE_ENV = (
    "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE"
)
DUPLICATE_DETECTION_KEY_FILE_ENV = "TRACK_ANYWHERE_DUPLICATE_DETECTION_KEY_FILE"

SessionFactory = Callable[[], Session]
SessionDependency = Callable[[], Iterator[Session]]
UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    engine: Engine
    expected_runtime_role: str
    session_factory: SessionFactory
    get_session: SessionDependency
    uow_factory: UnitOfWorkFactory
    ledger_committer: LedgerCommitter
    protected_content_cipher: ProtectedContentCipher | None
    duplicate_detection_key_provider: DuplicateDetectionKeyProvider | None


def create_session_dependency(
    session_factory: SessionFactory,
) -> SessionDependency:
    def get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    return get_session


def build_runtime_dependencies(database_url: str) -> RuntimeDependencies:
    return build_engine_dependencies(
        create_v2_engine(database_url),
        protected_content_cipher=_configured_protected_content_cipher(),
        duplicate_detection_key_provider=(
            _configured_duplicate_detection_key_provider()
        ),
    )


def _configured_protected_content_cipher() -> ProtectedContentCipher | None:
    if os.environ.get(PROTECTED_CONTENT_KEYRING_FILE_ENV) is None:
        return None
    return ProtectedContentCipher(ProtectedContentKeyring.from_environment())


def _configured_duplicate_detection_key_provider(
) -> DuplicateDetectionKeyProvider | None:
    if os.environ.get(DUPLICATE_DETECTION_KEY_FILE_ENV) is None:
        return None
    try:
        return DuplicateDetectionKeyProvider.from_environment()
    except DuplicateDetectionConfigurationError:
        # Runtime remains available for unrelated APIs while entry writes fail
        # closed at the application boundary.
        return None


def build_engine_dependencies(
    engine: Engine,
    *,
    expected_runtime_role: str | None = None,
    protected_content_cipher: ProtectedContentCipher | None = None,
    duplicate_detection_key_provider: DuplicateDetectionKeyProvider | None = None,
) -> RuntimeDependencies:
    runtime_role = expected_runtime_role or engine.url.username
    if not runtime_role:
        raise RuntimeError("runtime database URL must include a login")
    if engine.url.username is None:
        raise RuntimeError("runtime database URL must include a login")

    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        future=True,
    )
    get_session = create_session_dependency(session_factory)

    def create_unit_of_work() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return RuntimeDependencies(
        engine=engine,
        expected_runtime_role=runtime_role,
        session_factory=session_factory,
        get_session=get_session,
        uow_factory=create_unit_of_work,
        ledger_committer=LedgerCommitter(),
        protected_content_cipher=protected_content_cipher,
        duplicate_detection_key_provider=duplicate_detection_key_provider,
    )


__all__ = [
    "DATABASE_URL_ENV",
    "DUPLICATE_DETECTION_KEY_FILE_ENV",
    "PROTECTED_CONTENT_KEYRING_FILE_ENV",
    "RuntimeDependencies",
    "SessionDependency",
    "SessionFactory",
    "UnitOfWorkFactory",
    "build_engine_dependencies",
    "build_runtime_dependencies",
    "create_session_dependency",
]
