from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from uuid import UUID

import pytest

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tools.frozen_v1_history.reference_reducer import (
    SourceLedgerFacts,
    reduce_canonical_plan,
)
from backend.tools.frozen_v1_history.verify import FrozenHistoryVerificationReport
from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    plan_sha256,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/verify-frozen-history-target.py"
TARGET_BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")


class _BinaryTextSink:
    def __init__(self, initial: bytes = b"") -> None:
        self.buffer = io.BytesIO(initial)

    def write(self, value: str) -> int:
        return self.buffer.write(value.encode("utf-8"))

    def flush(self) -> None:
        return None


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_frozen_target", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _reference():
    from backend.tools.frozen_v1_history.reference_artifact import (
        EXPECTED_PLAN_SHA256,
        EXPECTED_TERMINAL_HASH,
    )

    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = json.loads(canonical_plan_bytes(plan))
    assert type(raw) is dict
    return replace(
        reduce_canonical_plan(raw),
        plan_hash=EXPECTED_PLAN_SHA256,
        terminal_hash=EXPECTED_TERMINAL_HASH,
    )


def _pass_report(reference) -> FrozenHistoryVerificationReport:
    hashes = {
        **reference.hashes,
        "archive_metadata": reference.archive_metadata_hash,
        "archive_plaintext": reference.archive_plaintext_sha256,
        "archive_seal": "a" * 64,
        "description_aggregate": reference.description_aggregate_sha256,
        "terminal": reference.terminal_hash,
    }
    return FrozenHistoryVerificationReport(
        status="PASS",
        issues=(),
        counts=reference.counts,
        hashes=hashes,
    )


def test_reference_artifact_roundtrip_is_versioned_fixed_and_secret_free() -> None:
    from backend.tools.frozen_v1_history.reference_artifact import (
        parse_reference_artifact,
        serialize_reference_artifact,
    )

    reference = _reference()
    encoded = serialize_reference_artifact(reference)
    decoded = parse_reference_artifact(encoded)

    assert decoded == reference
    parsed = json.loads(encoded)
    assert parsed["contract_version"] == 1
    assert set(parsed) == {
        "archive_id",
        "archive_metadata_hash",
        "archive_plaintext_sha256",
        "book_id",
        "contract_version",
        "counts",
        "description_aggregate_sha256",
        "description_ids",
        "hashes",
        "plan_hash",
        "terminal_hash",
        "terminal_position",
    }
    lowered = encoded.lower()
    for forbidden in (
        b"current_name",
        b"purpose",
        b"memo",
        b"canonical_plaintext",
        b"database_url",
        b"postgresql://",
        b"ciphertext",
        b"nonce",
        b"keyring",
    ):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    "mutation",
    (
        "unexpected_root",
        "unexpected_count",
        "unexpected_hash",
        "invalid_digest",
        "alternate_plan_hash",
        "alternate_terminal_hash",
    ),
)
def test_reference_artifact_rejects_malformed_or_unallowlisted_input(
    mutation: str,
) -> None:
    from backend.tools.frozen_v1_history.reference_artifact import (
        ReferenceArtifactError,
        serialize_reference_artifact,
        parse_reference_artifact,
    )

    parsed = json.loads(serialize_reference_artifact(_reference()))
    if mutation == "unexpected_root":
        parsed["private"] = "sentinel"
    elif mutation == "unexpected_count":
        parsed["counts"]["private"] = 1
    elif mutation == "unexpected_hash":
        parsed["hashes"]["private"] = "0" * 64
    elif mutation == "invalid_digest":
        parsed["terminal_hash"] = "sentinel-private-value"
    elif mutation == "alternate_plan_hash":
        parsed["plan_hash"] = "0" * 64
    else:
        parsed["terminal_hash"] = "1" * 64

    with pytest.raises(ReferenceArtifactError, match="^reference_artifact_invalid$"):
        parse_reference_artifact(
            json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode()
        )


