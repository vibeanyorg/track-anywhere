from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, SessionTransaction


class SqlAlchemyUnitOfWork:
    """Own one SQLAlchemy session and its outermost database transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self.session: Session
        self._transaction: SessionTransaction

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        try:
            self._transaction = self.session.begin()
            self._transaction.__enter__()
        except BaseException:
            self.session.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return self._transaction.__exit__(exc_type, exc, traceback)
        finally:
            self.session.close()


__all__ = ["SqlAlchemyUnitOfWork"]
