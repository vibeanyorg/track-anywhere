from __future__ import annotations

from pathlib import Path


MAX_PYTHON_FILE_LINES = 1000
CHECKED_ROOTS = ("backend/app", "backend/tests", "cli", "alembic")


def test_python_files_stay_under_complexity_line_limit():
    repo_root = Path(__file__).resolve().parents[2]
    oversized = []
    for root_name in CHECKED_ROOTS:
        for path in (repo_root / root_name).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            line_count = sum(1 for _ in path.open(encoding="utf-8"))
            if line_count > MAX_PYTHON_FILE_LINES:
                oversized.append(f"{path.relative_to(repo_root)}:{line_count}")

    assert oversized == []
