from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.fixtures.monthly import (
    MonthlyScenario,
    post_classified_expense,
    seed_monthly_scenario,
)
from backend.tests.v2.postgres_factory import ProvisionedDatabase
from backend.tools.backfill_v1.reference_reducer import canonical_json_bytes
from backend.tools.backfill_v1.verify import verify_backfill, verify_target
from backend.tools.backfill_v1.verify_determinism import compare_verification_reports
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class VerifierScenario:
    database: ProvisionedDatabase
    monthly: MonthlyScenario
    original_transaction_id: UUID
    reversal_transaction_id: UUID
    usdt_transaction_id: UUID
    usdt_debit_posting_id: UUID
    other_book_category_id: UUID


def seed_verifier_target(database: ProvisionedDatabase) -> VerifierScenario:
    engine = create_engine(database.runtime_url, pool_pre_ping=True)
    try:
        monthly = seed_monthly_scenario(engine, actor_id="human:verifier-a")
        original_transaction_id = post_classified_expense(
            engine,
            monthly,
            effective_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
            amount="12.34",
        )
        factory = sessionmaker(engine, expire_on_commit=False)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(factory)

        reversal_transaction_id = uuid4()
        execute_reverse_transaction(
            ReverseTransactionCommand(
                book_id=monthly.journal.book_id,
                command_id=uuid4(),
                reversal_transaction_id=reversal_transaction_id,
                reverses_transaction_id=original_transaction_id,
                expected_stream_version=0,
                reason_code=ReversalReasonCode.IMPORT_CORRECTION,
                effective_at=datetime(2026, 1, 3, 3, 4, tzinfo=UTC),
            ),
            raw_key=f"verify-reverse:{reversal_transaction_id}",
            actor=monthly.actor,
            uow_factory=uow_factory,
        )

        usdt_debit_account_id = uuid4()
        usdt_credit_account_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into assets (asset_code, kind, ledger_scale, input_scale, "
                    "display_scale, current_name, status) values "
                    "('USDT', 'crypto', 8, 8, 8, 'Tether', 'active')"
                )
            )
            for account_id, name in (
                (usdt_debit_account_id, "USDT wallet"),
                (usdt_credit_account_id, "USDT equity"),
            ):
                connection.execute(
                    text(
                        "insert into accounts (book_id, account_id, asset_code, "
                        "account_type, current_name, status) values "
                        "(:book_id, :account_id, 'USDT', 'asset', :name, 'active')"
                    ),
                    {
                        "book_id": monthly.journal.book_id,
                        "account_id": account_id,
                        "name": name,
                    },
                )
        usdt_transaction_id = uuid4()
        usdt_debit_posting_id = uuid4()
        execute_post_transaction(
            PostTransactionCommand(
                book_id=monthly.journal.book_id,
                command_id=uuid4(),
                transaction_id=usdt_transaction_id,
                expected_stream_version=0,
                kind=TransactionKind.OPENING,
                postings=(
                    PostTransactionPosting(
                        posting_id=usdt_debit_posting_id,
                        account_id=usdt_debit_account_id,
                        asset_code="USDT",
                        side=PostingSide.DEBIT,
                        amount="1.12345678",
                    ),
                    PostTransactionPosting(
                        posting_id=uuid4(),
                        account_id=usdt_credit_account_id,
                        asset_code="USDT",
                        side=PostingSide.CREDIT,
                        amount="1.12345678",
                    ),
                ),
                effective_at=datetime(2026, 1, 4, 3, 4, tzinfo=UTC),
            ),
            raw_key=f"verify-usdt:{usdt_transaction_id}",
            actor=monthly.actor,
            uow_factory=uow_factory,
        )

        other = seed_monthly_scenario(engine, actor_id="human:verifier-b")
        return VerifierScenario(
            database=database,
            monthly=monthly,
            original_transaction_id=original_transaction_id,
            reversal_transaction_id=reversal_transaction_id,
            usdt_transaction_id=usdt_transaction_id,
            usdt_debit_posting_id=usdt_debit_posting_id,
            other_book_category_id=other.category_id,
        )
    finally:
        engine.dispose()


@pytest.fixture
def verifier_scenario(postgres_database_factory) -> VerifierScenario:
    database = postgres_database_factory.create(purpose="verifier", schema="v2")
    return seed_verifier_target(database)


def test_independent_sql_verifier_accepts_a_valid_target_and_is_deterministic(
    verifier_scenario: VerifierScenario,
) -> None:
    first = verify_target(verifier_scenario.database.runtime_url)
    second = verify_target(verifier_scenario.database.runtime_url)

    assert first.status == "PASS"
    assert first.issues == ()
    assert first.to_dict() == second.to_dict()
    assert first.counts["ledger_events"] == 4
    assert set(first.book_terminal_hashes) == {
        str(verifier_scenario.monthly.journal.book_id),
        # The second Book is intentionally empty and therefore has the zero hash.
        next(
            book_id
            for book_id, terminal_hash in first.book_terminal_hashes.items()
            if terminal_hash == "00" * 32
        ),
    }
    assert set(first.projection_hashes) >= {"journal", "reporting", "investments"}


