from __future__ import annotations

import json
from pathlib import Path

import track_anywhere_cli.data_backup as data_backup
import track_anywhere_cli.main as cli_main
from track_anywhere.posting_semantics import backup_posting_semantics_metadata
from track_anywhere_cli.main import EXIT_VALIDATION, main


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def test_data_backup_postgres_requires_target_transaction(capsys):
    exit_code = main(
        [
            "data",
            "backup",
            "--database-url",
            "postgresql+psycopg://track_anywhere:track_anywhere@127.0.0.1:5432/track_anywhere",
            "--json",
        ]
    )

    assert exit_code == EXIT_VALIDATION
    payload = _json_from_output(capsys.readouterr())
    assert payload["command"] == "data.backup"
    assert "requires --transaction-id" in payload["diagnostics"][0]["message"]


def test_data_backup_postgres_target_snapshot_includes_posting_semantics(monkeypatch, tmp_path, capsys):
    class FakeScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class FakeRows:
        def __init__(self, rows):
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

    class FakeRow:
        def __init__(self, **mapping):
            self._mapping = mapping

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement, params=None):
            sql = str(statement)
            if "current_database()" in sql:
                return FakeScalarResult("track_anywhere")
            if "current_schema()" in sql:
                return FakeScalarResult("public")
            if "alembic_version" in sql:
                return FakeScalarResult("0019_posting_constraints")
            if "count(*)" in sql:
                return FakeScalarResult(1)
            if "from transactions" in sql:
                return FakeRows([FakeRow(transaction_id="txn_1", book_id="book_default", memo="card purchase")])
            if "from transaction_lines" in sql:
                return FakeRows([FakeRow(transaction_id="txn_1", category_id="cat_1", category_version_id="catv_1")])
            if "from postings" in sql:
                return FakeRows(
                    [
                        FakeRow(
                            transaction_id="txn_1",
                            account_id="acc_card",
                            amount="11.08",
                            side="credit",
                            amount_semantics="debit_credit",
                        )
                    ]
                )
            if 'from "accounts"' in sql:
                return FakeRows([FakeRow(account_id="acc_card", type="liability", subtype="credit_card")])
            if 'from "categories"' in sql:
                return FakeRows([FakeRow(category_id="cat_1", kind="expense", name="Domains")])
            if 'from "category_versions"' in sql:
                return FakeRows([FakeRow(category_version_id="catv_1", category_id="cat_1")])
            return FakeRows([])

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(data_backup, "create_engine", lambda _database_url: FakeEngine())

    assert (
        main(
            [
                "data",
                "backup",
                "--database-url",
                "postgresql://track-anywhere.example/ledger",
                "--transaction-id",
                "txn_1",
                "--output-dir",
                str(tmp_path),
                "--label",
                "before-posting-cutover",
                "--json",
            ]
        )
        == 0
    )

    payload = _json_from_output(capsys.readouterr())
    backup_path = Path(payload["data"]["backup"]["backup_path"])
    backup_payload = json.loads(backup_path.read_text())
    assert backup_payload["posting_semantics"] == backup_posting_semantics_metadata()
    assert backup_payload["postings"][0]["amount_semantics"] == "debit_credit"
    assert backup_payload["postings"][0]["side"] == "credit"


def test_system_status_dispatches_authenticated_get(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"status": "ok", "database": "track_anywhere"}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "system", "status", "--include-counts", "--json"]) == 0
    assert captured == {
        "method": "GET",
        "path": "/api/v1/system/status?include_counts=true",
        "payload": None,
        "key": None,
        "token": "token-1",
    }
    assert _json_from_output(capsys.readouterr())["command"] == "system.status"


def test_posting_semantics_audit_dispatches_authenticated_get(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"cutover_ready": True, "counts": {"legacy_signed_postings": 0}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "system", "posting-semantics", "audit", "--json"]) == 0
    assert captured == {
        "method": "GET",
        "path": "/api/v1/system/posting-semantics-audit?book_id=book_default",
        "payload": None,
        "key": None,
        "token": "token-1",
    }
    assert _json_from_output(capsys.readouterr())["command"] == "system.posting_semantics.audit"


def test_posting_semantics_rewrite_dispatches_authenticated_post(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"status": "rewritten", "confirmed_postings_rewritten": 2}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "rewrite",
                "--idempotency-key",
                "rewrite-1",
                "--json",
            ]
        )
        == 0
    )
    assert captured == {
        "method": "POST",
        "path": "/api/v1/system/posting-semantics-rewrite?book_id=book_default",
        "payload": {},
        "key": "rewrite-1",
        "token": "token-1",
    }
    assert _json_from_output(capsys.readouterr())["command"] == "system.posting_semantics.rewrite"


