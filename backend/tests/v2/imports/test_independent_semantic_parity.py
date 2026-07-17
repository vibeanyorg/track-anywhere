from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tests.v2.imports.test_plan_archive import approved_source_and_review
from backend.tests.v2.postgres.test_frozen_import_catalog import seed_target_baseline
from backend.tools.frozen_v1_history.planner import (
    compile_frozen_financial_history_plan,
)
from backend.tools.frozen_v1_history.production_catalog import (
    seed_production_catalog,
)
from backend.tools.frozen_v1_history.reference_reducer import (
    ReferenceReductionError,
    SourceLedgerFacts,
    bind_source_reference,
    reduce_canonical_plan,
    reduce_frozen_source_rows,
)
from backend.tools.frozen_v1_history.verify import (
    FrozenHistoryObservation,
    FrozenHistoryVerificationError,
    read_frozen_history_observation,
    reduce_approved_source_reference,
    verify_frozen_history,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    canonical_plan_bytes,
    plan_sha256,
)
from track_anywhere.application.imports.import_frozen_financial_history import (
    import_frozen_financial_history,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.serialization.canonical_json import EventHashEnvelope, event_hash
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.verification import LedgerReadbackFacts, hash_verification_rows


EXPECTED_COUNTS = {
    "accounts": 121,
    "archives": 1,
    "assets": 20,
    "async_projection_rows": 30,
    "categories": 37,
    "category_versions": 37,
    "credit_card_transactions": 0,
    "descriptions": 138,
    "journal_postings": 290,
    "journal_transactions": 138,
    "ledger_events": 176,
    "quarantine": 0,
    "reporting_lines": 38,
    "reversals": 8,
    "synchronous_projection_applied_events": 176,
}

_FROZEN_UUID_NAMESPACE = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
_TARGET_BOOK_ID = "a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d"


def _source_uuid(kind: str, *parts: str) -> str:
    kind_namespace = uuid5(_FROZEN_UUID_NAMESPACE, kind)
    encoded = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return str(uuid5(kind_namespace, encoded))


def _minimal_raw_source() -> dict[str, object]:
    source_book_id = "source-book"
    return {
        "snapshot_id": "sha256:" + "1" * 64,
        "target_book_id": _TARGET_BOOK_ID,
        "tables": {
            "accounts": [
                {
                    "account_id": "cash",
                    "book_id": source_book_id,
                    "currency": "USDT",
                    "name": "cash",
                    "subtype": None,
                    "type": "asset",
                },
                {
                    "account_id": "card",
                    "book_id": source_book_id,
                    "currency": "USDT",
                    "name": "card",
                    "subtype": "credit_card",
                    "type": "liability",
                },
            ],
            "assets": [
                {
                    "asset_code": "USDT",
                    "display_scale": 6,
                    "kind": "crypto",
                    "name": "USDT",
                    "scale": 6,
                    "status": "active",
                }
            ],
            "categories": [],
            "category_versions": [],
            "ledger_books": [{"book_id": source_book_id}],
            "postings": [
                {
                    "account_id": "cash",
                    "amount": "0.12345678",
                    "amount_semantics": "legacy_signed",
                    "book_id": source_book_id,
                    "currency": "USDT",
                    "id": 1,
                    "position": 0,
                    "side": None,
                    "transaction_id": "tx-1",
                },
                {
                    "account_id": "card",
                    "amount": "-0.12345678",
                    "amount_semantics": "legacy_signed",
                    "book_id": source_book_id,
                    "currency": "USDT",
                    "id": 2,
                    "position": 1,
                    "side": None,
                    "transaction_id": "tx-1",
                },
            ],
            "transaction_lines": [],
            "transactions": [
                {
                    "book_id": source_book_id,
                    "memo": None,
                    "occurred_at": "2026-01-02T03:04:05.000006Z",
                    "purpose": "purchase",
                    "reversed_by": None,
                    "reverses_transaction_id": None,
                    "transaction_id": "tx-1",
                }
            ],
        },
        "review": {
            "exact_reversal_transaction_ids": [],
            "expected_card_balances": [
                {
                    "asset_code": "USDT",
                    "natural_units": 12345678,
                    "source_account_id": "card",
                }
            ],
            "posting_decisions": [],
            "retired_alias_account_ids": [],
        },
    }


def _minimal_expected_postings() -> list[dict[str, object]]:
    source_book_id = "source-book"
    snapshot_id = "sha256:" + "1" * 64
    target_book_id = _TARGET_BOOK_ID
    transaction_id = _source_uuid("transaction", snapshot_id, source_book_id, "tx-1")
    return [
        {
            "account_id": _source_uuid("account", source_book_id, "cash"),
            "asset_code": "USDT",
            "book_id": target_book_id,
            "position": 0,
            "posting_id": _source_uuid("posting", source_book_id, "tx-1", "1"),
            "side": "debit",
            "transaction_id": transaction_id,
            "units": "12345678",
        },
        {
            "account_id": _source_uuid("account", source_book_id, "card"),
            "asset_code": "USDT",
            "book_id": target_book_id,
            "position": 1,
            "posting_id": _source_uuid("posting", source_book_id, "tx-1", "2"),
            "side": "credit",
            "transaction_id": transaction_id,
            "units": "12345678",
        },
    ]


def _canonical_plan_object() -> dict[str, object]:
    parsed = json.loads(canonical_plan_bytes(_fixed_plan()))
    assert type(parsed) is dict
    return parsed


def _swap_independent_events_and_reseal(raw: dict[str, object]) -> dict[str, object]:
    mutated = json.loads(json.dumps(raw))
    events = mutated["events"]
    assert type(events) is list
    assert all(type(event) is dict for event in events)
    assert (
        events[0]["event_type"] == events[1]["event_type"] == "JournalTransactionPosted"
    )
    events[0], events[1] = events[1], events[0]

    hashes_by_event_id: dict[str, bytes] = {}
    previous_hash = bytes(32)
    book_id = UUID(str(mutated["target_book_id"]))
    for position, event in enumerate(events, start=1):
        assert type(event) is dict
        payload = event["payload"]
        assert type(payload) is dict
        if event["event_type"] == "JournalTransactionReversed":
            original_id = str(payload["original_event_id"])
            payload["original_event_hash"] = hashes_by_event_id[original_id].hex()
        event["book_position"] = position
        event["previous_hash"] = previous_hash.hex()
        effective_at = datetime.fromisoformat(
            str(event["effective_at"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        calculated = event_hash(
            EventHashEnvelope(
                event_id=UUID(str(event["event_id"])),
                book_id=book_id,
                book_position=position,
                global_sequence=1,
                recorded_at=datetime(1970, 1, 1, tzinfo=UTC),
                stream_type=str(event["stream_type"]),
                stream_id=UUID(str(event["stream_id"])),
                stream_version=int(event["stream_version"]),
                event_type=str(event["event_type"]),
                event_schema_version=int(event["event_schema_version"]),
                command_id=UUID(str(event["command_id"])),
                actor_subject_id=str(event["actor_subject_id"]),
                correlation_id=UUID(str(event["correlation_id"])),
                causation_event_id=(
                    None
                    if event["causation_event_id"] is None
                    else UUID(str(event["causation_event_id"]))
                ),
                effective_at=effective_at,
                previous_hash=previous_hash,
            ),
            payload,
        )
        event["event_hash"] = calculated.hex()
        hashes_by_event_id[str(event["event_id"])] = calculated
        previous_hash = calculated
    mutated["expected_terminal_hash"] = previous_hash.hex()
    return mutated


def _fixed_plan():
    return build_valid_fixture_plan(
        target_book_id=UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
    )


def _cipher() -> ProtectedContentCipher:
    return ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="v1",
            keys={"v1": bytes(range(32))},
        )
    )


def test_reference_reducer_has_no_shared_planner_or_projection_dependency() -> None:
    from backend.tools.frozen_v1_history import reference_reducer

    source_path = Path(reference_reducer.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert all(
        module.split(".", 1)[0]
        in {
            "__future__",
            "base64",
            "collections",
            "dataclasses",
            "datetime",
            "decimal",
            "hashlib",
            "json",
            "re",
            "types",
            "typing",
            "uuid",
        }
        for module in imported
    )


def test_raw_source_oracle_does_not_follow_balanced_planner_common_mode_errors() -> (
    None
):
    source = reduce_frozen_source_rows(_minimal_raw_source())
    correct = _minimal_expected_postings()

    assert source.hashes["journal_postings"] == hash_verification_rows(correct)

    wrong_accounts = [dict(row) for row in correct]
    wrong_accounts[0]["account_id"], wrong_accounts[1]["account_id"] = (
        wrong_accounts[1]["account_id"],
        wrong_accounts[0]["account_id"],
    )
    rounded_usdt = [dict(row, units="1234567") for row in correct]

    assert len(wrong_accounts) == len(correct) == len(rounded_usdt)
    assert hash_verification_rows(wrong_accounts) != source.hashes["journal_postings"]
    assert hash_verification_rows(rounded_usdt) != source.hashes["journal_postings"]
    assert hash_verification_rows(rounded_usdt) != source.hashes["usdt_postings"]

    auxiliary = reduce_canonical_plan(_canonical_plan_object())
    expected_hashes = dict(auxiliary.hashes)
    expected_hashes.update(
        {key: source.hashes[key] for key in ("journal_postings", "usdt_postings")}
    )
    reference = replace(auxiliary, hashes=expected_hashes)
    observed_hashes = dict(reference.hashes)
    observed_hashes["journal_postings"] = hash_verification_rows(wrong_accounts)
    observed_hashes["usdt_postings"] = hash_verification_rows(rounded_usdt)
    ledger_counts = {
        key: value
        for key, value in reference.counts.items()
        if key not in {"archives", "descriptions", "quarantine"}
    }
    observation = FrozenHistoryObservation(
        ledger=LedgerReadbackFacts(
            book_id=reference.book_id,
            terminal_position=reference.terminal_position,
            terminal_hash=reference.terminal_hash,
            counts=ledger_counts,
            hashes=observed_hashes,
            async_checkpoint_position=reference.terminal_position,
            unresolved_projection_failures=0,
        ),
        additional_counts={"archives": 1, "descriptions": 1, "quarantine": 0},
        description_aggregate_sha256=reference.description_aggregate_sha256,
        archive_plaintext_sha256=reference.archive_plaintext_sha256,
        archive_metadata_hash=reference.archive_metadata_hash,
        archive_seal="a" * 64,
        archive_verified=True,
    )

    report = verify_frozen_history(reference, observation)
    assert report.status == "FAIL"
    assert "journal_postings_digest_mismatch" in report.issues
    assert "usdt_postings_digest_mismatch" in report.issues


def test_raw_source_oracle_derives_event_journal_reference_and_terminal_facts() -> None:
    source = reduce_frozen_source_rows(_minimal_raw_source())

    assert source.terminal_position == 1
    assert len(source.terminal_hash) == 64
    assert source.terminal_hash != "0" * 64
    assert {
        "async_projection",
        "balances",
        "event_order",
        "event_payloads",
        "events",
        "external_references",
        "journal",
        "journal_postings",
        "journal_transactions",
        "reversals",
        "synchronous_projection",
    }.issubset(source.hashes)
    assert all(len(source.hashes[key]) == 64 for key in source.hashes)
    assert source.hashes["journal"] != source.hashes["journal_postings"]
    expected_balances = sorted(
        (
            {
                "account_id": posting["account_id"],
                "as_of_position": 1,
                "asset_code": posting["asset_code"],
                "balance_units": (
                    posting["units"]
                    if posting["side"] == "debit"
                    else f"-{posting['units']}"
                ),
                "book_id": posting["book_id"],
            }
            for posting in _minimal_expected_postings()
        ),
        key=lambda row: (str(row["account_id"]), str(row["asset_code"])),
    )
    assert source.hashes["balances"] == hash_verification_rows(expected_balances)
    assert source.counts["async_projection_rows"] == 0
    assert source.hashes["async_projection"] == hash_verification_rows([])


def test_source_oracle_rejects_a_validly_reordered_and_resealed_plan() -> None:
    original = reduce_canonical_plan(_canonical_plan_object())
    source = SourceLedgerFacts(
        book_id=original.book_id,
        terminal_position=original.terminal_position,
        terminal_hash=original.terminal_hash,
        counts=original.counts,
        hashes=original.hashes,
        description_ids=original.description_ids,
        description_aggregate_sha256=original.description_aggregate_sha256,
    )
    mutated = reduce_canonical_plan(
        _swap_independent_events_and_reseal(_canonical_plan_object())
    )

    assert mutated.hashes["events"] != source.hashes["events"]
    assert mutated.terminal_hash != source.terminal_hash
    with pytest.raises(ReferenceReductionError, match="source_plan_integrity_mismatch"):
        bind_source_reference(mutated, source)


def test_fixed_raw_source_oracle_rejects_a_validly_reordered_and_resealed_plan() -> (
    None
):
    frozen_source, review = approved_source_and_review()
    plan = compile_frozen_financial_history_plan(source=frozen_source, review=review)
    source = reduce_approved_source_reference(
        source=frozen_source,
        review=review,
        target_book_id=plan.target_book_id,
    )
    raw_plan = json.loads(canonical_plan_bytes(plan))
    assert type(raw_plan) is dict
    mutated = reduce_canonical_plan(_swap_independent_events_and_reseal(raw_plan))

    with pytest.raises(ReferenceReductionError, match="source_plan_integrity_mismatch"):
        bind_source_reference(mutated, source)


def test_reference_reducer_derives_the_pinned_semantics_from_raw_plan_facts() -> None:
    raw = _canonical_plan_object()

    reference = reduce_canonical_plan(raw)

    assert dict(reference.counts) == EXPECTED_COUNTS
    assert reference.terminal_position == 176
    assert reference.terminal_hash == raw["expected_terminal_hash"]
    assert set(reference.hashes) == {
        "account_balances_semantic",
        "accounts",
        "assets",
        "async_projection",
        "balances",
        "cards",
        "categories",
        "event_order",
        "event_payloads",
        "events",
        "external_references",
        "journal",
        "journal_postings",
        "journal_transactions",
        "reporting",
        "reversal_semantic",
        "reversals",
        "synchronous_projection",
        "usdt_postings",
    }
    assert all(len(value) == 64 for value in reference.hashes.values())
    assert len(reference.description_aggregate_sha256) == 64
    assert len(reference.archive_plaintext_sha256) == 64
    assert len(reference.archive_metadata_hash) == 64
    rendered = repr(reference)
    assert "fixture-purpose" not in rendered
    assert "canonical_plaintext" not in rendered


def test_reference_reducer_uses_explicit_natural_balance_equations() -> None:
    raw = _canonical_plan_object()
    accounts = raw["accounts"]
    assert type(accounts) is list and type(accounts[1]) is dict
    accounts[1]["expected_natural_units"] = 2

    with pytest.raises(
        ReferenceReductionError,
        match="account_natural_balance_mismatch",
    ):
        reduce_canonical_plan(raw)


def _seed_production_target(pg_engine, plan) -> None:
    seed_production_catalog(
        pg_engine.url.render_as_string(hide_password=False),
        plan,
        expected_plan_sha256=plan_sha256(plan),
    )


def _import_and_project(
    pg_engine,
    *,
    plan,
    cipher,
    raw_key: str,
    seed_target=seed_target_baseline,
) -> None:
    seed_target(pg_engine, plan)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    import_frozen_financial_history(
        plan,
        expected_plan_hash=plan_sha256(plan),
        raw_key=raw_key,
        actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        protected_content_cipher=cipher,
    )
    worker = AsyncProjectionWorker(session_factory)
    while worker.run_once(plan.target_book_id).processed_events:
        pass


def test_pg17_import_matches_independent_authorized_semantic_readback(
    pg_engine,
) -> None:
    source, review = approved_source_and_review()
    plan = compile_frozen_financial_history_plan(source=source, review=review)
    cipher = _cipher()
    _import_and_project(
        pg_engine,
        plan=plan,
        cipher=cipher,
        raw_key="independent-real-source-semantic-parity",
        seed_target=_seed_production_target,
    )

    raw_plan = json.loads(canonical_plan_bytes(plan))
    assert type(raw_plan) is dict
    auxiliary = reduce_canonical_plan(raw_plan)
    source_facts = reduce_approved_source_reference(
        source=source,
        review=review,
        target_book_id=plan.target_book_id,
    )
    reference = bind_source_reference(auxiliary, source_facts)
    with Session(pg_engine) as session:
        observation = read_frozen_history_observation(
            session,
            reference=reference,
            cipher=cipher,
        )

    raw_events = raw_plan["events"]
    assert type(raw_events) is list
    expected_order = [
        {
            "book_position": event["book_position"],
            "event_id": event["event_id"],
        }
        for event in raw_events
    ]
    expected_payloads = [
        {
            "event_id": event["event_id"],
            "event_schema_version": event["event_schema_version"],
            "event_type": event["event_type"],
            "payload": event["payload"],
        }
        for event in raw_events
    ]
    assert observation.ledger.hashes["event_order"] == hash_verification_rows(
        expected_order
    )
    assert observation.ledger.hashes["event_payloads"] == hash_verification_rows(
        expected_payloads
    )

    report = verify_frozen_history(reference, observation)
    assert report.status == "PASS", report.issues
    assert dict(report.counts) == EXPECTED_COUNTS


_DATABASE_MUTATIONS = (
    (
        "journal_postings",
        """
        UPDATE journal_postings
        SET side = (
          CASE WHEN side = 'debit' THEN 'credit' ELSE 'debit' END
        )::posting_side
        WHERE posting_id = (
          SELECT posting_id FROM journal_postings
          WHERE book_id = :book_id AND asset_code <> 'USDT'
          ORDER BY posting_id LIMIT 1
        )
        """,
        "journal_postings_digest_mismatch",
    ),
    (
        "journal_postings",
        """
        UPDATE journal_postings
        SET units = units + 1
        WHERE posting_id = (
          SELECT posting_id FROM journal_postings
          WHERE book_id = :book_id AND asset_code <> 'USDT'
          ORDER BY posting_id LIMIT 1
        )
        """,
        "journal_postings_digest_mismatch",
    ),
    (
        "journal_postings",
        """
        UPDATE journal_postings AS posting
        SET account_id = (
          SELECT account.account_id FROM accounts AS account
          WHERE account.book_id = posting.book_id
            AND account.asset_code = posting.asset_code
            AND account.account_id <> posting.account_id
          ORDER BY account.account_id LIMIT 1
        )
        WHERE posting.posting_id = (
          SELECT posting_id FROM journal_postings
          WHERE book_id = :book_id ORDER BY posting_id LIMIT 1
        )
        """,
        "journal_postings_digest_mismatch",
    ),
    (
        "journal_postings",
        """
        UPDATE journal_postings
        SET asset_code = (
          SELECT asset_code FROM assets
          WHERE asset_code <> journal_postings.asset_code
          ORDER BY asset_code LIMIT 1
        )
        WHERE posting_id = (
          SELECT posting_id FROM journal_postings
          WHERE book_id = :book_id ORDER BY posting_id LIMIT 1
        )
        """,
        "journal_postings_digest_mismatch",
    ),
    (
        "journal_postings",
        """
        UPDATE journal_postings SET units = units + 1
        WHERE posting_id = (
          SELECT posting_id FROM journal_postings
          WHERE book_id = :book_id AND asset_code = 'USDT'
          ORDER BY posting_id LIMIT 1
        )
        """,
        "usdt_postings_digest_mismatch",
    ),
    (
        "ledger_events",
        """
        UPDATE ledger_events SET stream_version = stream_version + 1
        WHERE book_id = :book_id AND book_position = 1
        """,
        "events_digest_mismatch",
    ),
    (
        "ledger_events",
        """
        UPDATE ledger_events SET book_position = 177
        WHERE book_id = :book_id AND book_position = 1
        """,
        "event_order_digest_mismatch",
    ),
    (
        "ledger_events",
        """
        UPDATE ledger_events
        SET payload = payload || jsonb_build_object('tampered', true)
        WHERE book_id = :book_id AND book_position = 1
        """,
        "event_payloads_digest_mismatch",
    ),
    (
        "ledger_events",
        """
        UPDATE ledger_events SET event_hash = decode(repeat('00', 32), 'hex')
        WHERE book_id = :book_id AND book_position = 1
        """,
        "events_digest_mismatch",
    ),
    (
        "book_event_heads",
        """
        UPDATE book_event_heads SET last_hash = decode(repeat('00', 32), 'hex')
        WHERE book_id = :book_id
        """,
        "terminal_hash_mismatch",
    ),
    (
        "transaction_reversals",
        """
        UPDATE transaction_reversals AS reversal
        SET original_event_id = (
          SELECT event_id FROM ledger_events
          WHERE book_id = :book_id
            AND event_id <> reversal.original_event_id
          ORDER BY book_position LIMIT 1
        )
        WHERE reversal_transaction_id = (
          SELECT reversal_transaction_id FROM transaction_reversals
          WHERE book_id = :book_id ORDER BY reversal_transaction_id LIMIT 1
        )
        """,
        "reversals_digest_mismatch",
    ),
    (
        "transaction_reversals",
        """
        UPDATE transaction_reversals
        SET original_event_hash = decode(repeat('00', 32), 'hex')
        WHERE reversal_transaction_id = (
          SELECT reversal_transaction_id FROM transaction_reversals
          WHERE book_id = :book_id ORDER BY reversal_transaction_id LIMIT 1
        )
        """,
        "reversals_digest_mismatch",
    ),
    (
        "reporting_lines",
        """
        UPDATE reporting_lines AS line
        SET dimension_id = (
          SELECT category_id FROM categories
          WHERE book_id = :book_id AND category_id <> line.dimension_id
          ORDER BY category_id LIMIT 1
        )
        WHERE line_id = (
          SELECT line_id FROM reporting_lines
          WHERE book_id = :book_id ORDER BY line_id LIMIT 1
        )
        """,
        "reporting_digest_mismatch",
    ),
    (
        "reporting_lines",
        """
        UPDATE reporting_lines AS line
        SET catalog_id = (
          SELECT category_version_id FROM category_versions
          WHERE book_id = :book_id AND category_version_id <> line.catalog_id
          ORDER BY category_version_id LIMIT 1
        )
        WHERE line_id = (
          SELECT line_id FROM reporting_lines
          WHERE book_id = :book_id ORDER BY line_id LIMIT 1
        )
        """,
        "reporting_digest_mismatch",
    ),
    (
        "accounts",
        """
        UPDATE accounts
        SET status = CASE WHEN status = 'active' THEN 'closed' ELSE 'active' END
        WHERE account_id = (
          SELECT account_id FROM accounts
          WHERE book_id = :book_id AND account_subtype = 'credit_card'
          ORDER BY account_id LIMIT 1
        ) AND book_id = :book_id
        """,
        "cards_digest_mismatch",
    ),
    (
        "account_balances",
        """
        UPDATE account_balances SET balance_units = balance_units + 1
        WHERE (book_id, account_id, asset_code) = (
          SELECT book_id, account_id, asset_code FROM account_balances
          WHERE book_id = :book_id ORDER BY account_id, asset_code LIMIT 1
        )
        """,
        "account_balances_semantic_digest_mismatch",
    ),
    (
        "projection_checkpoints",
        """
        UPDATE projection_checkpoints
        SET last_book_position = last_book_position - 1
        WHERE book_id = :book_id
        """,
        "async_checkpoint_mismatch",
    ),
    (
        "monthly_category_summaries",
        """
        UPDATE monthly_category_summaries SET units = units + 1
        WHERE ctid = (
          SELECT ctid FROM monthly_category_summaries
          WHERE book_id = :book_id ORDER BY ctid LIMIT 1
        )
        """,
        "async_projection_digest_mismatch",
    ),
)


def test_database_mutation_sql_has_only_the_explicit_book_id_bind() -> None:
    for table_name, statement, _expected_issue in _DATABASE_MUTATIONS:
        assert set(text(statement).compile().params) == {"book_id"}, table_name


def test_pg17_verifier_rejects_each_single_stored_fact_mutation(
    pg_engine,
    migrated_postgres_database,
) -> None:
    plan = _fixed_plan()
    cipher = _cipher()
    _import_and_project(
        pg_engine,
        plan=plan,
        cipher=cipher,
        raw_key="stored-field-mutation-source",
    )
    reference = reduce_canonical_plan(_canonical_plan_object())
    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        for table_name, statement, expected_issue in _DATABASE_MUTATIONS:
            with admin_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} DISABLE TRIGGER ALL")
                    )
                    result = connection.execute(
                        text(statement), {"book_id": str(plan.target_book_id)}
                    )
                    assert result.rowcount == 1, table_name
                    with Session(
                        bind=connection,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        observation = read_frozen_history_observation(
                            session,
                            reference=reference,
                            cipher=cipher,
                        )
                    report = verify_frozen_history(reference, observation)
                    assert report.status == "FAIL", table_name
                    assert expected_issue in report.issues, table_name
                finally:
                    transaction.rollback()
    finally:
        admin_engine.dispose()


def test_pg17_authorized_description_readback_rejects_ciphertext_mutation(
    pg_engine,
    migrated_postgres_database,
) -> None:
    plan = _fixed_plan()
    cipher = _cipher()
    _import_and_project(
        pg_engine,
        plan=plan,
        cipher=cipher,
        raw_key="description-mutation-source",
    )
    reference = reduce_canonical_plan(_canonical_plan_object())
    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        with admin_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        "ALTER TABLE protected_description_sidecars DISABLE TRIGGER ALL"
                    )
                )
                result = connection.execute(
                    text(
                        """
                        UPDATE protected_description_sidecars
                        SET ciphertext = set_byte(
                          ciphertext, 0, (get_byte(ciphertext, 0) + 1) % 256
                        )
                        WHERE sidecar_id = (
                          SELECT sidecar_id FROM protected_description_sidecars
                          WHERE book_id = :book_id
                            AND kind = 'transaction_description'
                          ORDER BY sidecar_id LIMIT 1
                        ) AND book_id = :book_id
                        """
                    ),
                    {"book_id": str(plan.target_book_id)},
                )
                assert result.rowcount == 1
                with Session(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    with pytest.raises(
                        FrozenHistoryVerificationError,
                        match="^description_readback_failed$",
                    ):
                        read_frozen_history_observation(
                            session,
                            reference=reference,
                            cipher=cipher,
                        )
            finally:
                transaction.rollback()
    finally:
        admin_engine.dispose()


def test_pg17_archive_seal_is_separately_tamper_evident(
    pg_engine,
    migrated_postgres_database,
) -> None:
    plan = _fixed_plan()
    cipher = _cipher()
    _import_and_project(
        pg_engine,
        plan=plan,
        cipher=cipher,
        raw_key="archive-seal-mutation-source",
    )
    reference = reduce_canonical_plan(_canonical_plan_object())
    admin_engine = create_engine(migrated_postgres_database.admin_url)
    try:
        with admin_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text("ALTER TABLE import_archive_manifests DISABLE TRIGGER ALL")
                )
                result = connection.execute(
                    text(
                        """
                        UPDATE import_archive_manifests
                        SET seal = set_byte(seal, 0, (get_byte(seal, 0) + 1) % 256)
                        WHERE book_id = :book_id
                        """
                    ),
                    {"book_id": str(plan.target_book_id)},
                )
                assert result.rowcount == 1
                with Session(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    with pytest.raises(
                        FrozenHistoryVerificationError,
                        match="^archive_readback_failed$",
                    ):
                        read_frozen_history_observation(
                            session,
                            reference=reference,
                            cipher=cipher,
                        )
            finally:
                transaction.rollback()
    finally:
        admin_engine.dispose()
