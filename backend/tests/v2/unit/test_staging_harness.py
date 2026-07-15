from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[4]

HARNESS_SOURCE_PATHS = (
    ".dockerignore",
    "compose.e2e.yaml",
    "docker/postgres/init/001-v2-roles.sh",
    "scripts/lib/e2e-harness-common.sh",
    "scripts/staging-v2-smoke.sh",
    "scripts/e2e-docker-postgres.sh",
    "docs/operations/v2-isolated-staging-checklist.md",
    "backend/tests/v2/unit/test_staging_harness.py",
)

API_IMAGE_ID = "sha256:" + "a" * 64
POSTGRES_IMAGE_ID = "sha256:" + "c" * 64


FAKE_DOCKER = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
trace = Path(os.environ["FAKE_DOCKER_TRACE"])
trace.parent.mkdir(parents=True, exist_ok=True)
with trace.open("a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\n")

api_ref = os.environ.get("TRACK_ANYWHERE_E2E_API_IMAGE", "local-api:test")
source_commit = os.environ.get("FAKE_SOURCE_COMMIT", "0" * 40)
api_id = "sha256:" + "a" * 64
postgres_id = "sha256:" + "c" * 64


def finish(code=0, output=""):
    if output:
        print(output)
    raise SystemExit(code)


def mark(name, payload):
    destination = os.environ.get(name)
    if destination:
        with Path(destination).open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload) + "\n")


def compose_parts():
    try:
        index = args.index("compose") + 1
    except ValueError:
        return None, []
    while index < len(args):
        if args[index] in {"-p", "--project-name", "-f", "--file"}:
            index += 2
            continue
        return args[index], args[index + 1 :]
    return None, []


if args[:2] == ["context", "show"]:
    finish(output=os.environ.get("FAKE_DOCKER_CONTEXT", "desktop-linux"))
if args[:2] == ["context", "inspect"]:
    finish(output=os.environ.get("FAKE_DOCKER_ENDPOINT", "unix:///tmp/docker.sock"))
if args and args[0] == "version":
    finish(output="27.0.0")

if args[:2] == ["image", "inspect"]:
    reference = args[2]
    rendered = " ".join(args)
    if reference == api_ref:
        value = api_id
    elif reference == "postgres:17-alpine":
        value = postgres_id
    else:
        finish(1)
    if "RepoDigests" in rendered:
        finish(output="[]")
    if "org.opencontainers.image.revision" in rendered:
        finish(output=source_commit)
    finish(output=value)

if args and args[0] == "inspect":
    container = args[1]
    rendered = " ".join(args)
    if "org.opencontainers.image.revision" in rendered:
        finish(output=source_commit)
    if "postgres" in container:
        finish(output=postgres_id)
    finish(output=api_id)

if args and args[0] == "rm":
    mark("FAKE_DOCKER_MUTATIONS", args)
    finish()

command, rest = compose_parts()
if command is None:
    finish()

if command in {"up", "run"}:
    mark("FAKE_DOCKER_MUTATIONS", args)
    if os.environ.get("FAKE_ENFORCE_PULL", "0") == "1":
        try:
            pull_index = rest.index("--pull")
        except ValueError:
            mark("FAKE_DOCKER_PULL_VIOLATIONS", args)
            finish(86)
        if pull_index + 1 >= len(rest) or rest[pull_index + 1] != "never":
            mark("FAKE_DOCKER_PULL_VIOLATIONS", args)
            finish(86)
    finish()

if command in {"build", "pull", "create", "start", "restart"}:
    mark("FAKE_DOCKER_MUTATIONS", args)
    finish(87)

if command == "config":
    finish(output="\n".join((api_ref, api_ref, "postgres:17-alpine")))

if command == "ps":
    if "-q" not in rest:
        finish()
    service = rest[-1]
    finish(output=f"{service}-container")

if command == "logs":
    finish(output="fake local diagnostics")

