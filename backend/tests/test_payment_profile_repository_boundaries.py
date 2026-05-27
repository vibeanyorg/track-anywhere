from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_payment_profile_use_cases_read_through_focused_repository():
    source = (BACKEND / "service_payment_profile_lifecycle.py").read_text()
    repository_source = (BACKEND / "storage_repositories/payments.py").read_text()
    forbidden = re.compile(
        r"\bself\.storage\.(get_payment_profile|get_payment_profile_by_slug|list_payment_profiles)\b"
    )

    offenders = [
        f"service_payment_profile_lifecycle.py:{line_number}: {line.strip()}"
        for line_number, line in enumerate(source.splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == []
    assert "uow.payment_profiles.list_profiles" in source
    assert "uow.payment_profiles.get_profile" in source
    assert "uow.payment_profiles.get_profile_by_slug" in source
    assert "def list_profiles(" in repository_source
    assert "def get_profile(" in repository_source
    assert "def get_profile_by_slug(" in repository_source
