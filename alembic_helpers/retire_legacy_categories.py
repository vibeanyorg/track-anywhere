"""retire legacy transaction categories

Revision ID: 0008_retire_legacy_categories
Revises: 0007_drop_django_tables
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


def upgrade() -> None:
    bind = op.get_bind()
    _create_category_migration_audit_table(bind)
    _normalize_category_nodes(bind)
    _backfill_transaction_lines(bind)
    _drop_column_if_exists(bind, "transactions", "category_id")
    _drop_column_if_exists(bind, "categories", "primary")
    _drop_column_if_exists(bind, "categories", "secondary")


def downgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(bind, "transactions", sa.Column("category_id", sa.String(length=80), nullable=True))
    _add_column_if_missing(bind, "categories", sa.Column("primary", sa.String(length=80), nullable=True))
    _add_column_if_missing(bind, "categories", sa.Column("secondary", sa.String(length=80), nullable=True))
    _restore_legacy_category_columns(bind)
    if _has_table(bind, "transaction_category_migration_audit"):
        op.drop_table("transaction_category_migration_audit")


def _create_category_migration_audit_table(bind) -> None:
    if _has_table(bind, "transaction_category_migration_audit"):
        return
    op.create_table(
        "transaction_category_migration_audit",
        sa.Column("audit_id", sa.String(length=80), nullable=False),
        sa.Column("transaction_id", sa.String(length=80), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("legacy_category_id", sa.String(length=80), nullable=False),
        sa.Column("created_line_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )


def _normalize_category_nodes(bind) -> None:
    if not _has_table(bind, "categories"):
        return
    columns = _column_names(bind, "categories")
    quoted = bind.dialect.identifier_preparer.quote
    has_primary = "primary" in columns
    has_secondary = "secondary" in columns
    primary_expr = quoted("primary") if has_primary else "name"
    secondary_expr = quoted("secondary") if has_secondary else "null"
    rows = list(
        bind.execute(
            text(
                f"""
                select
                  category_id,
                  coalesce(book_id, 'book_default') as book_id,
                  kind,
                  {primary_expr} as primary_name,
                  {secondary_expr} as secondary_name,
                  parent_id,
                  name,
                  level,
                  path_cache,
                  status,
                  version
                from categories
                """
            )
        ).mappings()
    )
    by_id = {row["category_id"]: dict(row) for row in rows}
    root_by_key = {
        (row["book_id"], row["kind"], _normalize(row["name"] or row["primary_name"]))
        : row["category_id"]
        for row in rows
        if row["parent_id"] is None and (row["name"] or row["primary_name"])
    }

    for row in rows:
        primary = _clean(row["primary_name"]) or _clean(row["name"])
        secondary = _clean(row["secondary_name"])
        name = _clean(row["name"])
        parent_id = row["parent_id"]
        if secondary and not parent_id:
            parent_id = root_by_key.get((row["book_id"], row["kind"], _normalize(primary)))
            if parent_id is None:
                parent_id = _deterministic_id("cat_migrated", row["book_id"], row["kind"], primary)
                _insert_category_parent(bind, columns, parent_id, row, primary)
                root_by_key[(row["book_id"], row["kind"], _normalize(primary))] = parent_id
            name = secondary
        elif parent_id:
            parent = by_id.get(parent_id)
            primary = _clean(parent["name"] if parent else primary) or primary
            name = name or secondary or primary
        else:
            name = name or primary
            primary = name
            secondary = None

        level = 2 if parent_id else 1
        path_cache = f"{primary} / {name}" if parent_id else name
        _update_category_node(bind, columns, row["category_id"], primary, secondary, parent_id, name, level, path_cache)


def _insert_category_parent(bind, columns: set[str], category_id: str, child_row: dict[str, object], primary: str) -> None:
    values: dict[str, object] = {
        "category_id": category_id,
        "book_id": child_row["book_id"],
        "kind": child_row["kind"],
        "parent_id": None,
        "name": primary,
        "normalized_name": _normalize(primary),
        "level": 1,
        "path_cache": primary,
        "status": "active",
        "sort_order": 0,
        "version": 1,
    }
    if "primary" in columns:
        values["primary"] = primary
    if "secondary" in columns:
        values["secondary"] = None
    insert_columns = [column for column in values if column in columns]
    quoted = bind.dialect.identifier_preparer.quote
    bind.execute(
        text(
            f"""
            insert into categories ({', '.join(quoted(column) for column in insert_columns)})
            values ({', '.join(':' + column for column in insert_columns)})
            """
        ),
        {column: values[column] for column in insert_columns},
    )


def _update_category_node(
    bind,
    columns: set[str],
    category_id: str,
    primary: str,
    secondary: str | None,
    parent_id: str | None,
    name: str,
    level: int,
    path_cache: str,
) -> None:
    values: dict[str, object] = {
        "parent_id": parent_id,
        "name": name,
        "normalized_name": _normalize(name),
        "level": level,
        "path_cache": path_cache,
    }
    if "primary" in columns:
        values["primary"] = primary
    if "secondary" in columns:
        values["secondary"] = secondary
    set_columns = [column for column in values if column in columns]
    quoted = bind.dialect.identifier_preparer.quote
    bind.execute(
        text(
            f"""
            update categories
            set {', '.join(quoted(column) + ' = :' + column for column in set_columns)}
            where category_id = :category_id
            """
        ),
        {**{column: values[column] for column in set_columns}, "category_id": category_id},
    )


def _backfill_transaction_lines(bind) -> None:
    if not {"transactions", "transaction_lines", "postings", "accounts", "categories"} <= set(
        inspect(bind).get_table_names()
    ):
        return
    if "category_id" not in _column_names(bind, "transactions"):
        return

    transactions = list(
        bind.execute(
            text(
                """
                select transaction_id, coalesce(book_id, 'book_default') as book_id, category_id, memo, purpose
                from transactions
                where category_id is not null
                order by transaction_id
                """
            )
        ).mappings()
    )
    _raise_on_backfill_preflight_failures(bind, transactions)
    for transaction in transactions:
        category = _legacy_category(bind, transaction["category_id"])
        if category is None:
            _audit(bind, transaction, None, "failed", "legacy category does not exist")
            raise RuntimeError(f"legacy category does not exist: {transaction['category_id']}")
        if category["book_id"] != transaction["book_id"]:
            _audit(bind, transaction, None, "failed", "legacy category belongs to a different book")
            raise RuntimeError(f"legacy category belongs to a different book: {transaction['transaction_id']}")

        line_rows = _transaction_line_rows(bind, transaction["transaction_id"])
        line_category_ids = {row["category_id"] for row in line_rows if row["category_id"] is not None}
        if line_category_ids:
            if transaction["category_id"] not in line_category_ids:
                _audit(bind, transaction, None, "failed", "line category does not match legacy transaction category")
                raise RuntimeError(f"line category mismatch for {transaction['transaction_id']}")
            _audit(bind, transaction, None, "already_line_backed", "transaction already has categorized lines")
            continue

        snapshot = _category_snapshot(bind, category)
        version_id = _active_category_version_id(bind, transaction["category_id"])
        if len(line_rows) == 1:
            line_id = line_rows[0]["line_id"]
            bind.execute(
                text(
                    """
                    update transaction_lines
                    set category_id = :category_id,
                        category_version_id = :category_version_id,
                        category_path_snapshot = :category_path_snapshot
                    where line_id = :line_id
                    """
                ).bindparams(sa.bindparam("category_path_snapshot", type_=sa.JSON())),
                {
                    "line_id": line_id,
                    "category_id": transaction["category_id"],
                    "category_version_id": version_id,
                    "category_path_snapshot": snapshot,
                },
            )
            _audit(bind, transaction, line_id, "updated_existing_line", "single existing line received category")
            continue
        if line_rows:
            _audit(bind, transaction, None, "failed", "multiple existing lines cannot be mapped from one legacy category")
            raise RuntimeError(f"multiple existing lines cannot be mapped from legacy category: {transaction['transaction_id']}")

        currency, amount = _derive_reporting_amount(bind, transaction["transaction_id"], category["kind"])
        line_id = _deterministic_id("line_migrated", transaction["transaction_id"], transaction["category_id"], currency)
        position = bind.execute(
            text(
                """
                select coalesce(max(position) + 1, 0)
                from transaction_lines
                where transaction_id = :transaction_id
                """
            ),
            {"transaction_id": transaction["transaction_id"]},
        ).scalar_one()
        bind.execute(
            text(
                """
                insert into transaction_lines (
                    line_id, transaction_id, position, line_type, amount, currency, book_id, category_id,
                    category_version_id, category_path_snapshot, merchant_id, project_id, necessity,
                    reimbursement_status, memo, version
                ) values (
                    :line_id, :transaction_id, :position, :line_type, :amount, :currency, :book_id, :category_id,
                    :category_version_id, :category_path_snapshot, null, null, 'unknown',
                    'none', :memo, 1
                )
                """
            ).bindparams(sa.bindparam("category_path_snapshot", type_=sa.JSON())),
            {
                "line_id": line_id,
                "transaction_id": transaction["transaction_id"],
                "position": position,
                "line_type": category["kind"],
                "amount": str(amount),
                "currency": currency,
                "book_id": transaction["book_id"],
                "category_id": transaction["category_id"],
                "category_version_id": version_id,
                "category_path_snapshot": snapshot,
                "memo": transaction["memo"] or "",
            },
        )
        _audit(bind, transaction, line_id, "created_line", "created line from legacy transaction category")


def _raise_on_backfill_preflight_failures(bind, transactions) -> None:
    failures = []
    for transaction in transactions:
        category = _legacy_category(bind, transaction["category_id"])
        if category is None:
            failures.append(_preflight_failure(transaction, "legacy category does not exist"))
            continue
        if category["book_id"] != transaction["book_id"]:
            failures.append(_preflight_failure(transaction, "legacy category belongs to a different book"))
            continue

        line_rows = _transaction_line_rows(bind, transaction["transaction_id"])
        line_category_ids = {row["category_id"] for row in line_rows if row["category_id"] is not None}
        if line_category_ids:
            if transaction["category_id"] not in line_category_ids:
                failures.append(_preflight_failure(transaction, "line category does not match legacy transaction category"))
            continue
        if len(line_rows) > 1:
            failures.append(_preflight_failure(transaction, "multiple existing lines cannot be mapped from one legacy category"))
            continue
        if not line_rows:
            try:
                _derive_reporting_amount(bind, transaction["transaction_id"], category["kind"])
            except RuntimeError as exc:
                failures.append(_preflight_failure(transaction, str(exc)))

    if failures:
        rendered = "; ".join(
            f"transaction_id={item['transaction_id']} legacy_category_id={item['legacy_category_id']} reason={item['reason']}"
            for item in failures[:10]
        )
        suffix = f"; ... {len(failures) - 10} more" if len(failures) > 10 else ""
        raise RuntimeError(
            "legacy category cutover preflight failed; fix these rows before rerunning migration: "
            f"{rendered}{suffix}"
        )


def _preflight_failure(transaction, reason: str) -> dict[str, str]:
    return {
        "transaction_id": transaction["transaction_id"],
        "legacy_category_id": transaction["category_id"],
        "reason": reason,
    }


def _legacy_category(bind, category_id: str):
    return bind.execute(
        text(
            """
            select category_id, coalesce(book_id, 'book_default') as book_id, kind, parent_id, name, path_cache
            from categories
            where category_id = :category_id
            """
        ),
        {"category_id": category_id},
    ).mappings().first()


def _transaction_line_rows(bind, transaction_id: str):
    return list(
        bind.execute(
            text(
                """
                select line_id, category_id
                from transaction_lines
                where transaction_id = :transaction_id
                order by position
                """
            ),
            {"transaction_id": transaction_id},
        ).mappings()
    )


def _derive_reporting_amount(bind, transaction_id: str, category_kind: str) -> tuple[str, Decimal]:
    amounts: dict[str, Decimal] = {}
    postings = bind.execute(
        text(
            """
            select postings.amount, postings.currency, accounts.type
            from postings
            join accounts on accounts.account_id = postings.account_id
            where postings.transaction_id = :transaction_id
            order by postings.position
            """
        ),
        {"transaction_id": transaction_id},
    ).mappings()
    for posting in postings:
        try:
            amount = Decimal(str(posting["amount"]))
        except InvalidOperation as exc:
            raise RuntimeError(f"invalid posting amount for {transaction_id}") from exc
        if category_kind == "expense" and posting["type"] == "expense" and amount > 0:
            amounts[posting["currency"]] = amounts.get(posting["currency"], Decimal("0")) + amount
        elif category_kind == "income" and posting["type"] == "income" and amount < 0:
            amounts[posting["currency"]] = amounts.get(posting["currency"], Decimal("0")) - amount

    positive_amounts = [(currency, amount) for currency, amount in amounts.items() if amount > 0]
    if len(positive_amounts) != 1:
        raise RuntimeError(f"cannot derive exactly one reporting line for {transaction_id}")
    return positive_amounts[0]


def _category_snapshot(bind, category) -> dict[str, str | None]:
    parent = None
    if category["parent_id"]:
        parent = bind.execute(
            text("select name, path_cache from categories where category_id = :category_id"),
            {"category_id": category["parent_id"]},
        ).mappings().first()
    primary = parent["name"] if parent is not None else category["name"]
    secondary = category["name"] if parent is not None else None
    path = category["path_cache"] or (f"{primary} / {secondary}" if secondary else primary)
    return {
        "category_id": category["category_id"],
        "category_version_id": _active_category_version_id(bind, category["category_id"]),
        "primary": primary,
        "secondary": secondary,
        "path": path,
    }


def _active_category_version_id(bind, category_id: str) -> str | None:
    if not _has_table(bind, "category_versions"):
        return None
    return bind.execute(
        text(
            """
            select category_version_id
            from category_versions
            where category_id = :category_id and valid_to is null
            order by valid_from desc, category_version_id desc
            limit 1
            """
        ),
        {"category_id": category_id},
    ).scalar()


def _audit(bind, transaction, line_id: str | None, status: str, reason: str) -> None:
    audit_id = _deterministic_id("cat_cutover", transaction["transaction_id"], transaction["category_id"], status)
    bind.execute(text("delete from transaction_category_migration_audit where audit_id = :audit_id"), {"audit_id": audit_id})
    bind.execute(
        text(
            """
            insert into transaction_category_migration_audit (
                audit_id, transaction_id, book_id, legacy_category_id, created_line_id, status, reason
            ) values (
                :audit_id, :transaction_id, :book_id, :legacy_category_id, :created_line_id, :status, :reason
            )
            """
        ),
        {
            "audit_id": audit_id,
            "transaction_id": transaction["transaction_id"],
            "book_id": transaction["book_id"],
            "legacy_category_id": transaction["category_id"],
            "created_line_id": line_id,
            "status": status,
            "reason": reason,
        },
    )


def _restore_legacy_category_columns(bind) -> None:
    if {"transactions", "transaction_lines"} <= set(inspect(bind).get_table_names()):
        bind.execute(
            text(
                """
                update transactions
                set category_id = (
                    select category_id
                    from transaction_lines
                    where transaction_lines.transaction_id = transactions.transaction_id
                      and category_id is not null
                    order by position
                    limit 1
                )
                where category_id is null
                """
            )
        )
    if "categories" not in inspect(bind).get_table_names():
        return
    rows = list(bind.execute(text("select category_id, parent_id, name from categories")).mappings())
    names = {row["category_id"]: row["name"] for row in rows}
    for row in rows:
        parent_name = names.get(row["parent_id"]) if row["parent_id"] else None
        bind.execute(
            text(
                """
                update categories
                set "primary" = :primary_name,
                    secondary = :secondary_name
                where category_id = :category_id
                """
            ),
            {
                "category_id": row["category_id"],
                "primary_name": parent_name or row["name"],
                "secondary_name": row["name"] if parent_name else None,
            },
        )


def _drop_column_if_exists(bind, table_name: str, column_name: str) -> None:
    if column_name not in _column_names(bind, table_name):
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_column(column_name)


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if column.name in _column_names(bind, table_name):
        return
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(column)


def _column_names(bind, table_name: str) -> set[str]:
    if not _has_table(bind, table_name):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _has_table(bind, table_name: str) -> bool:
    return table_name in set(inspect(bind).get_table_names())


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize(value: object) -> str:
    return _clean(value).casefold()


def _deterministic_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"
