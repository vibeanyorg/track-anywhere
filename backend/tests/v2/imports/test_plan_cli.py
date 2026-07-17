from __future__ import annotations

import io
import json
import sys

import pytest

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    plan_summary,
)


class _BinaryTextSink:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, value: str) -> int:
        return self.buffer.write(value.encode("utf-8"))

    def flush(self) -> None:
        return None


def _configure_cli(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    monkeypatch.setenv(
        "TRACK_ANYWHERE_FROZEN_SOURCE_URL",
        "postgresql://sentinel-secret-source",
    )
    monkeypatch.setenv("TRACK_ANYWHERE_FROZEN_MANIFEST_PATH", "/fixture/manifest")
    monkeypatch.setenv("TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH", "/fixture/review")
    monkeypatch.setattr(module, "read_full_manifest", lambda _path: object())
    monkeypatch.setattr(
        module,
        "extract_fixed_source",
        lambda _url, **_kwargs: object(),
    )
    monkeypatch.setattr(
        module,
        "read_approved_credit_card_review",
        lambda _path, *, source: object(),
    )


def test_cli_writes_only_plan_bytes_to_stdout_and_safe_summary_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    _configure_cli(monkeypatch, cli)
    plan = build_valid_fixture_plan()
    monkeypatch.setattr(
        cli,
        "compile_frozen_financial_history_plan",
        lambda **_kwargs: plan,
    )
    stdout = _BinaryTextSink()
    stderr = _BinaryTextSink()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert cli.main() == 0
    assert stdout.buffer.getvalue() == canonical_plan_bytes(plan)
    assert json.loads(stderr.buffer.getvalue()) == plan_summary(plan)
    assert b"sentinel-secret-source" not in stderr.buffer.getvalue()


def test_cli_failure_is_stable_and_does_not_echo_exception_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tools.frozen_v1_history import __main__ as cli

    _configure_cli(monkeypatch, cli)
    monkeypatch.setattr(
        cli,
        "compile_frozen_financial_history_plan",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("sentinel-private-value")),
    )
    stdout = _BinaryTextSink()
    stderr = _BinaryTextSink()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert cli.main() == 2
    assert stdout.buffer.getvalue() == b""
    assert stderr.buffer.getvalue() == b'{"error":"plan_compilation_failed"}\n'
    assert b"sentinel-private-value" not in stderr.buffer.getvalue()
