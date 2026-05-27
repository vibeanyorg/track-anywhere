from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session


class StorageUpsertWriters:
    def _upsert_record(self, session: Session, model, values: dict[str, Any], key_columns: list[str]) -> None:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert = postgresql_insert
        elif dialect_name == "sqlite":
            insert = sqlite_insert
        else:
            session.merge(model(**values))
            return
        statement = insert(model).values(**values)
        update_values = {key: getattr(statement.excluded, key) for key in values if key not in key_columns}
        if update_values:
            statement = statement.on_conflict_do_update(index_elements=key_columns, set_=update_values)
        else:
            statement = statement.on_conflict_do_nothing(index_elements=key_columns)
        session.execute(statement)
