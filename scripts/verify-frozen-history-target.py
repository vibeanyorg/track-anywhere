#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
import sys
from typing import BinaryIO, Final
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from backend.tools.frozen_v1_history.reference_artifact import (
    ReferenceArtifactError,
    TARGET_BOOK_ID,
    parse_reference_artifact,
)
from backend.tools.frozen_v1_history.reference_reducer import ReferenceLedgerFacts
from backend.tools.frozen_v1_history.verify import (
    FrozenHistoryVerificationReport,
    read_frozen_history_observation,
    verify_frozen_history,
)
from track_anywhere.infrastructure.crypto import (
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.engine import create_v2_engine
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.serialization.canonical_json import canonical_json_bytes


MAX_STDIN_BYTES: Final = 1024 * 1024
DATABASE_URL_ENV: Final = "TRACK_ANYWHERE_DATABASE_URL"
KEYRING_FILE_ENV: Final = "TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE"


class TargetVerificationFailure(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _read_stdin(stdin: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_STDIN_BYTES:
        try:
            chunk = stdin.read(MAX_STDIN_BYTES + 1 - total)
        except Exception:
            raise TargetVerificationFailure("stdin_read_failed") from None
        if type(chunk) is not bytes:
            raise TargetVerificationFailure("stdin_read_failed")
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_STDIN_BYTES:
            raise TargetVerificationFailure("stdin_too_large")
    raise TargetVerificationFailure("stdin_too_large")


def _runtime_value(environ: Mapping[str, str], name: str) -> str:
    try:
        value = environ.get(name)
    except Exception:
        raise TargetVerificationFailure("runtime_configuration_invalid") from None
    if type(value) is not str or not value:
        raise TargetVerificationFailure("runtime_configuration_invalid")
    return value


def _verify_target(
    database_url: str,
    keyring_file: str,
    reference: ReferenceLedgerFacts,
) -> FrozenHistoryVerificationReport:
    engine = create_v2_engine(database_url)
    try:
        keyring = ProtectedContentKeyring.from_file(keyring_file)
        cipher = ProtectedContentCipher(keyring)
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            future=True,
        )
        worker = AsyncProjectionWorker(session_factory, batch_size=500)
        for _ in range(8):
            projection = worker.run_once(UUID(TARGET_BOOK_ID))
            if projection.paused:
                raise TargetVerificationFailure("projection_failed")
            if projection.processed_events == 0:
                if projection.last_book_position != reference.terminal_position:
                    raise TargetVerificationFailure("projection_incomplete")
                break
        else:
            raise TargetVerificationFailure("projection_incomplete")

        with session_factory() as session:
            observation = read_frozen_history_observation(
                session,
                reference=reference,
                cipher=cipher,
            )
            report = verify_frozen_history(reference, observation)
        del observation, cipher, keyring
        return report
    finally:
        engine.dispose()


def _execute(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    environ: Mapping[str, str],
    verify_operation: Callable[
        [str, str, ReferenceLedgerFacts], FrozenHistoryVerificationReport
    ] = _verify_target,
) -> dict[str, object]:
    if tuple(argv) != ("--stdin",):
        raise TargetVerificationFailure("invalid_arguments")
    raw = _read_stdin(stdin)
    try:
        reference = parse_reference_artifact(raw)
    except ReferenceArtifactError:
        raise TargetVerificationFailure("reference_artifact_invalid") from None
    finally:
        raw = b""

    database_url = _runtime_value(environ, DATABASE_URL_ENV)
    keyring_file = _runtime_value(environ, KEYRING_FILE_ENV)
    report = verify_operation(database_url, keyring_file, reference)
    if type(report) is not FrozenHistoryVerificationReport or report.status != "PASS":
        raise TargetVerificationFailure("target_verification_failed")
    return report.to_dict()


def _write_line(stream: BinaryIO, value: dict[str, object]) -> None:
    stream.write(canonical_json_bytes(value) + b"\n")  # type: ignore[arg-type]
    stream.flush()


def _run(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    stdout: BinaryIO,
    stderr: BinaryIO,
    environ: Mapping[str, str],
) -> int:
    try:
        report = _execute(argv, stdin=stdin, environ=environ)
    except TargetVerificationFailure as exc:
        _write_line(stderr, {"error": exc.code})
        return 1
    except Exception:
        _write_line(stderr, {"error": "target_verification_failed"})
        return 1
    try:
        _write_line(stdout, report)
    except Exception:
        _write_line(stderr, {"error": "target_verification_failed"})
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _run(
        sys.argv[1:] if argv is None else argv,
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
        environ=os.environ,
    )


if __name__ == "__main__":
    raise SystemExit(main())
