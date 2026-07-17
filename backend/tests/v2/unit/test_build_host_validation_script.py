from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/validate-frozen-backfill-build-host.sh"
IID_READ_CONTRACT = r"""
set -Eeuo pipefail
IMAGE_IID_FILE="$1"
IMAGE_IID_SIZE="$(wc -c <"$IMAGE_IID_FILE" | tr -d ' ')"
if [[ "$IMAGE_IID_SIZE" != '71' && "$IMAGE_IID_SIZE" != '72' ]]; then
  exit 1
fi
IMAGE_ID="$(<"$IMAGE_IID_FILE")"
if [[ ! "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  exit 1
fi
printf '%s' "$IMAGE_ID"
"""


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _read_iid(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", IID_READ_CONTRACT, "read-iid", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_build_host_validation_uses_the_existing_exact_git_checkout() -> None:
    source = _source()

    assert 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in source
    assert 'REPO="$ROOT_DIR"' in source
    assert 'git -C "$REPO" rev-parse HEAD' in source
    assert 'git -C "$REPO" status --porcelain)"' in source
    assert "--untracked-files=no" not in source
    assert "gh repo clone" not in source
    assert "scp " not in source


def test_build_host_postgres_is_random_loopback_only_and_label_scoped() -> None:
    source = _source()

    assert 'PG_INIT_MOUNT="$RUN_ROOT/001-v2-roles.sh"' in source
    assert (
        '-v "$PG_INIT_MOUNT:/docker-entrypoint-initdb.d/001-v2-roles.sh:ro"' in source
    )
    assert source.index("phase=isolated_staging") < source.index(
        'chmod 0555 "$PG_INIT_SCRIPT"'
    )
    assert (
        'docker network create --driver bridge --label "$LABEL_KEY=$LABEL_VALUE"'
        in source
    )
    assert "docker network create --internal" not in source
    assert "--publish 127.0.0.1::5432" in source
    assert "docker network inspect \"$PG_NETWORK\" --format '{{.Internal}}'" in source
    assert 'docker ps -aq --filter "label=$LABEL_KEY=$LABEL_VALUE"' in source
    assert "docker system prune" not in source
    assert "docker network prune" not in source
    assert "docker volume prune" not in source


def test_build_host_candidate_and_failure_evidence_are_fail_closed() -> None:
    source = _source()

    assert (
        "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
        in source
    )
    assert '--iidfile "$IMAGE_IID_FILE"' in source
    assert 'IMAGE_IID_SIZE="$(wc -c <"$IMAGE_IID_FILE" | tr -d \' \')"' in source
    assert 'IMAGE_ID="$(<"$IMAGE_IID_FILE")"' in source
    assert 'IFS= read -r IMAGE_ID <"$IMAGE_IID_FILE"' not in source
    assert "org.opencontainers.image.revision=$SOURCE_COMMIT" in source
    assert 'TRACK_ANYWHERE_E2E_API_IMAGE="$IMAGE_ID"' in source
    assert '"migrator": f"{migrator_role}|{migrator_role}"' in source
    assert '"runtime": f"{runtime_role}|{runtime_role}"' in source
    assert 'sed -e "s/$POSTGRES_PASSWORD/[REDACTED]/g"' in source
    assert 'chmod 600 "$PG_STATE_FILE" "$PG_LOG_FILE"' in source
    assert "TASK14_PASS=1" in source


def test_build_host_iid_reader_accepts_buildkit_without_a_trailing_newline(
    tmp_path: Path,
) -> None:
    image_id = f"sha256:{'a' * 64}"
    iid_file = tmp_path / "candidate-image.iid"

    iid_file.write_bytes(image_id.encode("ascii"))
    result = _read_iid(iid_file)
    assert result.returncode == 0
    assert result.stdout == image_id

    iid_file.write_bytes(f"{image_id}\n".encode("ascii"))
    result = _read_iid(iid_file)
    assert result.returncode == 0
    assert result.stdout == image_id


def test_build_host_iid_reader_rejects_empty_or_multiline_content(
    tmp_path: Path,
) -> None:
    image_id = f"sha256:{'a' * 64}"
    iid_file = tmp_path / "candidate-image.iid"

    for content in (b"", f"{image_id}\n\n".encode(), f"{image_id}\nextra".encode()):
        iid_file.write_bytes(content)
        assert _read_iid(iid_file).returncode != 0
