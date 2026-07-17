from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
import track_anywhere.application.imports.contracts as contracts
import track_anywhere.application.imports.import_frozen_financial_history as frozen_import
from track_anywhere.application.idempotency import CommandOutcome, CommandResult
from track_anywhere.serialization.canonical_json import canonical_json_bytes
import track_anywhere.offline.import_frozen_financial_history as runner


TARGET_BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
DATABASE_URL = "postgresql+psycopg://runtime:password@127.0.0.1:9/track_anywhere"
KEYRING_PATH = "/run/secrets/track_anywhere_protected_content_keyring"


@pytest.fixture(scope="module")
def fixture_plan():
    return build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)


@pytest.fixture(scope="module")
def fixture_plan_bytes(fixture_plan) -> bytes:
    return contracts.canonical_plan_bytes(fixture_plan)


def _argv(plan_hash: str, *, target_book_id: str | None = None) -> list[str]:
    return [
        "--target-book-id",
        target_book_id or str(TARGET_BOOK_ID),
        "--plan-sha256",
        plan_hash,
        "--stdin",
    ]


def _environment(*, configured: bool = True) -> dict[str, str]:
    if not configured:
        return {}
    return {
        "TRACK_ANYWHERE_DATABASE_URL": DATABASE_URL,
        "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE": KEYRING_PATH,
    }


def _inserted_counts() -> dict[str, int]:
    return {
        "accounts": 57,
        "archives": 1,
        "assets": 4,
        "categories": 37,
        "category_versions": 37,
        "credit_card_transactions": 0,
        "descriptions": 138,
        "events": 176,
        "journal_transactions": 138,
        "postings": 290,
        "quarantine": 0,
        "reporting_lines": 38,
        "reversals": 8,
    }


def _outcome(fixture_plan, *, replayed: bool) -> CommandOutcome:
    plan_hash = contracts.plan_sha256(fixture_plan)
    counts = contracts.plan_summary(fixture_plan)["counts"]
    assert type(counts) is dict
    return CommandOutcome(
        result=CommandResult(
            response_schema_version=1,
            status_code=201,
            body={
                "book_id": str(TARGET_BOOK_ID),
                "plan_hash": plan_hash,
                "expected_terminal_hash": fixture_plan.expected_terminal_hash,
                "counts": counts,
                "inserted_counts": _inserted_counts(),
            },
            first_book_position=1,
            last_book_position=176,
        ),
        replayed=replayed,
    )


def _invoke(
    argv: list[str],
    payload: bytes,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[int, bytes, bytes]:
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    status = runner._run(
        argv,
        stdin=io.BytesIO(payload),
        stdout=stdout,
        stderr=stderr,
        environ=_environment() if environ is None else environ,
    )
    return status, stdout.getvalue(), stderr.getvalue()


def _install_success_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: CommandOutcome,
    calls: list[str] | None = None,
) -> None:
    trace = [] if calls is None else calls

    class FakeKeyring:
        @classmethod
        def from_file(cls, path: str):
            assert path == KEYRING_PATH
            trace.append("keyring")
            return cls()

    class FakeEngine:
        def dispose(self) -> None:
            trace.append("dispose")

    monkeypatch.setattr(runner, "ProtectedContentKeyring", FakeKeyring)
    monkeypatch.setattr(
        runner,
        "ProtectedContentCipher",
        lambda keyring: trace.append("cipher") or SimpleNamespace(keyring=keyring),
    )
    monkeypatch.setattr(
        runner,
        "create_v2_engine",
        lambda database_url: trace.append("engine") or FakeEngine(),
    )
    monkeypatch.setattr(
        runner,
        "sessionmaker",
        lambda **kwargs: trace.append("sessionmaker") or (lambda: None),
    )
    monkeypatch.setattr(
        runner,
        "import_frozen_financial_history",
        lambda *args, **kwargs: trace.append("import") or outcome,
    )


def test_invalid_arguments_are_rejected_before_stdin_or_runtime_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreadableInput:
        def read(self, _size: int) -> bytes:
            raise AssertionError("invalid arguments must be rejected before stdin")

    monkeypatch.setattr(
        runner.ProtectedContentKeyring,
        "from_file",
        lambda *_args, **_kwargs: pytest.fail("keyring must remain untouched"),
    )
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    status = runner._run(
        ["--stdin"],
        stdin=UnreadableInput(),
        stdout=stdout,
        stderr=stderr,
        environ=_environment(),
    )

    assert status == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == b'{"error":"invalid_arguments"}\n'


