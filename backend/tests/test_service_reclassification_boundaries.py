from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_reclassification_reads_transactions_through_focused_repository():
    source = (BACKEND / "service_reclassification.py").read_text()
    forbidden = re.compile(r"\bself\.storage\.get_confirmed_transaction\b")
    offenders = [
        f"service_reclassification.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "_get_transaction_from_storage" in source
