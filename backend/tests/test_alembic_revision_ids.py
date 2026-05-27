from __future__ import annotations

import re
from pathlib import Path


def test_alembic_revision_ids_fit_version_column():
    repo_root = Path(__file__).resolve().parents[2]
    oversized = []
    pattern = re.compile(r'^revision:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)
    for path in (repo_root / "alembic" / "versions").glob("*.py"):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match and len(match.group(1)) > 32:
            oversized.append(f"{path.name}:{match.group(1)}")

    assert oversized == []
