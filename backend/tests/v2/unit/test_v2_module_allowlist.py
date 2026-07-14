from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
RUNTIME_ROOT = ROOT / "backend/app/track_anywhere"

APPROVED_ROOT_ENTRIES = {
    "__init__.py",
    "api/",
    "application/",
    "auth/",
    "domain/",
    "infrastructure/",
    "observability/",
    "outbox/",
    "queries/",
    "serialization/",
}


def test_v2_runtime_root_contains_only_approved_packages() -> None:
    actual = {
        f"{path.name}/" if path.is_dir() else path.name
        for path in RUNTIME_ROOT.iterdir()
        if path.name != "__pycache__"
        and (
            path.is_file()
            or any(
                candidate.is_file() and "__pycache__" not in candidate.parts
                for candidate in path.rglob("*.py")
            )
        )
    }
    assert actual == APPROVED_ROOT_ENTRIES


def test_approved_runtime_packages_do_not_import_root_legacy_modules() -> None:
    approved_packages = tuple(
        RUNTIME_ROOT / entry.removesuffix("/")
        for entry in sorted(APPROVED_ROOT_ENTRIES)
        if entry.endswith("/")
    )
    legacy_import_prefixes = (
        "from track_anywhere.service",
        "from track_anywhere.storage",
        "from track_anywhere.api_runtime",
        "from track_anywhere.api_routes",
        "from track_anywhere.posting_semantics",
        "from track_anywhere.ledger",
        "import track_anywhere.service",
        "import track_anywhere.storage",
    )
    findings: list[str] = []
    for package in approved_packages:
        for path in package.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for prefix in legacy_import_prefixes:
                if prefix in source:
                    findings.append(f"{path.relative_to(ROOT)}: {prefix}")
    assert not findings, "legacy imports remain:\n" + "\n".join(findings)
