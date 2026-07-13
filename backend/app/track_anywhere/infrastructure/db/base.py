from __future__ import annotations

import importlib

from sqlalchemy import CheckConstraint, MetaData, SmallInteger, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class V2Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class V2SchemaMetadata(V2Base):
    __tablename__ = "v2_schema_metadata"
    __table_args__ = (
        CheckConstraint("schema_generation = 2", name="schema_generation_v2"),
    )

    schema_generation: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        autoincrement=False,
    )


def load_v2_models() -> None:
    module_name = "track_anywhere.infrastructure.db.models"
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise


def marker_table() -> Table:
    return V2SchemaMetadata.__table__


__all__ = [
    "NAMING_CONVENTION",
    "V2Base",
    "V2SchemaMetadata",
    "load_v2_models",
    "marker_table",
]
