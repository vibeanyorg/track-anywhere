from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    """Request-scoped transaction boundary owned by the application layer."""

    session: Any

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


__all__ = ["UnitOfWork"]