def test_posting_semantics_resolve_dispatches_review_decisions(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"resolved_postings": 1}

    monkeypatch.setattr(cli_main, "request_json", fake_request)
    decision = {
        "record_ref": "txn_1",
        "position": 0,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-1",
                "--json",
            ]
        )
        == 0
    )
    assert captured == {
        "method": "POST",
        "path": "/api/v1/system/posting-semantics-review-resolutions?book_id=book_default",
        "payload": {"decisions": [decision]},
        "key": "resolve-1",
        "token": "token-1",
    }
    assert _json_from_output(capsys.readouterr())["command"] == "system.posting_semantics.resolve"


def test_posting_semantics_resolve_rejects_raw_target_side(capsys):
    decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
        "target_side": "credit",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-raw-side",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["command"] == "system.posting_semantics.resolve"
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "target_side" in payload["diagnostics"][0]["message"]


def test_posting_semantics_resolve_rejects_copied_recommendation_amount_semantics(capsys):
    decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "amount_semantics": "legacy_signed",
        "inferred_side_from_legacy_sign": "credit",
        "inferred_positive_amount": "9.36",
        "recommended_action": "manual_review_required_credit_card_semantics",
        "recommendation_reason": "choose whether this row represents charge or payment",
        "resolution_options": [
            {
                "action": "confirm_as_outstanding_liability",
                "target_side": "credit",
                "target_amount": "9.36",
            }
        ],
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-copied-recommendation",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["command"] == "system.posting_semantics.resolve"
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "read-only recommendation" in payload["diagnostics"][0]["message"]
    assert "amount_semantics" in payload["diagnostics"][0]["message"]


def test_posting_semantics_resolve_validates_review_decision_shape(capsys):
    decision = {
        "account_id": "cc_1",
        "position": 0,
        "currency": "USD",
        "legacy_amount": "0",
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-invalid-shape",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "record_ref or transaction_id" in payload["diagnostics"][0]["message"]


def test_posting_semantics_resolve_rejects_conflicting_record_refs(capsys):
    decision = {
        "record_ref": "draft:txn_1",
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-conflicting-ref",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "record_ref and transaction_id must match" in payload["diagnostics"][0]["message"]


def test_posting_semantics_resolve_requires_string_review_fields(capsys):
    decision = {
        "transaction_id": "txn_1",
        "position": 0,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": -9.36,
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-numeric-legacy-amount",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "legacy_amount as a non-empty string" in payload["diagnostics"][0]["message"]


def test_posting_semantics_resolve_rejects_boolean_position(capsys):
    decision = {
        "transaction_id": "txn_1",
        "position": True,
        "account_id": "cc_1",
        "currency": "USD",
        "legacy_amount": "-9.36",
        "action": "confirm_as_outstanding_liability",
    }

    assert (
        main(
            [
                "--token",
                "token-1",
                "system",
                "posting-semantics",
                "resolve",
                "--decision-json",
                json.dumps(decision),
                "--idempotency-key",
                "resolve-boolean-position",
                "--json",
            ]
        )
        == EXIT_VALIDATION
    )
    payload = _json_from_output(capsys.readouterr())
    assert payload["diagnostics"][0]["code"] == "invalid_posting_semantics_review_decisions"
    assert "position as a non-negative integer" in payload["diagnostics"][0]["message"]


def test_tx_snapshot_writes_output_file(monkeypatch, tmp_path, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"snapshot": {"transaction": {"transaction_id": "txn_1"}}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)
    output_path = tmp_path / "snapshot.json"

    assert main(["--token", "token-1", "tx", "snapshot", "txn_1", "--output", str(output_path), "--json"]) == 0
    assert captured == {
        "method": "GET",
        "path": "/api/v1/ledger/transactions/txn_1/snapshot",
        "payload": None,
        "key": None,
        "token": "token-1",
    }
    assert json.loads(output_path.read_text())["snapshot"]["transaction"]["transaction_id"] == "txn_1"
    payload = _json_from_output(capsys.readouterr())
    assert payload["command"] == "tx.snapshot"
    assert payload["data"]["snapshot_file"] == str(output_path)


def test_tx_reclassify_backup_before_snapshots_then_writes(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        if method == "GET":
            return 200, {"snapshot": {"transaction": {"transaction_id": "txn_1"}}}
        return 200, {"transaction": {"transaction_id": "txn_1", "lines": []}, "idempotent_replay": False}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "tx",
                "reclassify",
                "txn_1",
                "--category-id",
                "cat_2",
                "--line-id",
                "line_1",
                "--backup-before",
                "--backup-dir",
                str(tmp_path),
                "--backup-label",
                "before-food",
                "--idempotency-key",
                "tx-reclassify-1",
                "--json",
            ]
        )
        == 0
    )
    assert calls[0]["path"] == "/api/v1/ledger/transactions/txn_1/snapshot"
    assert calls[1]["payload"] == {"transaction_id": "txn_1", "category_id": "cat_2", "line_id": "line_1"}
    payload = _json_from_output(capsys.readouterr())
    backup_path = Path(payload["data"]["backup"]["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == tmp_path
    assert "before-food" in backup_path.name
