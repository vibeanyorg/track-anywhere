from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy.sql import Select


class RowLock(StrEnum):
    NONE = "none"
    SHARE = "share"
    UPDATE = "update"


def apply_row_lock(statement: Select[Any], lock: RowLock) -> Select[Any]:
    if lock is RowLock.SHARE:
        return statement.with_for_update(read=True)
    if lock is RowLock.UPDATE:
        return statement.with_for_update()
    return statement


__all__ = ["RowLock", "apply_row_lock"]
