from __future__ import annotations

import re
from pathlib import Path


def test_service_write_paths_do_not_stage_accounts_in_ledger_mirror():
    repo_root = Path.cwd()
    offenders: list[str] = []
    patterns = [
        re.compile(r"\bledger\.create_account\("),
        re.compile(r"\bledger\.dirty_accounts\("),
    ]
    for root in ("backend/app/track_anywhere/api_routers", "backend/app/track_anywhere"):
        for path in (repo_root / root).glob("service*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if any(pattern.search(line) for pattern in patterns):
                    offenders.append(f"{path.relative_to(repo_root)}:{line_number}: {line.strip()}")

    assert offenders == []
