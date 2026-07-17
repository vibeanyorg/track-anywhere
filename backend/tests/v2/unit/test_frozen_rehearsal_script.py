from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import UUID


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/rehearse-frozen-v1-history.sh"
RUNBOOK = ROOT / "docs/operations/v1-financial-backfill.md"
PLAN = ROOT / "docs/plans/2026-07-16-track-anywhere-full-v1-financial-backfill.md"
BASELINE_FIXTURE = (
    ROOT / "backend/tests/v2/imports/fixtures/frozen_production_catalog_baseline.json"
)
TARGET_BOOK_ID = "a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d"
BASELINE_FILE_SHA256 = (
    "501e5f1886e88a5d86f52e52a8e8e0c2c7cfdfc80f72f64d219828889bbe3cd2"
)
BASELINE_ACCOUNT_IDENTITY_SHA256 = (
    "7bc8f8b268bb3f9b5374b4a4206039c69fec0ae3b93a19a22cee9a399ab0a6c8"
)
BASELINE_IDENTITY_SHA256 = (
    "3b7556099f961ffdd65869fd2cd41af97aa0360406586734fab0cd71bce2dc02"
)
BASELINE_ASSET_CODES = (
    "CNY",
    "DOGE",
    "ETH",
    "EUR",
    "EUR24",
    "GBP",
    "HKD",
    "MATIC",
    "NOT",
    "ORDI",
    "TON",
    "USD",
    "USD24",
    "USDC",
    "USDT",
    "WLD",
)


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_production_catalog_fixture_is_exact_identity_only_and_non_sensitive() -> None:
    raw = BASELINE_FIXTURE.read_bytes()
    fixture = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == BASELINE_FILE_SHA256
    assert set(fixture) == {
        "account_ids",
        "asset_codes",
        "contract_version",
        "target_book_id",
    }
    assert fixture["contract_version"] == 1
    assert fixture["target_book_id"] == TARGET_BOOK_ID
    UUID(fixture["target_book_id"])

    account_ids = fixture["account_ids"]
    assert type(account_ids) is list
    assert len(account_ids) == 64
    assert account_ids == sorted(account_ids)
    assert len(set(account_ids)) == 64
    assert all(str(UUID(account_id)) == account_id for account_id in account_ids)
    assert (
        hashlib.sha256(("\n".join(account_ids) + "\n").encode()).hexdigest()
        == BASELINE_ACCOUNT_IDENTITY_SHA256
    )

    asset_codes = fixture["asset_codes"]
    assert tuple(asset_codes) == BASELINE_ASSET_CODES
    assert len(set(asset_codes)) == 16

    identity = json.dumps(
        {
            "account_ids": account_ids,
            "asset_codes": asset_codes,
            "target_book_id": fixture["target_book_id"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert hashlib.sha256(identity).hexdigest() == BASELINE_IDENTITY_SHA256

    rendered = raw.decode().casefold()
    for forbidden in (
        "name",
        "balance",
        "amount",
        "purpose",
        "memo",
        "ciphertext",
        "nonce",
        "database_url",
        "dsn",
    ):
        assert forbidden not in rendered


def test_rehearsal_is_an_executable_strict_stdin_only_harness() -> None:
    source = _source()

    assert source.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    for required in (
        "--candidate-image",
        "--source-commit",
        "--run-id",
        "--report-dir",
        "stream-v1-dump-to-postgres.py",
        "seed-frozen-production-catalog.py",
        "backend.tools.frozen_v1_history",
        "track_anywhere.offline.import_frozen_financial_history",
    ):
        assert required in source
    for forbidden in (
        "--dump-file",
        "DUMP_PATH",
        "tee ",
        "mktemp",
        ".dump",
        ".backup",
        "pg_dump --table=accounts",
        "COPY accounts",
    ):
        assert forbidden not in source

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "required" in result.stderr.casefold()
    assert "docker" not in result.stderr.casefold()


def test_rehearsal_resources_are_run_scoped_internal_ephemeral_and_pinned() -> None:
    source = _source()

    image_match = re.search(
        r"readonly POSTGRES_IMAGE='(?P<image>postgres:17[^']*@sha256:[0-9a-f]{64})'",
        source,
    )
    assert image_match is not None
    assert source.count("$POSTGRES_IMAGE") >= 2
    for name in (
        "NETWORK_NAME",
        "SOURCE_CONTAINER",
        "TARGET_A_CONTAINER",
        "TARGET_B_CONTAINER",
        "SOURCE_VOLUME",
        "TARGET_A_VOLUME",
        "TARGET_B_VOLUME",
    ):
        assert re.search(rf'{name}="\$\{{RUN_SCOPE\}}-[^"]+"', source)
    assert "docker network create --internal" in source
    assert source.count("docker volume create") >= 3
    assert source.count("--tmpfs /var/lib/postgresql/data") == 1
    assert source.count("start_postgres ") == 3
    assert '--network "$NETWORK_NAME"' in source
    assert not re.search(r"(?:^|\s)(?:-p|--publish)(?:\s|=)", source)
    assert "127.0.0.1:" not in source
    assert "host.docker.internal" not in source
    assert 'RUN_LABEL="track-anywhere.frozen-rehearsal=$RUN_SCOPE"' in source


def test_run_scope_is_atomically_claimed_before_mutation_and_unowned_cleanup_is_inert(
    tmp_path: Path,
) -> None:
    source = _source()

    assert 'CLAIM_DIR="/dev/shm/${RUN_SCOPE}-claim"' in source
    assert "CLAIM_OWNED=0" in source
    claim_index = source.index('mkdir -m 0700 "$CLAIM_DIR"')
    assert source.index("trap cleanup EXIT") < claim_index
    for mutation in (
        'mkdir -m 0700 "$REPORT_DIR"',
        'mkdir -m 0700 "$KEYRING_DIR"',
        'mkdir -m 0755 "$SNAPSHOT_DIR"',
        "docker run --rm",
        "docker network create",
    ):
        assert claim_index < source.index(mutation)
    assert 'rmdir "$CLAIM_DIR"' in source
    assert '[[ ! -e "$CLAIM_DIR" ]]' in source

    cleanup_function = (
        "cleanup() {"
        + source.split("cleanup() {", maxsplit=1)[1].split(
            "\n}\ntrap cleanup EXIT", maxsplit=1
        )[0]
        + "\n}\n"
    )
    docker_trace = tmp_path / "docker-called"
    collision = tmp_path / "claimed"
    collision.mkdir()
    program = f"""
set -u
CLAIM_OWNED=0
docker() {{ : > {str(docker_trace)!r}; }}
{cleanup_function}
if mkdir -m 0700 {str(collision)!r} 2>/dev/null; then
  CLAIM_OWNED=1
fi
cleanup
test ! -e {str(docker_trace)!r}
"""
    result = subprocess.run(["bash", "-c", program], check=False, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_run_scoped_database_hostnames_stay_below_dns_label_limit() -> None:
    source = _source()

    assert 'RUN_SCOPE="taf-${SOURCE_COMMIT:0:12}-${RUN_ID//-/}"' in source
    suffixes = {
        "NETWORK_NAME": "net",
        "SOURCE_CONTAINER": "src",
        "TARGET_A_CONTAINER": "a",
        "TARGET_B_CONTAINER": "b",
        "SOURCE_VOLUME": "sv",
        "TARGET_A_VOLUME": "av",
        "TARGET_B_VOLUME": "bv",
    }
    maximum_scope = "taf-" + ("a" * 12) + "-" + ("b" * 32)
    for name, suffix in suffixes.items():
        assert f'{name}="${{RUN_SCOPE}}-{suffix}"' in source
        rendered = f"{maximum_scope}-{suffix}"
        assert len(rendered) <= 63
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", rendered)
    assert "resource_name_pattern" in source
    assert '[[ "${#resource_name}" -le 63 ]]' in source


def test_source_access_is_select_only_and_transaction_read_only() -> None:
    source = _source().casefold()

    for required in (
        "create role frozen_source_reader",
        "default_transaction_read_only=on",
        "grant connect on database",
        "grant usage on schema",
        "grant select on all tables",
        "begin read only",
    ):
        assert required in source
    assert "track_anywhere_frozen_source_url" in source
    assert "frozen_source_reader" in source
    assert "--host 127.0.0.1" in source
    assert '"frozen_source_reader|frozen_source_reader|on"' in source


def test_postgres_readiness_uses_tcp_and_reads_back_target_roles_and_owner() -> None:
    source = _source()

    assert "pg_isready -h 127.0.0.1" in source
    assert source.count("assert_target_roles_ready") == 3
    assert "FROM pg_roles" in source
    assert "pg_get_userbyid(datdba)" in source
    assert source.count('--env "PGPASSWORD=$POSTGRES_PASSWORD"') >= 2
    assert 'assert_target_roles_ready "$TARGET_A_CONTAINER"' in source
    assert 'assert_target_roles_ready "$TARGET_B_CONTAINER"' in source


def test_plan_and_catalog_seed_are_direct_pipes_with_exact_scheduler_variants() -> None:
    source = _source()

    for direct_pipe in (
        "run_planner A | seed_catalog A",
        "run_planner B | seed_catalog B",
        "run_planner A | run_importer A",
        "run_planner B | run_importer B",
    ):
        assert direct_pipe in source
    assert "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=0" in source
    assert "TRACK_ANYWHERE_FROZEN_BATCH_SIZE=37" in source
    assert "TRACK_ANYWHERE_FROZEN_WORKERS=1" in source
    assert "TZ=UTC" in source
    assert "LC_ALL=C" in source
    assert "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED=731" in source
    assert "TRACK_ANYWHERE_FROZEN_BATCH_SIZE=13" in source
    assert "TRACK_ANYWHERE_FROZEN_WORKERS=4" in source
    assert "TZ=Pacific/Auckland" in source
    assert "LC_ALL=C.UTF-8" in source
    assert "assert_catalog_identity_exact" in source
    assert "catalog_sha256" in source
    assert "compare_catalog_hashes" in source


def test_rehearsal_key_and_candidate_image_are_ephemeral_and_content_addressed() -> (
    None
):
    source = _source()

    assert 'KEYRING_DIR="/dev/shm/${RUN_SCOPE}-keyring"' in source
    assert 'chmod 0400 "$KEYRING_FILE"' in source
    assert 'chmod 0400 "$REVIEW_FILE"' in source
    assert "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE" in source
    assert re.search(r"@sha256:\[0-9a-f\]\{64\}", source)
    assert "docker image inspect \"$CANDIDATE_IMAGE\" --format '{{.Id}}'" in source
    assert "CANDIDATE_IMAGE_ID" in source
    assert 'rm -f -- "$KEYRING_FILE"' in source


def test_rehearsal_executes_only_allowlisted_source_commit_snapshot_and_candidate_code() -> (
    None
):
    source = _source()

    assert 'SNAPSHOT_DIR="/dev/shm/${RUN_SCOPE}-snapshot"' in source
    assert 'git -C "$ROOT_DIR" archive --format=tar "$SOURCE_COMMIT"' in source
    assert 'tar -xf - -C "$SNAPSHOT_DIR"' in source
    for committed_path in (
        "backend/tools/__init__.py",
        "backend/tools/frozen_v1_history",
        "backend/tests/v2/imports/fixtures/frozen_full_manifest.json",
        "backend/tests/v2/imports/fixtures/frozen_production_catalog_baseline.json",
        "docker/postgres/init/001-v2-roles.sh",
        "scripts/seed-frozen-production-catalog.py",
        "scripts/stream-v1-dump-to-postgres.py",
        "scripts/verify-frozen-history-target.py",
    ):
        assert committed_path in source
    assert (
        source.count('--mount "type=bind,src=$SNAPSHOT_DIR,dst=/workspace,readonly"')
        >= 3
    )
    assert "src=$ROOT_DIR" not in source
    assert "PYTHONPATH=/workspace" not in source
    assert 'python -I -c "$PYTHON_MODULE_BOOTSTRAP"' in source
    assert 'python -I -c "$PYTHON_SCRIPT_BOOTSTRAP"' in source
    assert (
        "src=$SNAPSHOT_DIR/docker/postgres/init/001-v2-roles.sh,"
        "dst=/docker-entrypoint-initdb.d/001-v2-roles.sh,readonly"
    ) in source
    assert 'python3 "$SNAPSHOT_DIR/scripts/stream-v1-dump-to-postgres.py"' in source
    assert "status --porcelain --untracked-files=all" in source
    assert 'show "${SOURCE_COMMIT}:scripts/rehearse-frozen-v1-history.sh"' in source


def test_review_uses_strict_content_hash_and_candidate_root_helper_for_secrets() -> (
    None
):
    source = _source()
    secret_setup = source.split("CANDIDATE_UID=", maxsplit=1)[1].split(
        "docker network create", maxsplit=1
    )[0]

    assert "verify-review-content" in source
    assert 'sha256sum "$REVIEW_FILE"' not in source
    assert "--network none" in source
    assert "--user 0:0" in source
    assert "--entrypoint chown" in source
    assert '"$CANDIDATE_IMAGE_ID"' in source
    assert '"$CANDIDATE_UID:$CANDIDATE_GID"' in source
    assert (
        '--mount "type=bind,src=$KEYRING_FILE,'
        'dst=/run/rehearsal-secrets/protected-content.json"'
    ) in source
    assert (
        '--mount "type=bind,src=$REVIEW_FILE,'
        'dst=/run/rehearsal-secrets/approved-card-review.json"'
    ) in source
    assert "type=bind,src=$KEYRING_DIR" not in source
    assert "stat -c '%u:%g:%a'" in source
    assert '"$CANDIDATE_UID:$CANDIDATE_GID:400"' in source
    assert secret_setup.count("docker run --rm") == 4
    assert secret_setup.count('--label "$RUN_LABEL"') >= 4


def test_replay_verification_determinism_and_safe_report_are_fail_closed() -> None:
    source = _source()

    assert source.count("assert_zero_inserted_counts") >= 2
    assert "compare_two_target_determinism" in source
    for required in (
        "deterministic_ids_sha256",
        "event_order_sha256",
        "event_payloads_sha256",
        "terminal_hash",
        "balance_sha256",
        "projection_sha256",
        "description_plaintext_sha256",
        "archive_sha256",
    ):
        assert required in source
    assert "ciphertext_sha256" not in source
    assert "nonce_sha256" not in source

    allowlist_match = re.search(
        r"readonly REPORT_ALLOWLIST='(?P<keys>[a-z0-9_ ]+)'",
        source,
    )
    assert allowlist_match is not None
    report_keys = set(allowlist_match.group("keys").split())
    assert report_keys == {
        "alembic_version",
        "archive_sha256",
        "balance_sha256",
        "candidate_image_id",
        "catalog_identity_sha256",
        "catalog_sha256",
        "counts",
        "credit_card_review_sha256",
        "description_plaintext_sha256",
        "deterministic_ids_sha256",
        "event_order_sha256",
        "event_payloads_sha256",
        "plan_sha256",
        "postgres_version_num",
        "projection_sha256",
        "quarantine_count",
        "receipt_state",
        "resource_counts",
        "role_names",
        "run_id",
        "source_commit",
        "source_dump_bytes",
        "source_dump_sha256",
        "source_manifest_sha256",
        "status",
        "terminal_hash",
    }
    for forbidden in (
        "dsn",
        "name",
        "balances",
        "purpose",
        "memo",
        "ciphertext",
        "nonce",
        "keyring",
    ):
        assert forbidden not in report_keys
    assert "validate_report_allowlist" in source
    assert 'read_hash "$VERIFY_REPORT" event_order' in source
    assert 'read_hash "$VERIFY_REPORT" event_payloads' in source
    assert 'read_hash "$VERIFY_REPORT" events' not in source
    assert 'read_hash "$VERIFY_REPORT" journal)' not in source


def test_report_collects_and_validates_only_safe_fixed_operational_metadata() -> None:
    source = _source()

    assert "SHOW server_version_num" in source
    assert "SELECT version_num FROM alembic_version" in source
    assert "v2_0013_frozen_import_fence" in source
    assert "EXPECTED_VERIFICATION_COUNTS_JSON" in source
    assert "EXPECTED_RECEIPT_COUNTS_JSON" in source
    assert "EXPECTED_INSERTED_COUNTS_JSON" in source
    assert "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f" in source
    assert "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430" in source
    assert '.receipt_state == "completed"' in source
    assert '\\"quarantine_count\\":$QUARANTINE_COUNT' in source
    for role in (
        "frozen_owner",
        "frozen_migrator",
        "frozen_runtime",
        "frozen_source_reader",
    ):
        assert role in source


def test_receipt_and_verification_use_distinct_exact_count_contracts() -> None:
    source = _source()

    def constant(name: str) -> dict[str, int]:
        matched = re.search(rf"readonly {name}='(?P<value>{{[^']+}})'", source)
        assert matched is not None
        value = json.loads(matched.group("value"))
        assert type(value) is dict
        return value

    assert constant("EXPECTED_RECEIPT_COUNTS_JSON") == {
        "accounts": 121,
        "archives": 1,
        "assets": 20,
        "categories": 37,
        "category_versions": 37,
        "descriptions": 138,
        "events": 176,
        "journal_transactions": 138,
        "postings": 290,
        "quarantine": 0,
        "reporting_assignments": 38,
        "reporting_lines": 38,
        "reversals": 8,
    }
    assert constant("EXPECTED_VERIFICATION_COUNTS_JSON") == {
        "accounts": 121,
        "archives": 1,
        "assets": 20,
        "async_projection_rows": 30,
        "categories": 37,
        "category_versions": 37,
        "credit_card_transactions": 0,
        "descriptions": 138,
        "journal_postings": 290,
        "journal_transactions": 138,
        "ledger_events": 176,
        "quarantine": 0,
        "reporting_lines": 38,
        "reversals": 8,
        "synchronous_projection_applied_events": 176,
    }
    receipt_assertions = source.split(
        "assert_first_receipt_completed() {", maxsplit=1
    )[1].split("compare_catalog_hashes() {", maxsplit=1)[0]
    assert receipt_assertions.count(
        '--argjson counts "$EXPECTED_RECEIPT_COUNTS_JSON"'
    ) == 2
    assert "EXPECTED_VERIFICATION_COUNTS_JSON" not in receipt_assertions


def test_read_hash_is_single_pass_strict_and_substitution_failures_are_not_masked(
    tmp_path: Path,
) -> None:
    source = _source()
    function_body = source.split("read_hash() {", maxsplit=1)[1].split(
        "\n}\nsha256_values()", maxsplit=1
    )[0]
    read_hash_function = "read_hash() {" + function_body + "\n}\n"

    assert function_body.count("jq ") == 1
    assert 'select(.status == "PASS")' in function_body
    assert 'select(type == "string"' in function_body
    assert not re.search(r'readonly [A-Z_]+="\$\(read_hash', source)
    assert re.search(
        r'ACCOUNTS_SHA256="\$\(read_hash "\$VERIFY_REPORT" accounts\)" \|\| '
        r"report_value_failure\nreadonly ACCOUNTS_SHA256",
        source,
    )

    digest = "a" * 64
    cases = {
        "pass": {"status": "PASS", "hashes": {"event_order": digest}},
        "fail": {"status": "FAIL", "hashes": {"event_order": digest}},
        "missing": {"status": "PASS", "hashes": {}},
        "invalid": {"status": "PASS", "hashes": {"event_order": "invalid"}},
    }
    for name, payload in cases.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [
                "bash",
                "-c",
                read_hash_function + '\nread_hash "$1" event_order',
                "bash",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) is (name == "pass")
        assert result.stdout == (f"{digest}\n" if name == "pass" else "")


def test_top_level_command_substitutions_are_checked_before_readonly() -> None:
    source = _source()

    assert re.search(r'^readonly [A-Z_]+="\$\(', source, flags=re.MULTILINE) is None
    for name in (
        "RUNNING_SCRIPT_SHA256",
        "COMMITTED_SCRIPT_SHA256",
        "POSTGRES_PASSWORD",
        "SOURCE_READER_PASSWORD",
        "MIGRATOR_PASSWORD",
        "RUNTIME_PASSWORD",
        "CANDIDATE_REVISION",
        "CANDIDATE_UID",
        "CANDIDATE_GID",
        "MASTER_KEY",
    ):
        assignment_index = source.index(f'{name}="$(')
        readonly_index = source.index(f"\nreadonly {name}", assignment_index)
        assert "||" in source[assignment_index:readonly_index]


def test_cleanup_proves_no_run_scoped_resources_before_pass() -> None:
    source = _source()
    cleanup_block = source.split("cleanup() {", maxsplit=1)[1].split(
        "trap cleanup EXIT", maxsplit=1
    )[0]

    assert "trap cleanup EXIT" in source
    assert "assert_no_run_resources" in source
    assert 'docker ps -aq --filter "label=$RUN_LABEL"' in cleanup_block
    assert 'docker rm -f "$resource"' in cleanup_block
    for resource in (
        'docker ps -aq --filter "label=$RUN_LABEL"',
        'docker network ls -q --filter "label=$RUN_LABEL"',
        'docker volume ls -q --filter "label=$RUN_LABEL"',
    ):
        assert resource in source
    cleanup_index = source.rindex("cleanup")
    proof_index = source.rindex("assert_no_run_resources")
    pass_index = source.rindex('write_report "PASS"')
    assert cleanup_index < proof_index < pass_index
    assert '\\"containers\\":0' in source
    assert '\\"networks\\":0' in source
    assert '\\"volumes\\":0' in source
    assert "query_run_resources" in source
    assert 'if ! result="$(docker ' in source


def test_runbook_and_plan_use_the_exact_rehearsal_cli_contract() -> None:
    for document in (RUNBOOK, PLAN):
        source = document.read_text(encoding="utf-8")
        assert "bash scripts/rehearse-frozen-v1-history.sh" in source
        assert "--candidate-image" in source
        assert "--source-commit" in source
        assert "--run-id" in source
        assert "--report-dir" in source
        assert '< "$FIXED_DUMP"' in source
        assert "TRACK_ANYWHERE_CANDIDATE_IMAGE" not in source
        assert "--book-id" not in source
        assert "--dump-stdin" not in source
        assert "source_manifest_sha256" in source
        assert "credit_card_review_sha256" in source
        candidate_lines = [
            line for line in source.splitlines() if "--candidate-image" in line
        ]
        assert candidate_lines
        assert all("$IMAGE_ID" in line for line in candidate_lines)
        assert all("v1-backfill-$SHA" not in line for line in candidate_lines)


def test_task15_stages_and_removes_approved_review_outside_repo() -> None:
    source = PLAN.read_text(encoding="utf-8")

    for required in (
        'REMOTE_REVIEW_DIR="/dev/shm/',
        'REMOTE_REVIEW="$REMOTE_REVIEW_DIR/',
        "REMOTE_REVIEW_TMP=",
        "cleanup_remote_review",
        "trap cleanup_remote_review EXIT",
        "mkdir -m 0700",
        "umask 077",
        "cat >",
        '< "$APPROVED_REVIEW"',
        "chmod 0400",
        "stat -c '%u:%g:%a'",
        "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_FILE",
        "rmdir",
        "test ! -e",
        "trap - EXIT",
    ):
        assert required in source
    assert "never enters the repository, `output/`, or command logs" in source