if command == "down":
    mark("FAKE_DOCKER_MUTATIONS", args)
    if os.environ.get("FAKE_DOWN_FAIL", "0") == "1":
        finish(55)
    finish()

if command == "exec":
    rendered = " ".join(rest)
    if "--command" in rest:
        statement = rest[rest.index("--command") + 1].casefold()
        if "show server_version_num" in statement:
            finish(output="170000")
        if "select session_user, current_user" in statement:
            role = rest[rest.index("-U") + 1]
            finish(output=f"{role}|{role}")
        if "pg_get_userbyid" in statement:
            finish(output=os.environ.get("TRACK_ANYWHERE_OWNER_ROLE", "track_anywhere_owner"))
        if "select version_num from alembic_version" in statement:
            finish(output="abc123")
        if "update ledger_events" in statement or "disable trigger" in statement:
            print("permission denied", file=sys.stderr)
            finish(1)
        finish()
    if "python -m alembic heads" in rendered:
        finish(output="abc123 (head)")
    if " python - " in f" {rendered} " and rest[-1] != "-":
        finish(output=json.dumps({"processed_events": 3, "projection_lag": 0, "status": "PASS"}))
    if rendered.rstrip().endswith("python -"):
        finish(output=json.dumps({
            "book_terminal_hashes": {},
            "counts": {},
            "issues": [],
            "projection_hashes": {},
            "status": "PASS",
        }))
    finish(output=json.dumps({"api_version": "v2", "status": "ok"}))

finish()
"""


FAKE_CURL = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
trace = Path(os.environ["FAKE_CURL_TRACE"])
trace.parent.mkdir(parents=True, exist_ok=True)
with trace.open("a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\n")


def output_path():
    if "-o" in args:
        return Path(args[args.index("-o") + 1])
    return None


def write_response(body, status):
    destination = output_path()
    if destination is None:
        sys.stdout.write(body)
    else:
        destination.write_text(body, encoding="utf-8")
    if "-w" in args:
        write_format = args[args.index("-w") + 1]
        if "%{content_type}" in write_format:
            content_type = os.environ.get(
                "FAKE_CURL_CONTENT_TYPE",
                "application/json; charset=utf-8",
            )
            sys.stdout.write(f"{status}|{content_type}")
        else:
            sys.stdout.write(status)


url = next((item for item in reversed(args) if item.startswith("http")), "")
mode = os.environ.get("FAKE_CURL_MODE", "valid")
if mode == "fail_all":
    raise SystemExit(22)

if url.endswith("/api/v2/ready"):
    write_response(json.dumps({
        "api_version": "v2",
        "checks": {"database": "ok", "schema": "ok"},
        "status": "ok",
    }), "200")
    raise SystemExit(0)

if url.endswith("/api/v2/health"):
    if mode == "redirect":
        follows = any(item.startswith("-") and "L" in item[1:] for item in args)
        if follows:
            Path(os.environ["FAKE_EXTERNAL_CONTACT"]).write_text("contacted\n", encoding="utf-8")
            write_response(json.dumps({"status": "ok", "api_version": "v2"}), "200")
        else:
            write_response(json.dumps({"location": "https://cloud.invalid/health"}), "302")
        raise SystemExit(0)
    if mode == "html":
        write_response("<html>not the V2 API</html>", "200")
        raise SystemExit(0)
    write_response(json.dumps({"status": "ok", "api_version": "v2"}), "200")
    raise SystemExit(0)

if "/api/v1/health" in url:
    write_response("not found", "404")
    raise SystemExit(0)

raise SystemExit(22)
"""


