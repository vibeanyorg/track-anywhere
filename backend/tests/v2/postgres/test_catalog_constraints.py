from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


CATALOG_TABLES = (
    "accounts",
    "assets",
    "books",
    "categories",
    "category_versions",
    "protected_description_sidecars",
)


def _execute(engine: Engine, statement: str, parameters: dict[str, object]) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), parameters)


def _rejects_integrity(
    engine: Engine, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        _execute(engine, statement, parameters)


def _rejects_database_operation(
    engine: Engine, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(DBAPIError):
        _execute(engine, statement, parameters)


def _insert_asset(
    engine: Engine,
    asset_code: str,
    *,
    ledger_scale: int = 2,
    input_scale: int = 2,
    display_scale: int = 2,
) -> None:
    _execute(
        engine,
        """
        insert into assets (
            asset_code, kind, ledger_scale, input_scale, display_scale,
            current_name, status
        ) values (
            :asset_code, 'fiat', :ledger_scale, :input_scale, :display_scale,
            :current_name, 'active'
        )
        """,
        {
            "asset_code": asset_code,
            "ledger_scale": ledger_scale,
            "input_scale": input_scale,
            "display_scale": display_scale,
            "current_name": f"{asset_code} name",
        },
    )


def _insert_book(
    engine: Engine, book_id: UUID, *, base_asset_code: str = "USD"
) -> None:
    _execute(
        engine,
        """
        insert into books (book_id, current_name, base_asset_code, write_state)
        values (:book_id, 'Primary book', :base_asset_code, 'active')
        """,
        {"book_id": book_id, "base_asset_code": base_asset_code},
    )


def _seed_books(engine: Engine) -> tuple[UUID, UUID]:
    first_book = uuid4()
    second_book = uuid4()
    _insert_asset(engine, "USD")
    _insert_asset(engine, "EUR")
    _insert_book(engine, first_book)
    _insert_book(engine, second_book, base_asset_code="EUR")
    return first_book, second_book


def test_catalog_tables_and_model_metadata_are_complete(pg_engine) -> None:
    from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

    load_v2_models()
    assert set(CATALOG_TABLES).issubset(V2Base.metadata.tables)

    with pg_engine.connect() as connection:
        relations = {
            name: connection.execute(
                text("select to_regclass(:relation)"),
                {"relation": f"public.{name}"},
            ).scalar_one()
            for name in CATALOG_TABLES
        }
        column_shapes = {
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    select table_name, column_name, data_type,
                           character_maximum_length
                      from information_schema.columns
                     where table_schema = 'public'
                       and (table_name, column_name) in (
                           ('assets', 'asset_code'),
                           ('books', 'book_id'),
                           ('accounts', 'account_id'),
                           ('categories', 'category_id'),
                           ('category_versions', 'category_version_id'),
                           ('protected_description_sidecars', 'sidecar_id')
                       )
                    """
                )
            )
        }

    assert all(relations.values())
    assert column_shapes == {
        ("accounts", "account_id", "uuid", None),
        ("assets", "asset_code", "character varying", 16),
        ("books", "book_id", "uuid", None),
        ("categories", "category_id", "uuid", None),
        ("category_versions", "category_version_id", "uuid", None),
        ("protected_description_sidecars", "sidecar_id", "uuid", None),
    }


def test_books_and_assets_enforce_identity_precision_and_mutable_display_state(
    pg_engine,
) -> None:
    _insert_asset(pg_engine, "USD", ledger_scale=8, input_scale=6, display_scale=6)
    book_id = uuid4()
    _insert_book(pg_engine, book_id)

    for statement, parameters in (
        (
            """
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('BAD1', 'fiat', -1, 0, 0, 'Bad', 'active')
            """,
            {},
        ),
        (
            """
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('BAD2', 'fiat', 31, 0, 0, 'Bad', 'active')
            """,
            {},
        ),
        (
            """
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('BAD3', 'fiat', 2, 3, 2, 'Bad', 'active')
            """,
            {},
        ),
        (
            """
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('BAD4', 'fiat', 2, 2, 3, 'Bad', 'active')
            """,
            {},
        ),
        (
            """
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('BAD5', ' ', 2, 2, 2, 'Bad', 'active')
            """,
            {},
        ),
        (
            """
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, ' ', 'USD', 'active')
            """,
            {"book_id": uuid4()},
        ),
        (
            """
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Bad state', 'USD', 'paused')
            """,
            {"book_id": uuid4()},
        ),
        (
            """
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Missing asset', 'ZZZ', 'active')
            """,
            {"book_id": uuid4()},
        ),
    ):
        _rejects_integrity(pg_engine, statement, parameters)

    # Identity and ledger precision are immutable even before any reference exists.
    _insert_asset(pg_engine, "UNREF", ledger_scale=4, input_scale=3, display_scale=2)
    _rejects_database_operation(
        pg_engine,
        "update assets set asset_code = 'MOVED' where asset_code = 'UNREF'",
        {},
    )
    _rejects_database_operation(
        pg_engine,
        "update assets set ledger_scale = 5 where asset_code = 'UNREF'",
        {},
    )
    _rejects_database_operation(
        pg_engine,
        "update books set book_id = :replacement where book_id = :book_id",
        {"replacement": uuid4(), "book_id": book_id},
    )

    _execute(
        pg_engine,
        """
        update assets
           set asset_code = asset_code,
               ledger_scale = ledger_scale,
               input_scale = 5,
               display_scale = 4,
               current_name = 'US Dollar',
               status = 'disabled'
         where asset_code = 'USD'
        """,
        {},
    )
    _execute(
        pg_engine,
        """
        update books
           set current_name = 'Renamed book', write_state = 'paused_integrity'
         where book_id = :book_id
        """,
        {"book_id": book_id},
    )

    with pg_engine.connect() as connection:
        asset = connection.execute(
            text(
                """
                select ledger_scale, input_scale, display_scale, current_name, status
                  from assets where asset_code = 'USD'
                """
            )
        ).one()
        book = connection.execute(
            text(
                "select current_name, write_state from books where book_id = :book_id"
            ),
            {"book_id": book_id},
        ).one()
    assert tuple(asset) == (8, 5, 4, "US Dollar", "disabled")
    assert tuple(book) == ("Renamed book", "paused_integrity")


def test_accounts_are_book_scoped_single_asset_and_have_unique_system_roles(
    pg_engine,
) -> None:
    first_book, second_book = _seed_books(pg_engine)
    account_id = uuid4()
    _execute(
        pg_engine,
        """
        insert into accounts (
            book_id, account_id, asset_code, account_type, system_role,
            current_name, status
        ) values (
            :book_id, :account_id, 'USD', 'asset', 'cash', 'Cash', 'active'
        )
        """,
        {"book_id": first_book, "account_id": account_id},
    )

    _rejects_integrity(
        pg_engine,
        """
        insert into accounts (
            book_id, account_id, asset_code, account_type, system_role,
            current_name, status
        ) values (
            :book_id, :account_id, 'USD', 'asset', 'cash', 'Duplicate', 'active'
        )
        """,
        {"book_id": first_book, "account_id": uuid4()},
    )
    # The same role is legal for another asset or Book.
    _execute(
        pg_engine,
        """
        insert into accounts (
            book_id, account_id, asset_code, account_type, system_role,
            current_name, status
        ) values (
            :book_id, :account_id, 'EUR', 'asset', 'cash', 'Euro cash', 'active'
        )
        """,
        {"book_id": first_book, "account_id": uuid4()},
    )
    _execute(
        pg_engine,
        """
        insert into accounts (
            book_id, account_id, asset_code, account_type, system_role,
            current_name, status
        ) values (
            :book_id, :account_id, 'USD', 'asset', 'cash', 'Other cash', 'active'
        )
        """,
        {"book_id": second_book, "account_id": uuid4()},
    )

    for assignment, parameters in (
        ("book_id = :value", {"value": second_book}),
        ("account_id = :value", {"value": uuid4()}),
        ("asset_code = :value", {"value": "EUR"}),
        ("system_role = :value", {"value": None}),
    ):
        _rejects_database_operation(
            pg_engine,
            f"update accounts set {assignment} "
            "where book_id = :book_id and account_id = :account_id",
            {"book_id": first_book, "account_id": account_id, **parameters},
        )

    _execute(
        pg_engine,
        """
        update accounts set current_name = 'Wallet', status = 'closed'
         where book_id = :book_id and account_id = :account_id
        """,
        {"book_id": first_book, "account_id": account_id},
    )
    with pg_engine.connect() as connection:
        unique_columns = {
            tuple(row[0])
            for row in connection.execute(
                text(
                    """
                    select array_agg(attribute.attname order by key.ordinality)
                      from pg_catalog.pg_constraint constraint_record
                      cross join lateral unnest(constraint_record.conkey)
                           with ordinality key(attnum, ordinality)
                      join pg_catalog.pg_attribute attribute
                        on attribute.attrelid = constraint_record.conrelid
                       and attribute.attnum = key.attnum
                     where constraint_record.conrelid = 'public.accounts'::regclass
                       and constraint_record.contype in ('p', 'u')
                     group by constraint_record.oid
                    """
                )
            )
        }
        partial_index = connection.execute(
            text(
                """
                select pg_get_expr(index_record.indpred, index_record.indrelid)
                  from pg_catalog.pg_index index_record
                 where index_record.indrelid = 'public.accounts'::regclass
                   and index_record.indisunique
                   and index_record.indpred is not null
                """
            )
        ).scalar_one()
    assert ("book_id", "account_id", "asset_code") in unique_columns
    assert "system_role IS NOT NULL" in partial_index


def test_categories_and_versions_keep_all_references_inside_the_book(pg_engine) -> None:
    first_book, second_book = _seed_books(pg_engine)
    parent_id = uuid4()
    child_id = uuid4()
    version_id = uuid4()
    _execute(
        pg_engine,
        """
        insert into categories (
            book_id, category_id, parent_category_id, current_name,
            current_version_id, status
        ) values (:book_id, :category_id, null, 'Parent', null, 'active')
        """,
        {"book_id": first_book, "category_id": parent_id},
    )
    _execute(
        pg_engine,
        """
        insert into categories (
            book_id, category_id, parent_category_id, current_name,
            current_version_id, status
        ) values (:book_id, :category_id, :parent_id, 'Child', null, 'active')
        """,
        {
            "book_id": first_book,
            "category_id": child_id,
            "parent_id": parent_id,
        },
    )
    _rejects_integrity(
        pg_engine,
        """
        insert into categories (
            book_id, category_id, parent_category_id, current_name,
            current_version_id, status
        ) values (:book_id, :category_id, :parent_id, 'Cross Book', null, 'active')
        """,
        {
            "book_id": second_book,
            "category_id": uuid4(),
            "parent_id": parent_id,
        },
    )

    _execute(
        pg_engine,
        """
        insert into category_versions (
            book_id, category_id, category_version_id, parent_category_id,
            name, status, change_reason_code
        ) values (
            :book_id, :category_id, :version_id, :parent_id,
            'Child v1', 'active', 'created'
        )
        """,
        {
            "book_id": first_book,
            "category_id": child_id,
            "version_id": version_id,
            "parent_id": parent_id,
        },
    )
    _rejects_integrity(
        pg_engine,
        """
        insert into category_versions (
            book_id, category_id, category_version_id, parent_category_id,
            name, status, change_reason_code
        ) values (
            :book_id, :category_id, :version_id, null,
            'Cross Book v1', 'active', 'created'
        )
        """,
        {
            "book_id": second_book,
            "category_id": child_id,
            "version_id": uuid4(),
        },
    )
    _execute(
        pg_engine,
        """
        update categories
           set current_name = 'Child renamed',
               current_version_id = :version_id,
               status = 'archived'
         where book_id = :book_id and category_id = :category_id
        """,
        {
            "book_id": first_book,
            "category_id": child_id,
            "version_id": version_id,
        },
    )
    with pg_engine.connect() as connection:
        unique_triple = connection.execute(
            text(
                """
                select exists(
                    select 1
                      from pg_catalog.pg_constraint constraint_record
                      cross join lateral unnest(constraint_record.conkey)
                           with ordinality key(attnum, ordinality)
                      join pg_catalog.pg_attribute attribute
                        on attribute.attrelid = constraint_record.conrelid
                       and attribute.attnum = key.attnum
                     where constraint_record.conrelid =
                           'public.category_versions'::regclass
                       and constraint_record.contype in ('p', 'u')
                     group by constraint_record.oid
                    having array_agg(attribute.attname order by key.ordinality) =
                           array['book_id', 'category_id', 'category_version_id']::name[]
                )
                """
            )
        ).scalar_one()
    assert unique_triple


def test_category_versions_are_append_only_even_for_the_schema_owner(
    pg_engine, migrated_postgres_database
) -> None:
    first_book, _ = _seed_books(pg_engine)
    category_id = uuid4()
    version_id = uuid4()
    _execute(
        pg_engine,
        """
        insert into categories (
            book_id, category_id, parent_category_id, current_name,
            current_version_id, status
        ) values (:book_id, :category_id, null, 'Category', null, 'active')
        """,
        {"book_id": first_book, "category_id": category_id},
    )
    _execute(
        pg_engine,
        """
        insert into category_versions (
            book_id, category_id, category_version_id, parent_category_id,
            name, status, change_reason_code
        ) values (
            :book_id, :category_id, :version_id, null,
            'Version', 'active', 'created'
        )
        """,
        {
            "book_id": first_book,
            "category_id": category_id,
            "version_id": version_id,
        },
    )

    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        for statement in (
            "update category_versions set name = 'Changed' "
            "where category_version_id = :version_id",
            "delete from category_versions where category_version_id = :version_id",
        ):
            with pytest.raises(DBAPIError):
                with owner_engine.begin() as connection:
                    connection.exec_driver_sql(
                        f'SET ROLE "{migrated_postgres_database.owner_role}"'
                    )
                    connection.execute(text(statement), {"version_id": version_id})
    finally:
        owner_engine.dispose()


def test_protected_description_sidecars_support_only_crypto_erasure(pg_engine) -> None:
    first_book, second_book = _seed_books(pg_engine)
    sidecar_id = uuid4()
    digest = b"h" * 32
    _execute(
        pg_engine,
        """
        insert into protected_description_sidecars (
            book_id, sidecar_id, kind, ciphertext, key_ref, nonce,
            algorithm, content_hash, status, erased_at
        ) values (
            :book_id, :sidecar_id, 'transaction_memo', :ciphertext, 'book-key-1',
            :nonce, 'AES-256-GCM', :content_hash, 'active', null
        )
        """,
        {
            "book_id": first_book,
            "sidecar_id": sidecar_id,
            "ciphertext": b"ciphertext",
            "nonce": b"n" * 12,
            "content_hash": digest,
        },
    )

    for statement, parameters in (
        (
            """
            insert into protected_description_sidecars (
                book_id, sidecar_id, kind, ciphertext, key_ref, nonce,
                algorithm, content_hash, status, erased_at
            ) values (
                :book_id, :sidecar_id, 'transaction_memo', null, null, null,
                'AES-256-GCM', :content_hash, 'active', null
            )
            """,
            {
                "book_id": first_book,
                "sidecar_id": uuid4(),
                "content_hash": digest,
            },
        ),
        (
            """
            insert into protected_description_sidecars (
                book_id, sidecar_id, kind, ciphertext, key_ref, nonce,
                algorithm, content_hash, status, erased_at
            ) values (
                :book_id, :sidecar_id, 'transaction_memo', :ciphertext, 'key',
                :nonce, 'AES-256-GCM', :content_hash, 'erased', :erased_at
            )
            """,
            {
                "book_id": first_book,
                "sidecar_id": uuid4(),
                "ciphertext": b"still-present",
                "nonce": b"n" * 12,
                "content_hash": digest,
                "erased_at": datetime.now(UTC),
            },
        ),
        (
            """
            insert into protected_description_sidecars (
                book_id, sidecar_id, kind, ciphertext, key_ref, nonce,
                algorithm, content_hash, status, erased_at
            ) values (
                :book_id, :sidecar_id, 'transaction_memo', null, null, null,
                'AES-256-GCM', :content_hash, 'erased', null
            )
            """,
            {
                "book_id": first_book,
                "sidecar_id": uuid4(),
                "content_hash": digest,
            },
        ),
    ):
        _rejects_integrity(pg_engine, statement, parameters)
    for bad_hash in (b"h" * 31, b"h" * 33):
        _rejects_integrity(
            pg_engine,
            """
            insert into protected_description_sidecars (
                book_id, sidecar_id, kind, ciphertext, key_ref, nonce,
                algorithm, content_hash, status, erased_at
            ) values (
                :book_id, :sidecar_id, 'transaction_memo', :ciphertext, 'key',
                :nonce, 'AES-256-GCM', :content_hash, 'active', null
            )
            """,
            {
                "book_id": first_book,
                "sidecar_id": uuid4(),
                "ciphertext": b"ciphertext",
                "nonce": b"n" * 12,
                "content_hash": bad_hash,
            },
        )

    _rejects_database_operation(
        pg_engine,
        """
        update protected_description_sidecars set book_id = :second_book
         where book_id = :first_book and sidecar_id = :sidecar_id
        """,
        {
            "first_book": first_book,
            "second_book": second_book,
            "sidecar_id": sidecar_id,
        },
    )
    _rejects_database_operation(
        pg_engine,
        """
        update protected_description_sidecars set sidecar_id = :replacement
         where book_id = :book_id and sidecar_id = :sidecar_id
        """,
        {
            "book_id": first_book,
            "sidecar_id": sidecar_id,
            "replacement": uuid4(),
        },
    )
    _execute(
        pg_engine,
        """
        update protected_description_sidecars
           set ciphertext = null, key_ref = null, nonce = null,
               status = 'erased', erased_at = :erased_at
         where book_id = :book_id and sidecar_id = :sidecar_id
        """,
        {
            "book_id": first_book,
            "sidecar_id": sidecar_id,
            "erased_at": datetime.now(UTC),
        },
    )
    _rejects_database_operation(
        pg_engine,
        """
        update protected_description_sidecars
           set ciphertext = :ciphertext, key_ref = 'new-key', nonce = :nonce,
               status = 'active', erased_at = null
         where book_id = :book_id and sidecar_id = :sidecar_id
        """,
        {
            "book_id": first_book,
            "sidecar_id": sidecar_id,
            "ciphertext": b"resurrected",
            "nonce": b"r" * 12,
        },
    )

    with pg_engine.connect() as connection:
        forbidden_columns = set(
            connection.execute(
                text(
                    """
                    select column_name
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name = 'protected_description_sidecars'
                       and column_name in (
                           'memo', 'description', 'plaintext', 'raw_description',
                           'purpose', 'secret', 'token'
                       )
                    """
                )
            ).scalars()
        )
    assert forbidden_columns == set()


def test_catalog_acl_is_exact_and_runtime_cannot_delete_or_manage_triggers(
    pg_engine, migrated_postgres_database
) -> None:
    table_list = ", ".join(f"'{name}'" for name in CATALOG_TABLES)
    with pg_engine.connect() as connection:
        grants = {
            (row.table_name, row.grantee, row.privilege_type, row.is_grantable)
            for row in connection.execute(
                text(
                    f"""
                    select relation.relname as table_name,
                           coalesce(grantee.rolname, 'PUBLIC') as grantee,
                           acl.privilege_type,
                           acl.is_grantable
                      from pg_catalog.pg_class relation
                      join pg_catalog.pg_namespace namespace
                        on namespace.oid = relation.relnamespace
                      cross join lateral pg_catalog.aclexplode(
                          coalesce(
                              relation.relacl,
                              pg_catalog.acldefault('r', relation.relowner)
                          )
                      ) acl
                      left join pg_catalog.pg_roles grantee
                        on grantee.oid = acl.grantee
                     where namespace.nspname = 'public'
                       and relation.relname in ({table_list})
                       and (
                           acl.grantee = 0
                           or grantee.rolname = :runtime_role
                       )
                    """
                ),
                {"runtime_role": migrated_postgres_database.runtime_role},
            )
        }

    expected = {
        (table_name, migrated_postgres_database.runtime_role, privilege, False)
        for table_name in CATALOG_TABLES
        for privilege in (
            ("SELECT", "INSERT")
            if table_name == "category_versions"
            else ("SELECT", "INSERT", "UPDATE")
        )
    }
    assert grants == expected

    for table_name in CATALOG_TABLES:
        _rejects_database_operation(
            pg_engine, f"delete from {table_name} where false", {}
        )
    for statement in (
        "alter table assets add column forbidden_runtime_ddl integer",
        "alter table assets disable trigger all",
    ):
        _rejects_database_operation(pg_engine, statement, {})


def test_catalog_foreign_keys_never_cascade_and_trigger_functions_are_hardened(
    pg_engine, migrated_postgres_database
) -> None:
    trigger_tables = (*CATALOG_TABLES, "oauth_device_grants")
    table_list = ", ".join(f"'{name}'" for name in trigger_tables)
    with pg_engine.connect() as connection:
        foreign_key_actions = {
            tuple(row)
            for row in connection.execute(
                text(
                    f"""
                    select source.relname, target.relname,
                           constraint_record.confupdtype,
                           constraint_record.confdeltype
                      from pg_catalog.pg_constraint constraint_record
                      join pg_catalog.pg_class source
                        on source.oid = constraint_record.conrelid
                      join pg_catalog.pg_class target
                        on target.oid = constraint_record.confrelid
                     where constraint_record.contype = 'f'
                       and source.relname in ({table_list})
                    """
                )
            )
        }
        trigger_rows = (
            connection.execute(
                text(
                    f"""
                select relation.relname as table_name,
                       function_record.proname as function_name,
                       function_record.prosecdef,
                       function_record.proconfig,
                       pg_catalog.pg_get_functiondef(function_record.oid) as definition,
                       exists(
                           select 1
                             from pg_catalog.aclexplode(
                                 coalesce(
                                     function_record.proacl,
                                     pg_catalog.acldefault(
                                         'f', function_record.proowner
                                     )
                                 )
                             ) function_acl
                             left join pg_catalog.pg_roles grantee
                               on grantee.oid = function_acl.grantee
                            where function_acl.privilege_type = 'EXECUTE'
                              and (
                                  function_acl.grantee = 0
                                  or grantee.rolname = :runtime_role
                              )
                       ) as broadly_executable
                  from pg_catalog.pg_trigger trigger_record
                  join pg_catalog.pg_class relation
                    on relation.oid = trigger_record.tgrelid
                  join pg_catalog.pg_proc function_record
                    on function_record.oid = trigger_record.tgfoid
                 where not trigger_record.tgisinternal
                   and relation.relname in ({table_list})
                 order by relation.relname, function_record.proname
                """
                ),
                {"runtime_role": migrated_postgres_database.runtime_role},
            )
            .mappings()
            .all()
        )
    assert foreign_key_actions
    assert all(
        update in {"a", "r"} and delete in {"a", "r"}
        for _, _, update, delete in foreign_key_actions
    )
    assert {row["table_name"] for row in trigger_rows} >= {
        "accounts",
        "assets",
        "books",
        "category_versions",
        "oauth_device_grants",
        "protected_description_sidecars",
    }
    for row in trigger_rows:
        assert row["prosecdef"] is False
        assert row["proconfig"] is not None
        assert any(
            setting.replace(" ", "") == "search_path=pg_catalog,public"
            for setting in row["proconfig"]
        )
        assert row["broadly_executable"] is False
    definitions = "\n".join(row["definition"] for row in trigger_rows).upper()
    assert "IS DISTINCT FROM" in definitions
