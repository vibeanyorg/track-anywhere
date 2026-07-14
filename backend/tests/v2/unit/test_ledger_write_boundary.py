from __future__ import annotations

import inspect
from pathlib import Path

from track_anywhere.application import ledger_committer
from track_anywhere.infrastructure.projections import synchronous


def test_only_ledger_committer_coordinates_the_private_event_append() -> None:
    package_root = Path(__file__).resolve().parents[3] / "app" / "track_anywhere"
    assert package_root.is_dir()
    offenders: list[str] = []
    candidate_paths = set(package_root.glob("api*.py"))
    for scope in (
        package_root / "application",
        package_root / "api",
        package_root / "api_routers",
        package_root / "api_ports",
    ):
        if not scope.exists():
            continue
        candidate_paths.update(scope.rglob("*.py"))
    for path in candidate_paths:
        if path == package_root / "application" / "ledger_committer.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "PostgresEventStore" in source or "._append_batch" in source:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == []

    source = inspect.getsource(ledger_committer)
    assert "PostgresEventStore" in source
    assert "._append_batch" in source
    assert "append_and_project" in source


def test_synchronous_projector_has_no_network_or_process_cache_dependency() -> None:
    source = inspect.getsource(synchronous)
    for forbidden in (
        "requests",
        "httpx",
        "urllib",
        "socket",
        "FinanceService",
        "OrmStorage",
        "lru_cache",
    ):
        assert forbidden not in source
