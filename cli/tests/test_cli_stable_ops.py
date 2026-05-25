from __future__ import annotations

import json
from pathlib import Path

import track_anywhere_cli.main as cli_main
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