def test_stdin_is_read_with_max_plus_one_and_oversize_is_rejected_before_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    class OversizedInput:
        def read(self, size: int) -> bytes:
            requested.append(size)
            return b"x" * size

    monkeypatch.setattr(
        runner,
        "parse_canonical_plan_bytes",
        lambda _raw: pytest.fail("oversized stdin must not be parsed"),
    )
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    status = runner._run(
        _argv("0" * 64),
        stdin=OversizedInput(),
        stdout=stdout,
        stderr=stderr,
        environ=_environment(),
    )

    assert requested == [runner.MAX_STDIN_BYTES + 1]
    assert status == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == b'{"error":"stdin_too_large"}\n'


def test_short_read_cannot_hide_trailing_stdin_bytes_before_runtime_access(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    reads: list[int] = []
    chunks = iter((fixture_plan_bytes, b"trailing", b""))

    class ShortReadInput:
        def read(self, size: int) -> bytes:
            reads.append(size)
            return next(chunks)

    monkeypatch.setattr(
        runner.ProtectedContentKeyring,
        "from_file",
        lambda *_args, **_kwargs: pytest.fail("runtime must remain untouched"),
    )
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    status = runner._run(
        _argv(contracts.plan_sha256(fixture_plan)),
        stdin=ShortReadInput(),
        stdout=stdout,
        stderr=stderr,
        environ=_environment(),
    )

    assert len(reads) == 3
    assert status == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == b'{"error":"invalid_plan"}\n'


def test_chunked_valid_stdin_is_read_to_eof_before_runtime_configuration(
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    split = len(fixture_plan_bytes) // 2
    chunks = iter((fixture_plan_bytes[:split], fixture_plan_bytes[split:], b""))

    class ChunkedInput:
        def read(self, _size: int) -> bytes:
            return next(chunks)

    stdout = io.BytesIO()
    stderr = io.BytesIO()
    status = runner._run(
        _argv(contracts.plan_sha256(fixture_plan)),
        stdin=ChunkedInput(),
        stdout=stdout,
        stderr=stderr,
        environ={},
    )

    assert status == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == b'{"error":"runtime_configuration_invalid"}\n'


def test_chunked_oversize_and_midstream_read_failure_are_safe() -> None:
    cases = (
        (
            iter((b"x" * runner.MAX_STDIN_BYTES, b"x")),
            b'{"error":"stdin_too_large"}\n',
        ),
        (iter((b"{", OSError("stdin-secret"))), b'{"error":"stdin_read_failed"}\n'),
    )
    for chunks, expected_error in cases:

        class FailingInput:
            def read(self, _size: int) -> bytes:
                value = next(chunks)
                if isinstance(value, Exception):
                    raise value
                return value

        stdout = io.BytesIO()
        stderr = io.BytesIO()
        status = runner._run(
            _argv("0" * 64),
            stdin=FailingInput(),
            stdout=stdout,
            stderr=stderr,
            environ={},
        )

        assert status == 2
        assert stdout.getvalue() == b""
        assert stderr.getvalue() == expected_error
        assert b"stdin-secret" not in stderr.getvalue()


@pytest.mark.parametrize(
    ("argv", "expected_error"),
    [
        (
            _argv("0" * 64, target_book_id="00000000-0000-0000-0000-000000000000"),
            "invalid_arguments",
        ),
        (_argv("f" * 64), "plan_contract_mismatch"),
    ],
)
def test_target_and_hash_mismatch_fail_before_keyring_engine_or_database(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan_bytes: bytes,
    argv: list[str],
    expected_error: str,
) -> None:
    monkeypatch.setattr(
        runner.ProtectedContentKeyring,
        "from_file",
        lambda *_args, **_kwargs: pytest.fail("keyring must remain untouched"),
    )
    monkeypatch.setattr(
        runner,
        "create_v2_engine",
        lambda *_args, **_kwargs: pytest.fail("engine must remain untouched"),
    )

    status, stdout, stderr = _invoke(argv, fixture_plan_bytes)

    assert status == 2
    assert stdout == b""
    assert json.loads(stderr) == {"error": expected_error}


def test_runtime_configuration_is_mandatory_but_read_only_after_pure_validation(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    parsed = False
    built = False
    real_parse = runner.parse_canonical_plan_bytes
    real_build = runner.build_frozen_financial_history_command

    def parse(raw: bytes):
        nonlocal parsed
        parsed = True
        return real_parse(raw)

    def build(plan, *, expected_plan_hash: str):
        nonlocal built
        built = True
        return real_build(plan, expected_plan_hash=expected_plan_hash)

    monkeypatch.setattr(runner, "parse_canonical_plan_bytes", parse)
    monkeypatch.setattr(runner, "build_frozen_financial_history_command", build)

    status, stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
        environ={},
    )

    assert (parsed, built) == (True, True)
    assert status == 2
    assert stdout == b""
    assert stderr == b'{"error":"runtime_configuration_invalid"}\n'


def test_entrypoint_orders_parse_and_pure_builder_before_keyring_and_lazy_engine(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    calls: list[str] = []
    real_parse = runner.parse_canonical_plan_bytes
    real_build = runner.build_frozen_financial_history_command

    def parse(raw: bytes):
        calls.append("parse")
        return real_parse(raw)

    def build(plan, *, expected_plan_hash: str):
        calls.append("builder")
        return real_build(plan, expected_plan_hash=expected_plan_hash)

    monkeypatch.setattr(runner, "parse_canonical_plan_bytes", parse)
    monkeypatch.setattr(runner, "build_frozen_financial_history_command", build)
    _install_success_runtime(
        monkeypatch,
        outcome=_outcome(fixture_plan, replayed=False),
        calls=calls,
    )

    status, _stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 0
    assert stderr == b""
    assert calls == [
        "parse",
        "builder",
        "keyring",
        "cipher",
        "engine",
        "sessionmaker",
        "import",
        "dispose",
    ]


def test_runner_uses_one_fixed_idempotency_key_for_conflict_safe_replay(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    captured: dict[str, object] = {}
    _install_success_runtime(
        monkeypatch,
        outcome=_outcome(fixture_plan, replayed=False),
    )

    def execute(_plan, **kwargs):
        captured.update(kwargs)
        return _outcome(fixture_plan, replayed=False)

    monkeypatch.setattr(runner, "import_frozen_financial_history", execute)

    status, _stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 0
    assert stderr == b""
    assert captured["raw_key"] == "frozen-v1-financial-history"


def test_entire_entrypoint_chain_parses_canonical_stdin_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
    tmp_path: Path,
) -> None:
    keyring_path = tmp_path / "protected-content-keyring.json"
    keyring_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_key_ref": "v1",
                "keys": {"v1": base64.b64encode(bytes(range(32))).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    keyring_path.chmod(0o600)
    parse_calls = 0
    real_parse = contracts.parse_canonical_plan_bytes

    def counted_parse(raw: bytes):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(runner, "parse_canonical_plan_bytes", counted_parse)
    monkeypatch.setattr(contracts, "parse_canonical_plan_bytes", counted_parse)
    for module in (frozen_import,):
        if hasattr(module, "parse_canonical_plan_bytes"):
            monkeypatch.setattr(module, "parse_canonical_plan_bytes", counted_parse)

    class FakeEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(runner, "create_v2_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(runner, "sessionmaker", lambda **_kwargs: lambda: None)
    monkeypatch.setattr(
        frozen_import,
        "execute_financial",
        lambda *_args, **_kwargs: _outcome(fixture_plan, replayed=False),
    )
    environ = {
        "TRACK_ANYWHERE_DATABASE_URL": DATABASE_URL,
        "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE": os.fspath(keyring_path),
    }

    status, _stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
        environ=environ,
    )

    assert status == 0
    assert stderr == b""
    assert parse_calls == 1
    task8_source = Path(frozen_import.__file__).read_text(encoding="utf-8")
    assert "parse_canonical_plan_bytes" not in task8_source


@pytest.mark.parametrize("replayed", [False, True])
def test_success_is_one_canonical_allowlisted_line_and_replay_reports_zero_inserts(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
    replayed: bool,
) -> None:
    outcome = _outcome(fixture_plan, replayed=replayed)
    _install_success_runtime(monkeypatch, outcome=outcome)

    status, stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 0
    assert stderr == b""
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1
    summary = json.loads(stdout)
    assert set(summary) == {
        "counts",
        "expected_terminal_hash",
        "first_book_position",
        "inserted_counts",
        "last_book_position",
        "plan_hash",
        "receipt_state",
        "replayed",
    }
    assert summary["receipt_state"] == "completed"
    assert summary["replayed"] is replayed
    assert summary["plan_hash"] == contracts.plan_sha256(fixture_plan)
    assert summary["expected_terminal_hash"] == fixture_plan.expected_terminal_hash
    assert (summary["first_book_position"], summary["last_book_position"]) == (
        1,
        176,
    )
    expected_inserted = (
        {key: 0 for key in _inserted_counts()} if replayed else _inserted_counts()
    )
    assert summary["inserted_counts"] == expected_inserted
    assert stdout == canonical_json_bytes(summary) + b"\n"
    assert b"fixture-account" not in stdout
    assert b"fixture-purpose" not in stdout


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("expected_terminal_hash", "0" * 64),
        *(
            (f"inserted_{key}", value + 1)
            for key, value in sorted(_inserted_counts().items())
        ),
        ("inserted_events", "176"),
    ],
)
def test_success_summary_rejects_receipt_facts_not_bound_to_the_fixed_plan(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
    field: str,
    corrupt_value: str | int,
) -> None:
    outcome = _outcome(fixture_plan, replayed=False)
    assert type(outcome.result.body) is dict
    if field == "expected_terminal_hash":
        outcome.result.body["expected_terminal_hash"] = corrupt_value
    else:
        inserted = outcome.result.body["inserted_counts"]
        assert type(inserted) is dict
        inserted[field.removeprefix("inserted_")] = corrupt_value
    _install_success_runtime(monkeypatch, outcome=outcome)

    status, stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 1
    assert stdout == b""
    assert stderr == b'{"error":"frozen_import_failed"}\n'


def test_runtime_failure_is_stable_safe_json_without_traceback_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    secret = "postgresql+psycopg://runtime:DO_NOT_LEAK@example.invalid/database"

    class FakeKeyring:
        @classmethod
        def from_file(cls, _path: str):
            return cls()

    monkeypatch.setattr(runner, "ProtectedContentKeyring", FakeKeyring)
    monkeypatch.setattr(runner, "ProtectedContentCipher", lambda _keyring: object())
    monkeypatch.setattr(
        runner,
        "create_v2_engine",
        lambda _url: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    status, stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 1
    assert stdout == b""
    assert stderr == b'{"error":"frozen_import_failed"}\n'
    assert secret.encode() not in stderr
    assert b"Traceback" not in stderr


def test_success_output_failure_returns_safe_error_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    class BrokenOutput:
        def write(self, _payload: bytes) -> int:
            raise OSError("operator-terminal-secret")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    _install_success_runtime(
        monkeypatch,
        outcome=_outcome(fixture_plan, replayed=False),
    )
    stderr = io.BytesIO()

    status = runner._run(
        _argv(contracts.plan_sha256(fixture_plan)),
        stdin=io.BytesIO(fixture_plan_bytes),
        stdout=BrokenOutput(),
        stderr=stderr,
        environ=_environment(),
    )

    assert status == 1
    assert stderr.getvalue() == b'{"error":"frozen_import_failed"}\n'
    assert b"operator-terminal-secret" not in stderr.getvalue()


def test_untrusted_result_body_cannot_expand_the_stdout_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    fixture_plan,
    fixture_plan_bytes: bytes,
) -> None:
    outcome = _outcome(fixture_plan, replayed=False)
    assert type(outcome.result.body) is dict
    outcome.result.body["database_url"] = "postgresql://secret@example.invalid/db"
    _install_success_runtime(monkeypatch, outcome=outcome)

    status, stdout, stderr = _invoke(
        _argv(contracts.plan_sha256(fixture_plan)),
        fixture_plan_bytes,
    )

    assert status == 1
    assert stdout == b""
    assert stderr == b'{"error":"frozen_import_failed"}\n'
    assert b"secret" not in stderr


def test_runner_has_no_plaintext_plan_path_or_temporary_file_surface() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "--plan-file",
        "--plan-path",
        "NamedTemporaryFile",
        "TemporaryDirectory",
        "mkstemp",
        "read_bytes(",
        "read_text(",
        "write_bytes(",
        "write_text(",
        "open(",
    ):
        assert forbidden not in source
    assert '"--stdin"' in source
    assert "MAX_STDIN_BYTES" in source
    assert "from_file" in source  # The protected-content keyring remains mandatory.
