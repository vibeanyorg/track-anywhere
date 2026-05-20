from __future__ import annotations

import json

from track_anywhere_cli.main import EXIT_SUCCESS, EXIT_VALIDATION, main


def test_release_bump_dry_run_is_default_and_structured(tmp_path, capsys):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")

    exit_code = main(["--agent", "release", "bump", "--project-file", str(project_file)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["command"] == "release.bump"
    assert payload["data"]["dry_run"] is True
    assert payload["data"]["applied"] is False
    assert payload["data"]["current_version"] == "1.2.3"
    assert payload["data"]["next_version"] == "1.2.4"
    assert payload["data"]["requires_confirmation"] is True
    assert payload["data"]["confirmation"] == {"flag": "--confirm", "value": "1.2.4"}
    assert project_file.read_text(encoding="utf-8").endswith('version = "1.2.3"\n')


def test_release_bump_apply_requires_confirmation(tmp_path, capsys):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")

    exit_code = main(["--agent", "release", "bump", "--project-file", str(project_file), "--apply"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "confirmation_required"
    assert project_file.read_text(encoding="utf-8").endswith('version = "1.2.3"\n')


def test_release_bump_apply_updates_project_version(tmp_path, capsys):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")

    exit_code = main(
        [
            "--agent",
            "release",
            "bump",
            "--project-file",
            str(project_file),
            "--part",
            "minor",
            "--apply",
            "--confirm",
            "1.3.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["data"]["dry_run"] is False
    assert payload["data"]["applied"] is True
    assert payload["data"]["next_version"] == "1.3.0"
    assert payload["data"]["requires_confirmation"] is False
    assert project_file.read_text(encoding="utf-8").endswith('version = "1.3.0"\n')


def test_release_bump_supports_exact_target(tmp_path, capsys):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")

    exit_code = main(["release", "bump", "--project-file", str(project_file), "--to", "2.0.0", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == EXIT_SUCCESS
    assert payload["data"]["part"] is None
    assert payload["data"]["next_version"] == "2.0.0"


def test_release_bump_schema_is_discoverable(capsys):
    assert main(["schema", "release.bump", "--json"]) == EXIT_SUCCESS

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["command"] == ["ta", "release", "bump"]
    assert command["supports_dry_run"] is True
    assert command["side_effects"] == ["mutates:release.bump"]
    assert command["requires_auth"] is False
