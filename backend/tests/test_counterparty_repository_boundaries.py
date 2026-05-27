from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_counterparty_use_cases_read_through_focused_repository():
    source = (BACKEND / "service_counterparties.py").read_text()
    repository_source = (BACKEND / "storage_repositories/catalog.py").read_text()
    forbidden = re.compile(
        r"\bself\.storage\.(get_counterparty|get_counterparty_by_slug|get_counterparty_by_name|list_counterparties)\b"
    )

    offenders = [
        f"service_counterparties.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "uow.counterparties.list_counterparties" in source
    assert "uow.counterparties.get_counterparty" in source
    assert "uow.counterparties.get_counterparty_by_slug" in source
    assert "uow.counterparties.get_counterparty_by_name" in source
    assert "def list_counterparties(" in repository_source
    assert "def get_counterparty(" in repository_source
    assert "def get_counterparty_by_slug(" in repository_source
    assert "def get_counterparty_by_name(" in repository_source
