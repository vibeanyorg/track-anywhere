#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import re
import subprocess
import sys
from typing import BinaryIO, Final


CHUNK_BYTES: Final = 1024 * 1024
_HEX_SHA256: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_CONTAINER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", flags=re.ASCII)
_PG_IDENTIFIER: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", flags=re.ASCII)


class StreamFailure(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parse_arguments(argv: Sequence[str]) -> tuple[str, str, str, int, str]:
    if len(argv) != 10:
        raise StreamFailure("invalid_arguments")
    values: dict[str, str] = {}
    index = 0
    allowed = {
        "--container",
        "--database",
        "--username",
        "--expected-bytes",
        "--expected-sha256",
    }
    while index < len(argv):
        key = argv[index]
        if key not in allowed or key in values or index + 1 >= len(argv):
            raise StreamFailure("invalid_arguments")
        value = argv[index + 1]
        if not value or value.startswith("--"):
            raise StreamFailure("invalid_arguments")
        values[key] = value
        index += 2
    if set(values) != allowed:
        raise StreamFailure("invalid_arguments")

    container = values["--container"]
    database = values["--database"]
    username = values["--username"]
    expected_hash = values["--expected-sha256"]
    try:
        expected_bytes = int(values["--expected-bytes"])
    except ValueError:
        raise StreamFailure("invalid_arguments") from None
    if (
        _CONTAINER.fullmatch(container) is None
        or _PG_IDENTIFIER.fullmatch(database) is None
        or _PG_IDENTIFIER.fullmatch(username) is None
        or expected_bytes <= 0
        or str(expected_bytes) != values["--expected-bytes"]
        or _HEX_SHA256.fullmatch(expected_hash) is None
    ):
        raise StreamFailure("invalid_arguments")
    return container, database, username, expected_bytes, expected_hash


def _stream(
    stdin: BinaryIO,
    *,
    container: str,
    database: str,
    username: str,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, int | str]:
    command = [
        "docker",
        "exec",
        "-i",
        container,
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--username",
        username,
        "--dbname",
        database,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        raise StreamFailure("pg_restore_failed") from None

    digest = hashlib.sha256()
    total = 0
    stream_error = False
    try:
        assert process.stdin is not None
        while True:
            try:
                read_size = min(CHUNK_BYTES, expected_bytes + 1 - total)
                chunk = stdin.read(read_size)
            except Exception:
                stream_error = True
                break
            if type(chunk) is not bytes:
                stream_error = True
                break
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes:
                process.kill()
                process.wait()
                raise StreamFailure("dump_byte_count_mismatch")
            digest.update(chunk)
            try:
                process.stdin.write(chunk)
            except (BrokenPipeError, OSError):
                stream_error = True
                break
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            stream_error = True
        return_code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise

    if return_code != 0:
        raise StreamFailure("pg_restore_failed")
    if stream_error:
        raise StreamFailure("stdin_read_failed")
    if total != expected_bytes:
        raise StreamFailure("dump_byte_count_mismatch")
    actual_hash = digest.hexdigest()
    if actual_hash != expected_sha256:
        raise StreamFailure("dump_sha256_mismatch")
    return {"bytes": total, "sha256": actual_hash, "status": "PASS"}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        container, database, username, expected_bytes, expected_sha256 = (
            _parse_arguments(tuple(sys.argv[1:] if argv is None else argv))
        )
        result = _stream(
            sys.stdin.buffer,
            container=container,
            database=database,
            username=username,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
        )
    except StreamFailure as exc:
        sys.stderr.write(json.dumps({"error": exc.code}, separators=(",", ":")) + "\n")
        return 1
    sys.stdout.write(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