STUB_E2E = r"""#!/usr/bin/env bash
set -euo pipefail
: "${TRACK_ANYWHERE_E2E_RESULT_FILE:?result path required}"
python3 - "$TRACK_ANYWHERE_E2E_RESULT_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], "x", encoding="utf-8") as output:
    json.dump({
        "book_id": "11111111-1111-4111-8111-111111111111",
        "fresh_connection_balance_visibility": True,
        "transaction_id": "22222222-2222-4222-8222-222222222222",
    }, output)
    output.write("\n")
PY
"""


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_staging_repo(
    tmp_path: Path, *, track_staging_script: bool = True
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    for relative in HARNESS_SOURCE_PATHS:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    _write_executable(repo / "scripts/e2e-docker-postgres.sh", STUB_E2E)

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "staging-test@example.invalid")
    _git(repo, "config", "user.name", "Staging Test")
    tracked = [
        item
        for item in HARNESS_SOURCE_PATHS
        if track_staging_script or item != "scripts/staging-v2-smoke.sh"
    ]
    _git(repo, "add", *tracked)
    _git(repo, "commit", "-q", "-m", "test fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _install_fake_tools(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    bin_dir = tmp_path / "fake-bin"
    docker_trace = tmp_path / "docker-trace.jsonl"
    curl_trace = tmp_path / "curl-trace.jsonl"
    mutations = tmp_path / "docker-mutations.jsonl"
    pull_violations = tmp_path / "pull-violations.jsonl"
    external_contact = tmp_path / "external-contact.txt"
    _write_executable(bin_dir / "docker", FAKE_DOCKER)
    _write_executable(bin_dir / "curl", FAKE_CURL)
    _write_executable(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    return bin_dir, {
        "curl_trace": curl_trace,
        "docker_trace": docker_trace,
        "external_contact": external_contact,
        "mutations": mutations,
        "pull_violations": pull_violations,
    }


def _fake_environment(
    bin_dir: Path, evidence: dict[str, Path], source_commit: str
) -> dict[str, str]:
    environment = {
        **os.environ,
        "FAKE_CURL_TRACE": str(evidence["curl_trace"]),
        "FAKE_DOCKER_MUTATIONS": str(evidence["mutations"]),
        "FAKE_DOCKER_PULL_VIOLATIONS": str(evidence["pull_violations"]),
        "FAKE_DOCKER_TRACE": str(evidence["docker_trace"]),
        "FAKE_EXTERNAL_CONTACT": str(evidence["external_contact"]),
        "FAKE_SOURCE_COMMIT": source_commit,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TRACK_ANYWHERE_DOCKER_CLI_TIMEOUT_SECONDS": "5",
        "TRACK_ANYWHERE_DOCKER_COMPOSE_TIMEOUT_SECONDS": "5",
        "TRACK_ANYWHERE_E2E_API_IMAGE": "local-api:test",
    }
    environment.pop("DOCKER_CONTEXT", None)
    environment.pop("DOCKER_HOST", None)
    return environment


def _run_staging(
    tmp_path: Path,
    *,
    track_staging_script: bool = True,
    hidden_harness_content_mismatch: str | None = None,
    curl_mode: str = "valid",
    curl_content_type: str = "application/json; charset=utf-8",
    docker_endpoint: str = "unix:///tmp/docker.sock",
    down_fails: bool = False,
    enforce_pull: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, Path]]:
    repo, source_commit = _make_staging_repo(
        tmp_path,
        track_staging_script=track_staging_script,
    )
    if hidden_harness_content_mismatch is not None:
        harness_path = repo / hidden_harness_content_mismatch
        _git(
            repo,
            "update-index",
            "--assume-unchanged",
            hidden_harness_content_mismatch,
        )
        harness_path.write_text(
            harness_path.read_text(encoding="utf-8") + "\n# hidden mismatch\n",
            encoding="utf-8",
        )
        assert not _git(repo, "status", "--porcelain", "--untracked-files=no")
    bin_dir, evidence = _install_fake_tools(tmp_path)
    environment = _fake_environment(bin_dir, evidence, source_commit)
    environment.update(
        {
            "FAKE_CURL_CONTENT_TYPE": curl_content_type,
            "FAKE_CURL_MODE": curl_mode,
            "FAKE_DOCKER_ENDPOINT": docker_endpoint,
            "FAKE_DOWN_FAIL": "1" if down_fails else "0",
            "FAKE_ENFORCE_PULL": "1" if enforce_pull else "0",
        }
    )
    run_id = str(uuid4())
    report_dir = repo / "output" / f"v2-staging-{source_commit}-{run_id}"
    result = subprocess.run(
        [
            "bash",
            str(repo / "scripts/staging-v2-smoke.sh"),
            "--source-commit",
            source_commit,
            "--run-id",
            run_id,
            "--report-dir",
            str(report_dir),
        ],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, report_dir, evidence


def _json_lines(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _compose_command(arguments: list[str]) -> str | None:
    if "compose" not in arguments:
        return None
    index = arguments.index("compose") + 1
    while index < len(arguments):
        if arguments[index] in {"-p", "--project-name", "-f", "--file"}:
            index += 2
            continue
        return arguments[index]
    return None


def test_compose_uses_one_application_image_with_separate_migration_and_runtime_roles() -> None:
    compose = _read("compose.e2e.yaml")

    assert "TRACK_ANYWHERE_E2E_API_IMAGE" in compose
    assert "migrate:" in compose
    assert "TRACK_ANYWHERE_MIGRATOR_ROLE" in compose
    assert "TRACK_ANYWHERE_DB_RUNTIME_ROLE" in compose
    assert "TRACK_ANYWHERE_RUNTIME_ROLE" in compose
    assert "service_healthy" in compose
    assert "web:" not in compose
    assert "TRACK_ANYWHERE_E2E_WEB_IMAGE" not in compose
    assert "web-runtime" not in compose
    assert compose.count("127.0.0.1") >= 2
    assert "0.0.0.0:" not in compose


def test_api_image_installs_python_dependencies_from_the_frozen_lock() -> None:
    dockerfile = _read("Dockerfile")

    assert "uv sync --frozen --no-dev --extra postgres" in dockerfile
    assert "--active --no-editable --no-cache" in dockerfile
    assert (
        'uv pip install --python /opt/venv/bin/python --no-cache ".[postgres]"'
        not in dockerfile
    )


def test_existing_stack_e2e_is_non_mutating_to_infrastructure() -> None:
    harness = _read("scripts/e2e-docker-postgres.sh")

    assert "TRACK_ANYWHERE_E2E_NO_BUILD" in harness
    assert "TRACK_ANYWHERE_E2E_EXISTING_STACK" in harness
    assert "NO_BUILD requires TRACK_ANYWHERE_E2E_EXISTING_STACK=1" in harness
    assert "--no-build" in harness
    assert "existing stack mode: refusing infrastructure mutation" in harness
    assert "existing stack API image mismatch" in harness
    assert "existing stack PostgreSQL image mismatch" in harness
    assert "TRACK_ANYWHERE_E2E_WEB_IMAGE" not in harness
    assert "EXISTING_WEB_CONTAINER" not in harness
    assert "TRACK_ANYWHERE_E2E_RESULT_FILE" in harness
    assert "fresh_connection_balance_visibility" in harness
    assert "static_web_smoke=PASS" in harness
    assert "embedded_projection_convergence=PASS" in harness
    assert "backup_restore_roundtrip=PASS" in harness
    assert "AsyncProjectionWorker" not in harness
    assert "embedded async projection runtime did not converge" in harness
    assert "public, max-age=31536000, immutable" in harness
    assert "account:read" not in harness
    assert "account:write" not in harness


def test_no_build_mode_refuses_to_prepare_a_stack_before_docker() -> None:
    environment = {**os.environ, "TRACK_ANYWHERE_E2E_NO_BUILD": "1"}
    environment.pop("TRACK_ANYWHERE_E2E_EXISTING_STACK", None)

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/e2e-docker-postgres.sh")],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "NO_BUILD requires TRACK_ANYWHERE_E2E_EXISTING_STACK=1" in result.stderr
    assert "docker" not in result.stderr.casefold()


def test_staging_harness_is_fail_closed_and_never_accepts_itself() -> None:
    harness = _read("scripts/staging-v2-smoke.sh")

    for required in (
        "--source-commit",
        "--run-id",
        "--report-dir",
        "TRACK_ANYWHERE_E2E_API_IMAGE",
        "org.opencontainers.image.revision",
        "docker image inspect",
        "docker inspect",
        "TRACK_ANYWHERE_E2E_NO_BUILD=1",
        "TRACK_ANYWHERE_E2E_EXISTING_STACK=1",
        "status=PASS",
        'write_verification "PASS"',
        'write_verification "FAIL"',
        "source_commit",
        "run_id",
        "server_version_num",
        "alembic_version",
        "runtime_cannot_update_events",
        "runtime_cannot_disable_triggers",
        "projection_lag",
        "verify_v2_ledger",
        "LEGACY_API_PATH",
        "down -v --remove-orphans",
    ):
        assert required in harness

    # Acceptance is intentionally an outer, independent operation.
    assert "accepted-run" not in harness
    assert "accepted_pointer" not in harness
    assert "account:read" not in harness
    assert "account:write" not in harness


def test_staging_harness_rejects_missing_arguments_before_docker() -> None:
    assert os.access(ROOT / "scripts/staging-v2-smoke.sh", os.X_OK)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/staging-v2-smoke.sh")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--source-commit, --run-id, and --report-dir are required" in result.stderr
    assert "docker" not in result.stderr.casefold()


def test_run_contract_requires_unique_report_and_external_acceptance() -> None:
    checklist = _read("docs/operations/v2-isolated-staging-checklist.md")
    final = _read("docs/operations/v2-final-verification.md")
    normalized_final = " ".join(final.casefold().split())

    for required in (
        "caller-supplied UUID",
        "nonexistent report directory",
        "clean migration",
        "PostgreSQL 17",
        "distinct migrator and runtime",
        "fresh connection",
        "hash chain",
        "Alembic head",
        "async projection lag",
        "independent replay",
        "exact running-container image IDs",
        "revision labels",
        "no `/api/v1` route",
        "atomically",
        "independently validates",
        "failed run directory",
        "no production deploy",
    ):
        assert required.casefold() in checklist.casefold()

    assert "NOT RUN" in final
    assert "production untouched" in normalized_final
    assert "current head contains no v1 import path" in normalized_final
    assert "has not been executed" in normalized_final


def test_docker_context_excludes_data_artifacts() -> None:
    dockerignore = _read(".dockerignore")

    assert "output" in dockerignore
    assert "*.dump" in dockerignore
    assert "*.backup" in dockerignore
    assert "backups" in dockerignore


def test_e2e_and_staging_share_bootstrap_mechanics() -> None:
    common_path = ROOT / "scripts/lib/e2e-harness-common.sh"

    assert common_path.is_file(), "the shared E2E harness library must exist"
    common = common_path.read_text(encoding="utf-8")
    harnesses = (
        _read("scripts/e2e-docker-postgres.sh"),
        _read("scripts/staging-v2-smoke.sh"),
    )

    for harness in harnesses:
        assert 'source "$ROOT_DIR/scripts/lib/e2e-harness-common.sh"' in harness
        for helper in (
            "ta_pick_loopback_port",
            "ta_require_postgres_identifier",
            "ta_run_with_timeout",
            "ta_initialize_database_owner",
        ):
            assert helper in harness
            assert f"{helper}()" not in harness
            assert f"{helper} ()" not in harness

    for helper in (
        "ta_pick_loopback_port",
        "ta_require_postgres_identifier",
        "ta_run_with_timeout",
        "ta_initialize_database_owner",
    ):
        assert f"{helper}()" in common


def test_common_harness_rejects_unsafe_postgres_identifiers() -> None:
    common_path = ROOT / "scripts/lib/e2e-harness-common.sh"
    command = 'source "$1"; ta_require_postgres_identifier "$2" "owner role"'

    accepted = subprocess.run(
        ["bash", "-c", command, "bash", str(common_path), "safe_role_42"],
        check=False,
        capture_output=True,
        text=True,
    )
    rejected = subprocess.run(
        ["bash", "-c", command, "bash", str(common_path), 'owner";drop'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode == 2
    assert "owner role must be a safe PostgreSQL identifier" in rejected.stderr


def test_common_harness_timeout_and_database_owner_initialization(
    tmp_path: Path,
) -> None:
    common_path = ROOT / "scripts/lib/e2e-harness-common.sh"
    trace = tmp_path / "owner-command.txt"
    fake_compose = tmp_path / "fake-compose"
    _write_executable(
        fake_compose,
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >"$HARNESS_TRACE"\n',
    )
    environment = {**os.environ, "HARNESS_TRACE": str(trace)}

    timeout_result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ta_run_with_timeout 0.01 python3 -c '
            "'import time; time.sleep(0.2)'",
            "bash",
            str(common_path),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    owner_result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ta_initialize_database_owner 5 safe_owner "$2"',
            "bash",
            str(common_path),
            str(fake_compose),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert timeout_result.returncode == 124
    assert "command timed out after" in timeout_result.stderr
    assert owner_result.returncode == 0, owner_result.stderr
    assert trace.read_text(encoding="utf-8").strip() == (
        "exec -T postgres psql --username track_anywhere --dbname postgres "
        "--set ON_ERROR_STOP=1 --command "
        'ALTER DATABASE track_anywhere OWNER TO "safe_owner"'
    )


def test_common_harness_timeout_preserves_command_standard_input() -> None:
    common_path = ROOT / "scripts/lib/e2e-harness-common.sh"

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ta_run_with_timeout 2 python3 -',
            "bash",
            str(common_path),
        ],
        input='print("stdin-preserved")\n',
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stdin-preserved"


def test_staging_rejects_an_untracked_harness_before_docker(tmp_path: Path) -> None:
    result, _, evidence = _run_staging(tmp_path, track_staging_script=False)

    assert result.returncode == 2
    assert "source commit must contain tracked harness file" in result.stderr
    assert not evidence["docker_trace"].exists()


def test_staging_rejects_harness_content_that_differs_from_source_commit(
    tmp_path: Path,
) -> None:
    result, _, evidence = _run_staging(
        tmp_path,
        hidden_harness_content_mismatch="compose.e2e.yaml",
    )

    assert result.returncode == 2
    assert "harness file differs from source commit: compose.e2e.yaml" in result.stderr
    assert not evidence["docker_trace"].exists()


def test_staging_rejects_changed_postgres_role_bootstrap_before_docker(
    tmp_path: Path,
) -> None:
    role_bootstrap = "docker/postgres/init/001-v2-roles.sh"
    result, _, evidence = _run_staging(
        tmp_path,
        hidden_harness_content_mismatch=role_bootstrap,
    )

    assert result.returncode == 2
    assert f"harness file differs from source commit: {role_bootstrap}" in result.stderr
    assert not evidence["docker_trace"].exists()


def test_staging_rejects_a_remote_docker_endpoint_before_mutation(
    tmp_path: Path,
) -> None:
    result, report_dir, evidence = _run_staging(
        tmp_path,
        docker_endpoint="ssh://builder.example.invalid",
    )

    assert result.returncode != 0
    assert "staging requires a local Docker endpoint" in result.stderr
    assert not evidence["mutations"].exists()
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"


def test_staging_preflights_postgres_and_disables_all_image_pulls(
    tmp_path: Path,
) -> None:
    result, report_dir, evidence = _run_staging(tmp_path, enforce_pull=True)

    assert result.returncode == 0, result.stderr
    assert not evidence["pull_violations"].exists()
    commands = _json_lines(evidence["docker_trace"])
    postgres_preflight = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ["image", "inspect", "postgres:17-alpine"]
    )
    resource_commands = [
        (index, command)
        for index, command in enumerate(commands)
        if _compose_command(command) in {"up", "run"}
    ]
    assert resource_commands
    assert postgres_preflight < resource_commands[0][0]
    for _, command in resource_commands:
        pull_index = command.index("--pull")
        assert command[pull_index + 1] == "never"
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["images"]["api"]["content_digest"] == API_IMAGE_ID
    assert "web" not in report["images"]
    assert report["checks"]["public_app_health"] == {
        "api_version": "v2",
        "status": "ok",
    }
    assert "web_proxy_health" not in report["checks"]


def test_public_app_health_redirect_is_not_followed_and_fails_closed(tmp_path: Path) -> None:
    result, report_dir, evidence = _run_staging(tmp_path, curl_mode="redirect")

    assert result.returncode != 0
    assert not evidence["external_contact"].exists()
    app_commands = [
        command
        for command in _json_lines(evidence["curl_trace"])
        if any(item.endswith("/api/v2/health") for item in command)
    ]
    assert app_commands
    assert all(
        not any(item.startswith("-") and "L" in item[1:] for item in command)
        for command in app_commands
    )
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["stage"] == "app_smoke"


def test_public_app_health_html_cannot_be_reported_as_pass(tmp_path: Path) -> None:
    result, report_dir, _ = _run_staging(tmp_path, curl_mode="html")

    assert result.returncode != 0
    assert "public app health payload must be exact V2 JSON" in result.stderr
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["stage"] == "app_smoke"


def test_public_app_health_json_with_wrong_mime_type_cannot_pass(tmp_path: Path) -> None:
    result, report_dir, _ = _run_staging(
        tmp_path,
        curl_content_type="text/plain; charset=utf-8",
    )

    assert result.returncode != 0
    assert "public app health content type must be application/json" in result.stderr
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["stage"] == "app_smoke"


def test_teardown_failure_overwrites_pass_and_returns_nonzero(tmp_path: Path) -> None:
    result, report_dir, _ = _run_staging(tmp_path, down_fails=True)

    assert result.returncode != 0
    assert "isolated staging teardown failed" in result.stderr
    report = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["stage"] == "teardown"


def test_existing_stack_executes_smoke_without_infrastructure_mutation_or_pull(
    tmp_path: Path,
) -> None:
    bin_dir, evidence = _install_fake_tools(tmp_path)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    environment = _fake_environment(bin_dir, evidence, source_commit)
    environment.update(
        {
            "FAKE_CURL_MODE": "valid",
            "FAKE_ENFORCE_PULL": "1",
            "TRACK_ANYWHERE_E2E_API_PORT": "18080",
            "TRACK_ANYWHERE_E2E_EXISTING_STACK": "1",
            "TRACK_ANYWHERE_E2E_NO_BUILD": "1",
            "TRACK_ANYWHERE_E2E_POSTGRES_PORT": "15543",
            "TRACK_ANYWHERE_E2E_PROJECT": "existing-stack-audit",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/e2e-docker-postgres.sh")],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert (
        result.returncode != 0
    )  # The fake HTTP boundary intentionally stops the smoke.
    commands = _json_lines(evidence["docker_trace"])
    assert any(_compose_command(command) == "ps" for command in commands)
    forbidden = {"build", "create", "down", "pull", "restart", "run", "start", "up"}
    assert all(_compose_command(command) not in forbidden for command in commands)
    assert not evidence["mutations"].exists()
    assert not evidence["pull_violations"].exists()


def test_final_verification_describes_only_the_current_v2_release_boundary() -> None:
    final = " ".join(
        _read("docs/operations/v2-final-verification.md").casefold().split()
    )

    assert "current head contains no v1 import path" in final
    assert "exact-image isolated staging: **not run**" in final
    assert "production deploy/cutover: **not performed**" in final
    assert "backfill" not in final
    assert "frozen dump" not in final


def test_checklist_scopes_no_registry_to_the_harness_invocation() -> None:
    checklist = " ".join(
        _read("docs/operations/v2-isolated-staging-checklist.md").casefold().split()
    )

    assert "from the staging harness invocation onward" in checklist
    assert "build preparation may pull base images" in checklist
    assert "no-registry guarantee" in checklist
