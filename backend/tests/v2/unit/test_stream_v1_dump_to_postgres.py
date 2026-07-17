from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/stream-v1-dump-to-postgres.py"


FAKE_DOCKER = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
Path(os.environ["FAKE_DOCKER_TRACE"]).write_text(
    json.dumps(arguments),
    encoding="utf-8",
)
payload = sys.stdin.buffer.read()
Path(os.environ["FAKE_DOCKER_PAYLOAD"]).write_bytes(payload)
if os.environ.get("FAKE_DOCKER_FAIL") == "1":
    sys.stderr.write(os.environ.get("FAKE_DOCKER_SECRET", "restore detail"))
    raise SystemExit(23)
"""


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_helper(
    tmp_path: Path,
    payload: bytes,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    restore_fails: bool = False,
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    _write_executable(bin_dir / "docker", FAKE_DOCKER)
    trace = tmp_path / "docker-arguments.json"
    restored = tmp_path / "restored.bin"
    working_directory = tmp_path / "working"
    working_directory.mkdir()
    environment = {
        **os.environ,
        "FAKE_DOCKER_FAIL": "1" if restore_fails else "0",
        "FAKE_DOCKER_PAYLOAD": str(restored),
        "FAKE_DOCKER_SECRET": "postgresql://private-user:private-password@db/private",
        "FAKE_DOCKER_TRACE": str(trace),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--container",
            "ta-rehearsal-source-opaque",
            "--database",
            "frozen_source",
            "--username",
            "frozen_restore",
            "--expected-bytes",
            str(len(payload) if expected_bytes is None else expected_bytes),
            "--expected-sha256",
            expected_sha256 or hashlib.sha256(payload).hexdigest(),
        ],
        cwd=working_directory,
        env=environment,
        input=payload,
        check=False,
        capture_output=True,
    )
    return result, trace, restored, working_directory


def test_stream_helper_feeds_pg_restore_while_hashing_without_a_dump_file(
    tmp_path: Path,
) -> None:
    payload = (b"frozen-custom-format-stream\x00" * 8192) + b"terminal"

    result, trace, restored, working_directory = _run_helper(tmp_path, payload)

    assert result.returncode == 0, result.stderr.decode()
    assert restored.read_bytes() == payload
    assert json.loads(trace.read_text(encoding="utf-8")) == [
        "exec",
        "-i",
        "ta-rehearsal-source-opaque",
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--username",
        "frozen_restore",
        "--dbname",
        "frozen_source",
    ]
    assert json.loads(result.stdout) == {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "status": "PASS",
    }
    assert list(working_directory.iterdir()) == []


def test_stream_helper_rejects_a_short_read_even_when_restore_exits_zero(
    tmp_path: Path,
) -> None:
    payload = b"truncated-custom-format-stream"

    result, _, restored, working_directory = _run_helper(
        tmp_path,
        payload,
        expected_bytes=len(payload) + 1,
    )

    assert result.returncode != 0
    assert restored.read_bytes() == payload
    assert result.stdout == b""
    assert result.stderr == b'{"error":"dump_byte_count_mismatch"}\n'
    assert list(working_directory.iterdir()) == []


def test_stream_helper_stops_after_expected_bytes_plus_one(
    tmp_path: Path,
) -> None:
    expected_bytes = 32
    payload = b"x" * (1024 * 1024)

    result, _, restored, working_directory = _run_helper(
        tmp_path,
        payload,
        expected_bytes=expected_bytes,
    )

    assert result.returncode != 0
    if restored.exists():
        assert len(restored.read_bytes()) <= expected_bytes + 1
    assert result.stdout == b""
    assert result.stderr == b'{"error":"dump_byte_count_mismatch"}\n'
    assert list(working_directory.iterdir()) == []


def test_stream_helper_rejects_a_hash_mismatch(tmp_path: Path) -> None:
    payload = b"complete-but-unapproved-custom-format-stream"

    result, _, restored, working_directory = _run_helper(
        tmp_path,
        payload,
        expected_sha256="0" * 64,
    )

    assert result.returncode != 0
    assert restored.read_bytes() == payload
    assert result.stdout == b""
    assert result.stderr == b'{"error":"dump_sha256_mismatch"}\n'
    assert list(working_directory.iterdir()) == []


def test_stream_helper_fails_closed_on_restore_failure_without_leaking_stderr(
    tmp_path: Path,
) -> None:
    payload = b"valid-stream-that-the-fake-restore-rejects"

    result, _, _, working_directory = _run_helper(
        tmp_path,
        payload,
        restore_fails=True,
    )

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == b'{"error":"pg_restore_failed"}\n'
    assert b"private-password" not in result.stderr
    assert list(working_directory.iterdir()) == []


def test_stream_helper_is_strict_and_has_no_regular_dump_spool() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/env python3\n")
    assert os.access(SCRIPT, os.X_OK)
    for forbidden in (
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "SpooledTemporaryFile",
        "tee",
        '".dump"',
        "'.dump'",
        '".backup"',
        "'.backup'",
    ):
        assert forbidden not in source
    assert "stdin.buffer" in source
    assert "hashlib.sha256" in source
    assert re.search(r'"exec",\s*"-i",', source)
    assert '"pg_restore"' in source
