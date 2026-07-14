from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
MATRIX = ROOT / "docs/operations/v2-capability-matrix.md"
MANIFEST = ROOT / "docs/operations/v2-retirement-manifest.md"

REQUIRED_CAPABILITIES = {
    "Auth",
    "Book and Book membership",
    "Assets, Accounts, and category versions",
    "Drafts",
    "Counterparties",
    "Projects",
    "Journal",
    "Reversal and correction",
    "External references",
    "Classification",
    "FX",
    "Investment lots",
    "Valuations",
    "Monthly reports",
    "Budgets",
    "Search",
    "CLI",
    "Attachments",
    "Imports and quarantine",
    "Recurring rules",
    "Payment instruments and tools",
    "Backup and restore",
    "System and operations configuration",
}
VALID_STATUSES = {"implemented", "deferred", "removed"}


def _table(path: Path, header: tuple[str, ...]) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    expected_header = list(header)
    rows: list[dict[str, str]] = []
    in_table = False
    for line in lines:
        if not line.startswith("|"):
            if in_table:
                break
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells == expected_header:
            in_table = True
            continue
        if not in_table or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if len(cells) != len(header):
            raise AssertionError(f"malformed table row in {path}: {line}")
        rows.append(dict(zip(header, cells, strict=True)))
    assert rows, f"missing {header!r} table in {path}"
    return rows


def _repo_links(value: str) -> list[Path]:
    links = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", value)
    return [(MATRIX.parent / link).resolve() for link in links]


def _manifest_path(value: str) -> str:
    assert value.startswith("`") and value.endswith("`"), value
    return value[1:-1]


def test_every_required_capability_has_exactly_one_reviewed_status() -> None:
    rows = _table(
        MATRIX,
        ("Capability", "Status", "Owner", "Test", "Evidence", "Reason"),
    )
    by_capability: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_capability.setdefault(row["Capability"], []).append(row)

    assert set(by_capability) == REQUIRED_CAPABILITIES
    assert all(len(entries) == 1 for entries in by_capability.values())
    for capability, (row,) in by_capability.items():
        assert row["Status"] in VALID_STATUSES, capability
        if row["Status"] == "implemented":
            assert row["Owner"] not in {"", "—", "TBD"}, capability
            assert _repo_links(row["Test"]), capability
            assert _repo_links(row["Evidence"]), capability
            for target in _repo_links(row["Test"] + row["Evidence"]):
                assert target.is_file(), f"{capability} evidence does not exist: {target}"
        else:
            assert row["Reason"] not in {"", "—", "TBD"}, capability


def test_retirement_manifest_accounts_for_every_root_runtime_entry_and_test() -> None:
    rows = _table(
        MANIFEST,
        ("Disposition", "Path", "V2 consumer", "Rationale"),
    )
    entries = [_manifest_path(row["Path"]) for row in rows]
    assert len(entries) == len(set(entries)), "manifest paths must be unique"
    dispositions = {"retain", "rewrite", "delete"}
    assert {row["Disposition"] for row in rows} <= dispositions

    runtime_root = ROOT / "backend/app/track_anywhere"
    actual_runtime = {
        f"backend/app/track_anywhere/{child.name}{'/' if child.is_dir() else ''}"
        for child in runtime_root.iterdir()
        if child.name != "__pycache__"
    }
    manifested_runtime = {
        entry
        for entry in entries
        if entry.startswith("backend/app/track_anywhere/")
        and "/"
        not in entry.removeprefix("backend/app/track_anywhere/").rstrip("/")
    }
    assert actual_runtime <= manifested_runtime

    legacy_tests = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "backend/tests").glob("test_*.py")
    }
    deletion_rows = {
        _manifest_path(row["Path"])
        for row in rows
        if row["Disposition"] == "delete"
    }
    assert legacy_tests <= deletion_rows


def test_every_retained_sensitive_or_cli_utility_names_a_v2_consumer() -> None:
    rows = _table(
        MANIFEST,
        ("Disposition", "Path", "V2 consumer", "Rationale"),
    )
    protected_prefixes = (
        "backend/app/track_anywhere/auth",
        "backend/app/track_anywhere/security",
        "backend/app/track_anywhere/attachments",
        "cli/track_anywhere_cli",
    )
    protected = [
        row
        for row in rows
        if row["Disposition"] == "retain"
        and _manifest_path(row["Path"]).startswith(protected_prefixes)
    ]
    manifested = {_manifest_path(row["Path"]) for row in rows}
    sensitive_files = {
        path.relative_to(ROOT).as_posix()
        for parent in (
            ROOT / "backend/app/track_anywhere/auth",
            ROOT / "cli/track_anywhere_cli",
        )
        for path in parent.glob("*.py")
    }
    sensitive_files.update(
        {
            "backend/app/track_anywhere/attachments.py",
            "backend/app/track_anywhere/security.py",
        }
    )
    assert sensitive_files <= manifested
    assert protected
    for row in protected:
        assert row["V2 consumer"] not in {"", "—", "TBD"}, row["Path"]
        assert row["Rationale"] not in {"", "—", "TBD"}, row["Path"]
