from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "rehearse-v2-backfill.sh"


def _content() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _fake_rehearsal_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    uv = fake_bin / "uv"
    uv.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >> "$FAKE_REHEARSAL_LOG"

if [[ -n "${TRACK_ANYWHERE_SOURCE_COUNTS_OUTPUT:-}" ]]; then
  cat >/dev/null
  printf '%s\n' '{"accounts":121,"postings":284,"transaction_lines":43,"transactions":135}' > "$TRACK_ANYWHERE_SOURCE_COUNTS_OUTPUT"
  exit 0
fi
if [[ -n "${TRACK_ANYWHERE_REPORT_ROOT:-}" ]]; then
  cat >/dev/null
  printf '%s\n' '{"status":"PASS","run_id":"synthetic-local"}' > "$TRACK_ANYWHERE_REPORT_ROOT/summary.json"
  printf 'summary-write\n' >> "$FAKE_REHEARSAL_LOG"
  exit 0
fi

case "$*" in
  *"postgres_factory.py create --purpose backfill_source"*)
    printf '%s\n' 'postgresql+psycopg://migrator:redacted@127.0.0.1:15543/ta_v2_source'
    ;;
  *"postgres_factory.py create --purpose backfill_target_a"*)
    printf '%s\n' 'postgresql+psycopg://runtime:redacted@127.0.0.1:15543/ta_v2_target_a'
    ;;
  *"postgres_factory.py create --purpose backfill_target_b"*)
    printf '%s\n' 'postgresql+psycopg://runtime:redacted@127.0.0.1:15543/ta_v2_target_b'
    ;;
  *"postgres_factory.py role-name --kind owner"*) printf '%s\n' 'owner_role' ;;
  *"postgres_factory.py role-name --kind runtime"*) printf '%s\n' 'runtime_role' ;;
  *"postgres_factory.py role-name --kind migrator"*) printf '%s\n' 'migrator_role' ;;
  *"postgres_factory.py libpq-url"*)
    printf '%s\n' 'postgresql://migrator:redacted@postgres:5432/ta_v2_source'
    ;;
  *"postgres_factory.py read-only-url"*)
    printf '%s\n' 'postgresql+psycopg://migrator:redacted@127.0.0.1:15543/ta_v2_source?options=readonly'
    ;;
  *"postgres_factory.py assert-absent"*)
    if [[ "${FAKE_FAIL_ASSERT_ABSENT:-0}" == 1 ]]; then exit 29; fi
    ;;
  *"backend.tools.backfill_v1.verify_determinism"*)
    output=""
    while (($#)); do
      if [[ "$1" == --output ]]; then output="$2"; break; fi
      shift
    done
    printf '%s\n' '{"status":"PASS","differences":[]}' > "$output"
    ;;
  *"backend.tools.backfill_v1.verify "*)
    output=""
    while (($#)); do
      if [[ "$1" == --output ]]; then output="$2"; break; fi
      shift
    done
    mkdir -p "$(dirname "$output")"
    printf '%s\n' '{"status":"PASS","snapshot_id":"synthetic","manifest_hash":"00","source_counts":{},"receipt_count":0,"quarantine_count":0,"counts":{},"book_terminal_hashes":{},"projection_hashes":{},"issues":[]}' > "$output"
    ;;
  *"backend.tools.backfill_v1 run "*)
    output=""
    while (($#)); do
      if [[ "$1" == --output-dir ]]; then output="$2"; break; fi
      shift
    done
    mkdir "$output"
    ;;
  *"run python -"*) cat >/dev/null ;;
  *) ;;
esac
""",
        encoding="utf-8",
    )
    docker = fake_bin / "docker"
    docker.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "$FAKE_REHEARSAL_LOG"
if [[ "$*" == *"pg17-client psql --version"* ]]; then
  printf '%s\n' 'psql (PostgreSQL) 17.6'
elif [[ "$*" == *"pg17-client pg_restore --version"* ]]; then
  printf '%s\n' 'pg_restore (PostgreSQL) 17.6'
elif [[ "$*" == *"pg17-client pg_dump --version"* ]]; then
  printf '%s\n' 'pg_dump (PostgreSQL) 17.6'
elif [[ "$*" == *"pg17-client pg_restore"* ]]; then
  cat >/dev/null
fi
""",
        encoding="utf-8",
    )
    for binary in (uv, docker):
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return fake_bin, log


def _run_fake_rehearsal(
    tmp_path: Path, *, fail_absence_readback: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake_bin, log = _fake_rehearsal_tools(tmp_path)
    dump = tmp_path / "synthetic.dump"
    manifest = tmp_path / "synthetic.manifest"
    output = tmp_path / "output"
    dump.write_bytes(b"synthetic-only")
    manifest.write_text("synthetic-only\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["FAKE_REHEARSAL_LOG"] = str(log)
    if fail_absence_readback:
        environment["FAKE_FAIL_ASSERT_ABSENT"] = "1"
    result = subprocess.run(
        [
            str(SCRIPT),
            "--dump",
            str(dump),
            "--manifest",
            str(manifest),
            "--output-root",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    return result, output, log


def test_rehearsal_is_one_fail_closed_shell_lifecycle() -> None:
    content = _content()

    assert content.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert content.index("trap cleanup_on_exit EXIT") < content.index(
        'mkdir "$OUTPUT_ROOT"'
    )
    assert content.index("trap cleanup_on_exit EXIT") < content.index(
        " create --purpose backfill_source "
    )
    assert content.index("cleanup_strict\n") < content.index(
        'destination = root / "summary.json"'
    )
    assert content.index("os.replace(temporary, destination)") < content.index(
        "trap - EXIT", content.index("os.replace(temporary, destination)")
    )
    assert "set +e" not in content
    assert 'rm -rf "$OUTPUT_ROOT"' not in content
    assert 'rm -r "$OUTPUT_ROOT"' not in content


def test_rehearsal_uses_pinned_client_and_safe_factory_url_conversion() -> None:
    content = _content()

    assert "for client in psql pg_restore pg_dump; do" in content
    assert '"$PG17_CLIENT" pg_restore' in content
    assert 'libpq-url --url "$SOURCE_URL" --host postgres --port 5432' in content
    assert '--dbname "$SOURCE_LIBPQ_URL"' in content
    assert "postgresql+psycopg://" not in content
    assert "host pg_restore" not in content
    assert "host psql" not in content
    assert "host pg_dump" not in content


def test_rehearsal_runs_both_independent_verifiers_before_strict_cleanup() -> None:
    content = _content()
    run_a = content.index("--batch-size 37")
    run_b = content.index("--batch-size 13")
    verify_a = content.index(
        '--output "$OUTPUT_ROOT/run-a/independent-verification.json"'
    )
    verify_b = content.index(
        '--output "$OUTPUT_ROOT/run-b/independent-verification.json"'
    )
    determinism = content.index("backend.tools.backfill_v1.verify_determinism")
    strict = content.index("cleanup_strict\n", determinism)

    assert run_a < run_b < verify_a < verify_b < determinism < strict
    assert "TZ=UTC LC_ALL=C PYTHONHASHSEED=0" in content
    assert "TZ=Pacific/Auckland LC_ALL=en_US.UTF-8 PYTHONHASHSEED=731" in content
    assert "--workers 1" in content
    assert "--workers 4" in content
    assert "--shuffle-seed 0" in content
    assert "--shuffle-seed 731" in content


def test_success_cleanup_is_strict_and_reads_absence_back() -> None:
    content = _content()
    best_effort = content.split("cleanup_best_effort() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    strict = content.split("cleanup_strict() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert best_effort.count("|| true") == 3
    assert "|| true" not in strict
    assert strict.count("drop --url") == 1
    assert strict.count("assert-absent --url") == 1
    assert 'for database_url in "$SOURCE_URL" "$TARGET_A_URL" "$TARGET_B_URL"' in strict


def test_one_process_owns_the_complete_success_lifecycle(tmp_path: Path) -> None:
    result, output, log = _run_fake_rehearsal(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (
        json.loads((output / "summary.json").read_text(encoding="utf-8"))["status"]
        == "PASS"
    )
    calls = log.read_text(encoding="utf-8")
    milestones = (
        "create --purpose backfill_source",
        "pg17-client pg_restore --dbname",
        "create --purpose backfill_target_a",
        "create --purpose backfill_target_b",
        "pytest",
        "--batch-size 37",
        "--batch-size 13",
        "run-a/independent-verification.json",
        "run-b/independent-verification.json",
        "verify_determinism",
        "postgres_factory.py drop",
        "postgres_factory.py assert-absent",
        "summary-write",
    )
    positions = tuple(calls.index(milestone) for milestone in milestones)
    assert positions == tuple(sorted(positions))
    assert calls.count("postgres_factory.py assert-absent") == 3


def test_failed_absence_readback_cannot_be_reported_as_pass(tmp_path: Path) -> None:
    result, output, log = _run_fake_rehearsal(tmp_path, fail_absence_readback=True)

    assert result.returncode == 29
    assert not (output / "summary.json").exists()
    calls = log.read_text(encoding="utf-8")
    assert "postgres_factory.py assert-absent" in calls
    assert "summary-write" not in calls
    # One strict drop of each database plus the EXIT trap's three independent
    # best-effort drops after the strict absence gate failed.
    assert calls.count("postgres_factory.py drop") == 6


def test_existing_output_is_rejected_without_deleting_or_overwriting_it(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "source.dump"
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "existing-output"
    dump.write_bytes(b"not-consumed")
    manifest.write_text("{}", encoding="utf-8")
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT),
            "--dump",
            str(dump),
            "--manifest",
            str(manifest),
            "--output-root",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "output root already exists" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (output / "summary.json").exists()


def test_failed_manifest_gate_cannot_write_a_pass_summary(tmp_path: Path) -> None:
    dump = tmp_path / "source.dump"
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "new-output"
    dump.write_bytes(b"synthetic-local-dump")
    manifest.write_text(
        json.dumps(
            {
                "dump_sha256": hashlib.sha256(b"different").hexdigest(),
                "format_version": 1,
                "snapshot_id": "synthetic",
                "source_revision": "synthetic-v1",
                "tables": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--dump",
            str(dump),
            "--manifest",
            str(manifest),
            "--output-root",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert output.is_dir()
    assert not (output / "summary.json").exists()
    assert "frozen dump SHA-256" in (result.stdout + result.stderr)


def test_rehearsal_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