def test_reference_reducer_reports_true_event_order_and_payload_hashes() -> None:
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = json.loads(canonical_plan_bytes(plan))
    assert type(raw) is dict
    events = raw["events"]
    assert type(events) is list
    reference = reduce_canonical_plan(raw)

    order_rows = [
        {
            "book_position": event["book_position"],
            "event_id": event["event_id"],
        }
        for event in events
    ]
    payload_rows = [
        {
            "event_id": event["event_id"],
            "event_schema_version": event["event_schema_version"],
            "event_type": event["event_type"],
            "payload": event["payload"],
        }
        for event in events
    ]

    assert (
        reference.hashes["event_order"]
        == hashlib.sha256(canonical_json_bytes(order_rows)).hexdigest()
    )
    assert (
        reference.hashes["event_payloads"]
        == hashlib.sha256(canonical_json_bytes(payload_rows)).hexdigest()
    )
    assert reference.hashes["event_order"] != reference.hashes["event_payloads"]


def test_target_verifier_reads_to_eof_and_rejects_oversize_before_runtime() -> None:
    module = _load_script()

    class OversizedInput:
        def read(self, size: int) -> bytes:
            return b"x" * size

    with pytest.raises(module.TargetVerificationFailure) as caught:
        module._execute(
            ["--stdin"],
            stdin=OversizedInput(),
            environ={},
            verify_operation=lambda *_args: pytest.fail("runtime must not open"),
        )
    assert caught.value.code == "stdin_too_large"


def test_target_verifier_parses_reference_before_reading_dsn_or_keyring() -> None:
    from backend.tools.frozen_v1_history.reference_artifact import (
        serialize_reference_artifact,
    )

    module = _load_script()
    malformed = bytearray(serialize_reference_artifact(_reference()))
    malformed[-1:] = b"x"

    class UnreadableEnvironment(dict[str, str]):
        def get(self, _key, _default=None):
            raise AssertionError("runtime configuration must remain untouched")

    with pytest.raises(module.TargetVerificationFailure) as caught:
        module._execute(
            ["--stdin"],
            stdin=io.BytesIO(bytes(malformed)),
            environ=UnreadableEnvironment(),
            verify_operation=lambda *_args: pytest.fail("runtime must not open"),
        )
    assert caught.value.code == "reference_artifact_invalid"


def test_target_verifier_outputs_only_report_dict_after_safe_runtime_values() -> None:
    from backend.tools.frozen_v1_history.reference_artifact import (
        serialize_reference_artifact,
    )

    module = _load_script()
    reference = _reference()
    calls = []

    def verify_operation(database_url: str, keyring_file: str, parsed_reference):
        assert database_url == "postgresql+psycopg://opaque"
        assert keyring_file == "/dev/shm/opaque-keyring"
        assert parsed_reference == reference
        calls.append("verified")
        return _pass_report(parsed_reference)

    result = module._execute(
        ["--stdin"],
        stdin=io.BytesIO(serialize_reference_artifact(reference)),
        environ={
            "TRACK_ANYWHERE_DATABASE_URL": "postgresql+psycopg://opaque",
            "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE": "/dev/shm/opaque-keyring",
        },
        verify_operation=verify_operation,
    )

    assert calls == ["verified"]
    assert result == _pass_report(reference).to_dict()
    assert set(result) == {"counts", "hashes", "issues", "status"}


def test_target_verifier_script_is_executable_strict_and_has_no_spool() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/env python3\n")
    assert os.access(SCRIPT, os.X_OK)
    assert "AsyncProjectionWorker" in source
    assert "parse_reference_artifact" in source
    assert "report.to_dict()" in source
    for forbidden in (
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "SpooledTemporaryFile",
        '".plan"',
        "'.plan'",
    ):
        assert forbidden not in source


