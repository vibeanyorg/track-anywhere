from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_service_metadata_cleanup_is_centralized():
    checked_paths = [
        *(BACKEND / "service_persistence").glob("*.py"),
        BACKEND / "service_credential_issuance.py",
    ]
    allowed = {BACKEND / "service_persistence/metadata.py"}
    forbidden = [
        re.compile(r"\bself\.credentials\.mark_clean\("),
        re.compile(r"\bself\.audit\.mark_persisted\("),
        re.compile(r"\bself\.idempotency\.mark_clean\("),
    ]
    offenders = []
    for path in checked_paths:
        if path in allowed:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_service_persistence_does_not_manage_storage_read_cache():
    offenders = []
    for path in (BACKEND / "service_persistence").glob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if "update_read_cache(" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []
