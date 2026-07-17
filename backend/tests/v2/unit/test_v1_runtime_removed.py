from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]

FORBIDDEN_RUNTIME_SYMBOLS = (
    "FinanceService",
    "OrmStorage",
    "StorageReadCache",
    "legacy_signed",
    "amount_semantics",
    "confirmed_transaction_count",
    "/api/v1",
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


def _runtime_files() -> tuple[Path, ...]:
    roots = (
        ROOT / "backend/app",
        ROOT / "cli",
        ROOT / "frontend/app",
        ROOT / "scripts",
        ROOT / "contract_tests",
        ROOT / ".github/workflows",
    )
    files: list[Path] = []
    for runtime_root in roots:
        files.extend(
            path
            for path in runtime_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in TEXT_SUFFIXES
        )
    files.append(ROOT / "Dockerfile")
    files.extend(ROOT.glob("compose*.yaml"))
    return tuple(sorted(set(files)))


def test_no_v1_runtime_symbol_remains_in_reachable_surfaces() -> None:
    findings: list[str] = []
    for path in _runtime_files():
        source = path.read_text(encoding="utf-8")
        for symbol in FORBIDDEN_RUNTIME_SYMBOLS:
            if symbol in source:
                findings.append(f"{path.relative_to(ROOT)}: {symbol}")
    assert not findings, "forbidden V1 runtime references remain:\n" + "\n".join(
        findings
    )


def test_test_bootstrap_has_no_sqlite_fallback() -> None:
    for path in (ROOT / "conftest.py", ROOT / "backend/tests/conftest.py"):
        source = path.read_text(encoding="utf-8")
        assert "sqlite" not in source.casefold(), path.relative_to(ROOT)


def test_auth_advertises_only_live_v2_authorization_scopes() -> None:
    from track_anywhere.auth.contracts import AGENT_ALLOWED_SCOPES

    assert AGENT_ALLOWED_SCOPES == {
        "book:read",
        "book:write",
        "ledger:read",
        "ledger:write",
    }


def test_offline_runner_is_not_registered_on_online_or_automatic_surfaces() -> None:
    runner_markers = (
        "track_anywhere.offline",
        "offline.import_frozen_financial_history",
    )
    roots = (
        ROOT / "backend/app/track_anywhere/api",
        ROOT / "backend/app/track_anywhere/mcp",
        ROOT / "backend/app/track_anywhere/outbox",
        ROOT / "alembic",
    )
    files = [
        ROOT / "backend/app/track_anywhere/server.py",
        ROOT / "scripts/backup-postgres-s3.sh",
        ROOT / "scripts/deploy-local.sh",
        ROOT / "scripts/deploy-vps.sh",
        ROOT / "scripts/restore-postgres-s3.sh",
        ROOT / "scripts/start-stable-local.sh",
    ]
    for root in roots:
        files.extend(path for path in root.rglob("*") if path.is_file())
    production_compose = ROOT / "compose.prod.yaml"
    files.extend(
        path for path in ROOT.glob("compose*.yaml") if path != production_compose
    )

    findings = [
        str(path.relative_to(ROOT))
        for path in files
        if any(
            marker in path.read_text(encoding="utf-8", errors="ignore")
            for marker in runner_markers
        )
    ]

    assert not findings, (
        "offline import runner was registered automatically:\n" + "\n".join(findings)
    )

    production = production_compose.read_text(encoding="utf-8")
    marker = "\n  frozen-v1-backfill:\n"
    prefix, separator, remainder = production.partition(marker)
    runner, next_service, suffix = remainder.partition("\n  cli:\n")
    assert separator == marker
    assert next_service == "\n  cli:\n"
    assert all(value in runner for value in runner_markers)
    assert 'profiles: ["frozen-v1-backfill"]' in runner
    assert 'restart: "no"' in runner
    assert "ports:" not in runner
    assert "depends_on:" not in runner
    assert "frozen-v1-backfill" not in prefix + suffix