def test_reference_mode_reduces_in_memory_and_never_serializes_private_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli
    from backend.tools.frozen_v1_history import reference_artifact
    from backend.tools.frozen_v1_history.reference_artifact import (
        parse_reference_artifact,
    )

    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)
    plan_reference = reduce_canonical_plan(json.loads(raw))
    source_reference = SourceLedgerFacts(
        book_id=plan_reference.book_id,
        terminal_position=plan_reference.terminal_position,
        terminal_hash=plan_reference.terminal_hash,
        counts=plan_reference.counts,
        hashes=plan_reference.hashes,
        description_ids=plan_reference.description_ids,
        description_aggregate_sha256=plan_reference.description_aggregate_sha256,
    )
    source_sentinel = object()
    review_sentinel = object()
    monkeypatch.setattr(cli, "EXPECTED_PLAN_SHA256", plan_sha256(plan))
    monkeypatch.setattr(reference_artifact, "EXPECTED_PLAN_SHA256", plan_sha256(plan))
    monkeypatch.setattr(
        reference_artifact,
        "EXPECTED_TERMINAL_HASH",
        plan.expected_terminal_hash,
    )
    monkeypatch.setattr(cli, "read_full_manifest", lambda _path: object())
    monkeypatch.setattr(
        cli,
        "extract_fixed_source",
        lambda *_args, **_kwargs: source_sentinel,
    )
    monkeypatch.setattr(
        cli,
        "read_approved_credit_card_review",
        lambda *_args, **_kwargs: review_sentinel,
    )

    def reduce_source(*, source, review, target_book_id):
        assert source is source_sentinel
        assert review is review_sentinel
        assert target_book_id == TARGET_BOOK_ID
        return source_reference

    monkeypatch.setattr(cli, "reduce_approved_source_reference", reduce_source)
    environment = {
        "TRACK_ANYWHERE_FROZEN_SOURCE_URL": "postgresql://sentinel-private-dsn",
        "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH": "/fixture/manifest",
        "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH": "/fixture/review",
    }

    artifact = cli._compile_reference_from_environment(raw, environment)

    assert parse_reference_artifact(artifact).book_id == str(TARGET_BOOK_ID)
    assert b"sentinel-private-dsn" not in artifact


def test_review_content_cli_uses_strict_canonical_hash_not_whole_file_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli
    from backend.tools.frozen_v1_history.credit_card_review import (
        calculated_review_sha256,
    )

    raw = {
        "content_sha256": "",
        "reviewer": "synthetic-reviewer",
        "schema_version": 1,
    }
    expected = calculated_review_sha256(raw)
    raw["content_sha256"] = expected
    path = tmp_path / "review.json"
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    assert hashlib.sha256(path.read_bytes()).hexdigest() != expected
    monkeypatch.setattr(cli, "EXPECTED_CREDIT_CARD_REVIEW_SHA256", expected)
    stdout = _BinaryTextSink()
    stderr = _BinaryTextSink()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert cli.main(("verify-review-content", str(path))) == 0
    assert json.loads(stdout.buffer.getvalue()) == {
        "content_sha256": expected,
        "status": "PASS",
    }
    assert stderr.buffer.getvalue() == b""


def test_review_content_cli_rejects_duplicate_json_keys_without_leaking_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    path = tmp_path / "review.json"
    path.write_text(
        '{"content_sha256":"sentinel-private","content_sha256":"duplicate"}',
        encoding="utf-8",
    )
    stdout = _BinaryTextSink()
    stderr = _BinaryTextSink()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert cli.main(("verify-review-content", str(path))) == 2
    assert stdout.buffer.getvalue() == b""
    assert stderr.buffer.getvalue() == b'{"error":"approved_review_invalid"}\n'
    assert b"sentinel" not in stderr.buffer.getvalue()


def test_reference_mode_stdin_is_capped_and_default_planner_call_remains_no_arg() -> (
    None
):
    from backend.tools.frozen_v1_history import __main__ as cli

    class OversizedInput:
        def read(self, size: int) -> bytes:
            return b"x" * size

    with pytest.raises(ValueError, match="^reference_stdin_too_large$"):
        cli._read_reference_plan(OversizedInput())
    assert cli.main.__defaults__ == (None,)


def test_reference_mode_runtime_failure_is_stable_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    class SourceReadFailure(Exception):
        pass

    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)
    monkeypatch.setattr(cli, "EXPECTED_PLAN_SHA256", plan_sha256(plan))
    monkeypatch.setattr(cli, "read_full_manifest", lambda _path: object())
    monkeypatch.setattr(
        cli,
        "extract_fixed_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SourceReadFailure("sentinel-source-secret")
        ),
    )
    for name, value in {
        "TRACK_ANYWHERE_FROZEN_SOURCE_URL": "postgresql://sentinel-private-dsn",
        "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH": "/fixture/manifest",
        "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH": "/fixture/review",
    }.items():
        monkeypatch.setenv(name, value)
    stdin = _BinaryTextSink(raw)
    stdout = _BinaryTextSink()
    stderr = _BinaryTextSink()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert cli.main(("reference", "--stdin")) == 2
    assert stdout.buffer.getvalue() == b""
    assert stderr.buffer.getvalue() == b'{"error":"reference_compilation_failed"}\n'
    assert b"sentinel" not in stderr.buffer.getvalue()


