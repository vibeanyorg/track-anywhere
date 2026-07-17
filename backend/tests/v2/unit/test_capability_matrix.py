from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
MATRIX = ROOT / "docs/operations/v2-capability-matrix.md"
BACKFILL_RUNBOOK = ROOT / "docs/operations/v1-financial-backfill.md"
BACKFILL_EVIDENCE = (
    ROOT / "docs/operations/v1-financial-backfill-verification-template.md"
)

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
                assert target.is_file(), (
                    f"{capability} evidence does not exist: {target}"
                )
        else:
            assert row["Reason"] not in {"", "—", "TBD"}, capability


def test_frozen_history_runbook_has_the_fail_closed_production_gate() -> None:
    runbook = BACKFILL_RUNBOOK.read_text(encoding="utf-8")
    evidence = BACKFILL_EVIDENCE.read_text(encoding="utf-8")

    fixed_inputs = {
        "a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d",
        "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e",
        "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f",
        "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430",
    }
    for value in fixed_inputs:
        assert value in runbook
        assert value in evidence

    required_runbook_contract = {
        "scripts/stream-v1-dump-to-postgres.py",
        "scripts/rehearse-frozen-v1-history.sh",
        "clean committed SHA",
        "immutable image digest",
        "isolated PostgreSQL 17 restore",
        "maintenance mode",
        "stdin only",
        "atomic",
        "independent verification",
        "cold replay",
        "projection catch-up",
        "authorized decrypt",
        "archive seal",
        "CLI/OAuth/MCP smoke",
        "fresh PostgreSQL 17 database",
        "Never repair, rewrite, or delete ledger events",
        "Production authorization",
    }
    folded_runbook = runbook.casefold()
    for contract in required_runbook_contract:
        assert contract.casefold() in folded_runbook
    assert "- [ ] Production authorization" in runbook
    assert "- [ ] Production authorization" in evidence


def test_frozen_history_boundary_is_linked_from_operations_and_clients() -> None:
    capability = MATRIX.read_text(encoding="utf-8")
    clients = (ROOT / "docs/operations/v2-client-capability-matrix.md").read_text(
        encoding="utf-8"
    )
    final_verification = (ROOT / "docs/operations/v2-final-verification.md").read_text(
        encoding="utf-8"
    )
    dokploy = (ROOT / "docs/operations/dokploy-deploy.md").read_text(encoding="utf-8")

    for source in (capability, clients, final_verification, dokploy):
        assert "v1-financial-backfill.md" in source
    assert "one-shot" in capability
    assert "no client command" in clients
    assert "Production authorization" in final_verification
    assert "ClamAV" in dokploy
    assert "port 3000" in dokploy
