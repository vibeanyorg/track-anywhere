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