def _valid_rehearsal_summary() -> dict[str, object]:
    digest = "a" * 64
    return {
        "alembic_version": "v2_0013_frozen_import_fence",
        "archive_sha256": digest,
        "balance_sha256": digest,
        "candidate_image_id": "sha256:" + digest,
        "catalog_identity_sha256": (
            "3b7556099f961ffdd65869fd2cd41af97aa0360406586734fab0cd71bce2dc02"
        ),
        "catalog_sha256": digest,
        "credit_card_review_sha256": (
            "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430"
        ),
        "counts": {
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
        },
        "description_plaintext_sha256": digest,
        "deterministic_ids_sha256": digest,
        "event_order_sha256": digest,
        "event_payloads_sha256": digest,
        "plan_sha256": (
            "c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8"
        ),
        "postgres_version_num": 170006,
        "projection_sha256": digest,
        "quarantine_count": 0,
        "receipt_state": {
            "first_apply": "completed",
            "first_apply_replayed": False,
            "replay": "completed",
            "replay_inserted_total": 0,
            "replayed": True,
        },
        "resource_counts": {"containers": 0, "networks": 0, "volumes": 0},
        "role_names": {
            "migrator": "frozen_migrator",
            "owner": "frozen_owner",
            "runtime": "frozen_runtime",
            "source_reader": "frozen_source_reader",
        },
        "run_id": "11111111-1111-4111-8111-111111111111",
        "source_commit": "b" * 40,
        "source_dump_bytes": 193256,
        "source_dump_sha256": (
            "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e"
        ),
        "source_manifest_sha256": (
            "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f"
        ),
        "status": "PASS",
        "terminal_hash": (
            "bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc"
        ),
    }


def test_verify_report_accepts_only_exact_pass_allowlist(tmp_path: Path) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    report = tmp_path / "summary.json"
    report.write_text(
        json.dumps(_valid_rehearsal_summary(), separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    assert cli._verify_report(report) == {"status": "PASS"}


@pytest.mark.parametrize("mutation", ("unexpected", "non_pass", "residual"))
def test_verify_report_rejects_unknown_nonpass_or_residual_without_echo(
    tmp_path: Path,
    mutation: str,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    payload = _valid_rehearsal_summary()
    if mutation == "unexpected":
        payload["private_name"] = "sentinel-private-value"
    elif mutation == "non_pass":
        payload["status"] = "FAIL"
    else:
        payload["resource_counts"] = {"containers": 1, "networks": 0, "volumes": 0}
    report = tmp_path / "summary.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="^rehearsal_report_invalid$") as caught:
        cli._verify_report(report)
    assert "sentinel-private-value" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("alembic_version", "postgresql://sentinel-private"),
        ("postgres_version_num", 160009),
        ("quarantine_count", 1),
        ("credit_card_review_sha256", "0" * 64),
        ("source_manifest_sha256", "0" * 64),
        ("receipt_state", {"replayed": False}),
        ("role_names", {"owner": "sentinel-private"}),
    ),
)
def test_verify_report_rejects_unpinned_operational_metadata(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    payload = _valid_rehearsal_summary()
    payload[field] = value
    report = tmp_path / "summary.json"
    report.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="^rehearsal_report_invalid$") as caught:
        cli._verify_report(report)
    assert "sentinel-private" not in str(caught.value)


@pytest.mark.parametrize(
    "mutation",
    (
        "count_bool",
        "quarantine_bool",
        "resource_bool",
        "receipt_count_bool",
    ),
)
def test_verify_report_rejects_json_bool_as_integer(
    tmp_path: Path,
    mutation: str,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    payload = _valid_rehearsal_summary()
    if mutation == "count_bool":
        assert type(payload["counts"]) is dict
        payload["counts"]["archives"] = True
    elif mutation == "quarantine_bool":
        payload["quarantine_count"] = False
    elif mutation == "resource_bool":
        assert type(payload["resource_counts"]) is dict
        payload["resource_counts"]["containers"] = False
    else:
        assert type(payload["receipt_state"]) is dict
        payload["receipt_state"]["replay_inserted_total"] = False
    report = tmp_path / "summary.json"
    report.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="^rehearsal_report_invalid$"):
        cli._verify_report(report)
