from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_asset_use_cases_read_through_focused_repositories():
    source = (BACKEND / "service_assets.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.(list_accounts|list_all_confirmed_transactions)\b")
    offenders = [
        f"service_assets.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_list_accounts_from_storage" in source
    assert "_list_all_transactions_from_storage" in source
