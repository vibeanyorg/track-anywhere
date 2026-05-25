from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_category_update_dispatches_patch(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"category": {"category_id": "cat_1", "name": "Food"}, "idempotent_replay": False}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "ta_token", "category", "update", "cat_1", "--name", "Food", "--idempotency-key", "cat-update-1", "--json"]) == 0
    assert captured == {
        "method": "PATCH",
        "path": "/api/v1/categories/cat_1",
        "payload": {"name": "Food"},
        "key": "cat-update-1",
        "token": "ta_token",
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "category.update"


def test_category_find_by_path_dispatches_to_path_endpoint(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"category": {"category_id": "cat_1", "path_cache": "食品 / 外出吃饭"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "ta_token", "category", "find", "--kind", "expense", "--path", "食品 / 外出吃饭", "--json"]) == 0
    assert captured == {
        "method": "GET",
        "path": "/api/v1/categories/by-path?kind=expense&path=%E9%A3%9F%E5%93%81+%2F+%E5%A4%96%E5%87%BA%E5%90%83%E9%A5%AD",
        "payload": None,
        "key": None,
        "token": "ta_token",
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "category.find"
    assert payload["data"]["category"]["path_cache"] == "食品 / 外出吃饭"


def test_category_ensure_dispatches_mutating_path_endpoint(monkeypatch, capsys):
    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured.update({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"category": {"category_id": "cat_1", "path_cache": "食品 / 外出吃饭"}, "created": True}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "ta_token",
                "category",
                "ensure",
                "--kind",
                "expense",
                "--path",
                "食品 / 外出吃饭",
                "--idempotency-key",
                "category-ensure-1",
                "--json",
            ]
        )
        == 0
    )
    assert captured == {
        "method": "POST",
        "path": "/api/v1/categories/ensure-path",
        "payload": {"kind": "expense", "path": "食品 / 外出吃饭"},
        "key": "category-ensure-1",
        "token": "ta_token",
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "category.ensure"
