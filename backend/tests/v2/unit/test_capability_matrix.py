from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
MATRIX = ROOT / "docs/operations/v2-capability-matrix.md"

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
