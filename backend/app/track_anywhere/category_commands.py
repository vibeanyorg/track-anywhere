from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .commands import StrictCommand


CATEGORY_KINDS = Literal["income", "expense"]


class EnsureCategoryPathCommand(StrictCommand):
    kind: CATEGORY_KINDS
    path: str = Field(min_length=1, max_length=180)

    @field_validator("path")
    @classmethod
    def normalize_category_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = [" ".join(part.strip().split()) for part in value.split("/")]
        parts = [part for part in parts if part]
        if not parts:
            raise ValueError("category path must not be blank")
        if len(parts) > 2:
            raise ValueError("category path supports at most two levels")
        return " / ".join(parts)
