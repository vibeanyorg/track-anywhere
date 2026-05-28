from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_fx_use_cases_read_accounts_through_focused_repository():
    source = (BACKEND / "service_fx.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.get_account\b")
    offenders = [
        f"service_fx.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_account_from_storage" in source
