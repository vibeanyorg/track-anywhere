from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/validate-frozen-backfill-build-host.sh"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


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
    assert "org.opencontainers.image.revision=$SOURCE_COMMIT" in source
    assert 'TRACK_ANYWHERE_E2E_API_IMAGE="$IMAGE_ID"' in source
    assert 'sed -e "s/$POSTGRES_PASSWORD/[REDACTED]/g"' in source
    assert 'chmod 600 "$PG_STATE_FILE" "$PG_LOG_FILE"' in source
    assert "TASK14_PASS=1" in source
