from __future__ import annotations

from collections.abc import Mapping, Sequence
import hmac
import os
import re
import sys
from types import MappingProxyType
from typing import BinaryIO, Final

from sqlalchemy.orm import sessionmaker

from ..application.idempotency import CommandActor, CommandOutcome
from ..application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    parse_canonical_plan_bytes,
    plan_sha256,
)
from ..application.imports.import_frozen_financial_history import (
    FROZEN_IMPORT_TARGET_BOOK_ID,
    build_frozen_financial_history_command,
    import_frozen_financial_history,
)
from ..infrastructure.crypto import ProtectedContentCipher, ProtectedContentKeyring
from ..infrastructure.db.engine import create_v2_engine
from ..infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from ..serialization.canonical_json import JSONValue, canonical_json_bytes


MAX_STDIN_BYTES: Final = 8 * 1024 * 1024
DATABASE_URL_ENV: Final = "TRACK_ANYWHERE_DATABASE_URL"
PROTECTED_CONTENT_KEYRING_FILE_ENV: Final = (
    "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE"
)

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_IDEMPOTENCY_KEY: Final = "frozen-v1-financial-history"
_RESULT_BODY_KEYS: Final = frozenset(
    {
        "book_id",
        "plan_hash",
        "expected_terminal_hash",
        "counts",
        "inserted_counts",
    }
)
_INSERTED_COUNT_KEYS: Final = frozenset(
    {
        "accounts",
        "archives",
        "assets",
        "categories",
        "category_versions",
        "credit_card_transactions",
        "descriptions",
        "events",
        "journal_transactions",
        "postings",
        "quarantine",
        "reporting_lines",
        "reversals",
    }
)
_EXPECTED_FIRST_INSERTED_COUNTS: Final = MappingProxyType(
    {
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
)


class _RunnerFailure(Exception):
    __slots__ = ("code", "exit_code")

    def __init__(self, code: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


def _parse_arguments(argv: Sequence[str]) -> tuple[str, str]:
    try:
        arguments = tuple(argv)
    except TypeError:
        raise _RunnerFailure("invalid_arguments") from None
    if (
        len(arguments) != 5
        or any(type(argument) is not str for argument in arguments)
        or arguments.count("--stdin") != 1
        or arguments.count("--target-book-id") != 1
        or arguments.count("--plan-sha256") != 1
    ):
        raise _RunnerFailure("invalid_arguments")

    values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--stdin":
            index += 1
            continue
        if argument not in {"--target-book-id", "--plan-sha256"}:
            raise _RunnerFailure("invalid_arguments")
        if index + 1 >= len(arguments):
            raise _RunnerFailure("invalid_arguments")
        value = arguments[index + 1]
        if value.startswith("--"):
            raise _RunnerFailure("invalid_arguments")
        values[argument] = value
        index += 2

    target_book_id = values.get("--target-book-id")
    expected_plan_hash = values.get("--plan-sha256")
    if (
        target_book_id != str(FROZEN_IMPORT_TARGET_BOOK_ID)
        or type(expected_plan_hash) is not str
        or _HEX_SHA256.fullmatch(expected_plan_hash) is None
    ):
        raise _RunnerFailure("invalid_arguments")
    return target_book_id, expected_plan_hash


def _read_stdin(stdin: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_STDIN_BYTES:
        try:
            chunk = stdin.read(MAX_STDIN_BYTES + 1 - total)
        except Exception:
            raise _RunnerFailure("stdin_read_failed") from None
        if type(chunk) is not bytes:
            raise _RunnerFailure("stdin_read_failed")
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_STDIN_BYTES:
            raise _RunnerFailure("stdin_too_large")
    raise _RunnerFailure("stdin_too_large")


def _required_runtime_value(environ: Mapping[str, str], name: str) -> str:
    try:
        value = environ.get(name)
    except Exception:
        raise _RunnerFailure("runtime_configuration_invalid") from None
    if type(value) is not str or not value:
        raise _RunnerFailure("runtime_configuration_invalid")
    return value


def _safe_summary(
    outcome: CommandOutcome,
    *,
    expected_plan_hash: str,
    expected_terminal_hash: str,
    expected_counts: dict[str, int],
) -> dict[str, JSONValue]:
    try:
        if type(outcome) is not CommandOutcome or type(outcome.replayed) is not bool:
            raise ValueError
        result = outcome.result
        body = result.body
        if (
            result.response_schema_version != 1
            or result.status_code != 201
            or type(body) is not dict
            or set(body) != _RESULT_BODY_KEYS
            or body.get("book_id") != str(FROZEN_IMPORT_TARGET_BOOK_ID)
            or body.get("plan_hash") != expected_plan_hash
            or body.get("counts") != expected_counts
            or type(body.get("expected_terminal_hash")) is not str
            or _HEX_SHA256.fullmatch(body["expected_terminal_hash"]) is None
            or not hmac.compare_digest(
                body["expected_terminal_hash"], expected_terminal_hash
            )
            or type(result.first_book_position) is not int
            or result.first_book_position != 1
            or type(result.last_book_position) is not int
            or result.last_book_position != expected_counts["events"]
        ):
            raise ValueError
        inserted = body.get("inserted_counts")
        if (
            type(inserted) is not dict
            or set(inserted) != _INSERTED_COUNT_KEYS
            or any(type(value) is not int or value < 0 for value in inserted.values())
            or inserted != _EXPECTED_FIRST_INSERTED_COUNTS
        ):
            raise ValueError
        safe_inserted = {
            key: 0 if outcome.replayed else inserted[key]
            for key in sorted(_INSERTED_COUNT_KEYS)
        }
        return {
            "counts": dict(sorted(expected_counts.items())),
            "expected_terminal_hash": body["expected_terminal_hash"],
            "first_book_position": result.first_book_position,
            "inserted_counts": safe_inserted,
            "last_book_position": result.last_book_position,
            "plan_hash": expected_plan_hash,
            "receipt_state": "completed",
            "replayed": outcome.replayed,
        }
    except (KeyError, TypeError, ValueError):
        raise _RunnerFailure("frozen_import_failed", exit_code=1) from None


def _execute(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    environ: Mapping[str, str],
) -> dict[str, JSONValue]:
    target_book_id, expected_plan_hash = _parse_arguments(argv)
    raw = _read_stdin(stdin)
    try:
        plan = parse_canonical_plan_bytes(raw)
    except (TypeError, ValueError):
        raise _RunnerFailure("invalid_plan") from None
    finally:
        raw = b""

    try:
        actual_plan_hash = plan_sha256(plan)
        if (
            plan.target_book_id != FROZEN_IMPORT_TARGET_BOOK_ID
            or str(plan.target_book_id) != target_book_id
            or not hmac.compare_digest(actual_plan_hash, expected_plan_hash)
        ):
            raise ValueError
        command = build_frozen_financial_history_command(
            plan,
            expected_plan_hash=expected_plan_hash,
        )
        expected_counts = dict(command.counts)
    except (TypeError, ValueError):
        raise _RunnerFailure("plan_contract_mismatch") from None

    database_url = _required_runtime_value(environ, DATABASE_URL_ENV)
    keyring_file = _required_runtime_value(
        environ,
        PROTECTED_CONTENT_KEYRING_FILE_ENV,
    )

    keyring = ProtectedContentKeyring.from_file(keyring_file)
    cipher = ProtectedContentCipher(keyring)
    engine = create_v2_engine(database_url)
    try:
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            future=True,
        )

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        outcome = import_frozen_financial_history(
            plan,
            expected_plan_hash=expected_plan_hash,
            raw_key=_IDEMPOTENCY_KEY,
            actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
            uow_factory=uow_factory,
            protected_content_cipher=cipher,
        )
        summary = _safe_summary(
            outcome,
            expected_plan_hash=expected_plan_hash,
            expected_terminal_hash=command.expected_terminal_hash,
            expected_counts=expected_counts,
        )
    finally:
        engine.dispose()

    del plan, command, outcome, cipher, keyring
    return summary


def _write_json_line(stream: BinaryIO, value: dict[str, JSONValue]) -> None:
    stream.write(canonical_json_bytes(value) + b"\n")
    stream.flush()


def _report_failure(stderr: BinaryIO, code: str, *, exit_code: int) -> int:
    try:
        _write_json_line(stderr, {"error": code})
    except Exception:
        pass
    return exit_code


def _run(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    stdout: BinaryIO,
    stderr: BinaryIO,
    environ: Mapping[str, str],
) -> int:
    try:
        summary = _execute(argv, stdin=stdin, environ=environ)
    except _RunnerFailure as error:
        return _report_failure(stderr, error.code, exit_code=error.exit_code)
    except Exception:
        return _report_failure(stderr, "frozen_import_failed", exit_code=1)
    try:
        _write_json_line(stdout, summary)
    except Exception:
        return _report_failure(stderr, "frozen_import_failed", exit_code=1)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _run(
        sys.argv[1:] if argv is None else argv,
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
        environ=os.environ,
    )


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    raise SystemExit(main())


__all__ = ["MAX_STDIN_BYTES", "main"]