def test_verifier_reimplements_hashing_without_production_imports() -> None:
    root = Path(__file__).resolve().parents[3]
    files = (
        root / "tools" / "backfill_v1" / "verify.py",
        root / "tools" / "backfill_v1" / "reference_reducer.py",
        root / "tools" / "backfill_v1" / "verify_determinism.py",
    )
    forbidden = {
        "track_anywhere.application",
        "track_anywhere.domain",
        "track_anywhere.infrastructure",
        "track_anywhere.serialization",
    }
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in forbidden
        )


def test_wrong_projection_time_has_a_stable_specific_code(
    verifier_scenario: VerifierScenario,
) -> None:
    engine = create_engine(verifier_scenario.database.migrator_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'SET ROLE "{verifier_scenario.database.owner_role}"'
            )
            connection.execute(
                text(
                    "update journal_transactions set effective_at = effective_at + "
                    ":delta where book_id=:book_id and transaction_id=:transaction_id"
                ),
                {
                    "book_id": verifier_scenario.monthly.journal.book_id,
                    "transaction_id": verifier_scenario.original_transaction_id,
                    "delta": timedelta(days=1),
                },
            )
            connection.exec_driver_sql("RESET ROLE")
    finally:
        engine.dispose()

    report = verify_target(verifier_scenario.database.runtime_url)
    assert "effective_time_mismatch" in report.issue_codes


def test_full_verifier_reads_source_target_manifest_and_writes_own_report(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    source = postgres_database_factory.create(purpose="verify-source", schema="empty")
    target = postgres_database_factory.create(purpose="verify-target", schema="v2")
    scenario = seed_verifier_target(target)
    target_report = verify_target(target.runtime_url)
    source_engine = create_engine(source.migrator_url)
    try:
        with source_engine.begin() as connection:
            connection.exec_driver_sql(f'SET ROLE "{source.owner_role}"')
            connection.exec_driver_sql(
                "create table public.alembic_version (version_num varchar(64) primary key)"
            )
            connection.execute(
                text("insert into public.alembic_version values ('v1-frozen')")
            )
            connection.exec_driver_sql(
                f'grant select on public.alembic_version to "{source.runtime_role}"'
            )
            connection.exec_driver_sql("RESET ROLE")
    finally:
        source_engine.dispose()
    content = {
        "dump_sha256": "d" * 64,
        "format_version": 1,
        "source_revision": "v1-frozen",
        "tables": [],
    }
    manifest_hash = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    snapshot_id = f"sha256:{manifest_hash}"
    manifest = {
        **content,
        "content_sha256": manifest_hash,
        "snapshot_id": snapshot_id,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    engine = create_engine(target.runtime_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into backfill_seals (snapshot_id, manifest_hash, "
                    "source_counts, terminal_book_hashes, quarantine_count, receipt_count) "
                    "values (:snapshot_id, :manifest_hash, cast(:source_counts as jsonb), "
                    "cast(:terminal_hashes as jsonb), 0, 0)"
                ),
                {
                    "snapshot_id": snapshot_id,
                    "manifest_hash": bytes.fromhex(manifest_hash),
                    "source_counts": json.dumps({}, separators=(",", ":")),
                    "terminal_hashes": json.dumps(
                        target_report.book_terminal_hashes,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            )
    finally:
        engine.dispose()

    output = tmp_path / "independent-verification.json"
    report = verify_backfill(
        source_url=source.runtime_url,
        target_url=target.runtime_url,
        manifest_path=manifest_path,
        output_path=output,
    )

    assert report.status == "PASS"
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report.to_dict()
    assert target.runtime_url not in output.read_text(encoding="utf-8")
    assert str(scenario.monthly.journal.book_id) in report.book_terminal_hashes


def test_determinism_comparison_covers_terminal_event_and_projection_hashes(
    verifier_scenario: VerifierScenario,
    tmp_path: Path,
) -> None:
    payload = verify_target(verifier_scenario.database.runtime_url).to_dict()
    run_a = tmp_path / "run-a.json"
    run_b = tmp_path / "run-b.json"
    run_a.write_bytes(canonical_json_bytes(payload) + b"\n")
    run_b.write_bytes(canonical_json_bytes(payload) + b"\n")

    assert compare_verification_reports(run_a, run_b).status == "PASS"

    changed = json.loads(run_b.read_text(encoding="utf-8"))
    changed["projection_hashes"]["events"] = "f" * 64
    run_b.write_bytes(canonical_json_bytes(changed) + b"\n")
    comparison = compare_verification_reports(run_a, run_b)
    assert comparison.status == "FAIL"
    assert [difference.field for difference in comparison.differences] == [
        "projection_hashes"
    ]
