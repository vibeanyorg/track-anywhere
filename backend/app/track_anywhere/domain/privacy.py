from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictStr


FORBIDDEN_EVENT_FIELD_NAMES = frozenset(
    {
        "attachment_bytes",
        "attachment_content",
        "attachment_name",
        "credential",
        "idempotency_key",
        "memo",
        "merchant_display_name",
        "merchant_name",
        "purpose",
        "raw_idempotency_key",
        "secret",
        "token",
    }
)

CanonicalUnits = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,37}$")]
AssetCode = Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9._-]{0,15}$")]
ProviderCode = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
ExternalReferenceValue = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"),
]
CanonicalEventHash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class EventContract(FrozenContract):
    event_type: ClassVar[str]
    schema_version: ClassVar[int] = 1


def validate_ordered_records(
    records: tuple[FrozenContract, ...],
    *,
    minimum: int,
    unique_fields: tuple[str, ...],
) -> None:
    if len(records) < minimum:
        raise ValueError(f"ordered records require at least {minimum} item(s)")

    positions = [record.position for record in records]
    if positions != list(range(len(records))):
        raise ValueError("ordered record positions must be contiguous from zero")

    for field_name in unique_fields:
        values = [getattr(record, field_name) for record in records]
        if len(values) != len(set(values)):
            raise ValueError(f"ordered record {field_name} values must be unique")
