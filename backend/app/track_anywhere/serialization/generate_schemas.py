from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .event_registry import (
    PRODUCTION_EVENT_REGISTRY,
    EventRegistry,
    EventRegistryError,
)


_SCHEMA_ID_BASE = "https://schemas.track-anywhere.dev/v2/events"
_INVALID_OUTPUT_DIRECTORY = "<non-regular-output-directory>"


class SchemaGenerationError(ValueError):
    """A safe, fail-closed schema filesystem error."""


@dataclass(frozen=True, slots=True)
class SchemaCheckResult:
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.unexpected)


def default_schema_directory() -> Path:
    return Path(__file__).with_name("schemas")


def _filename(event_type: str, schema_version: int) -> str:
    return f"{event_type}.v{schema_version}.json"


def _schema_document(
    model: type[Any],
    event_type: str,
    schema_version: int,
) -> dict[str, Any]:
    filename = _filename(event_type, schema_version)
    document = model.model_json_schema(
        mode="serialization",
        ref_template="#/$defs/{model}",
    )
    document["$id"] = f"{_SCHEMA_ID_BASE}/{filename}"
    document["x-event-type"] = event_type
    document["x-schema-version"] = schema_version
    return document


def schema_file_bytes(
    registry: EventRegistry = PRODUCTION_EVENT_REGISTRY,
) -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    registrations = registry.registrations()
    for (event_type, schema_version), model in registrations:
        filename = _filename(event_type, schema_version)
        document = _schema_document(model, event_type, schema_version)
        rendered[filename] = (
            json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
    registry.registrations()
    return dict(sorted(rendered.items()))


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None


def _prepare_output_directory(output_dir: Path) -> None:
    state = _lstat(output_dir)
    if state is None:
        try:
            output_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            pass
        state = _lstat(output_dir)
    if state is None or not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
        raise SchemaGenerationError("schema output directory must be a real directory")


def _write_atomic(path: Path, raw: bytes) -> None:
    temporary_path: Path | None = None
    write_failed = False
    descriptor = -1
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".track-anywhere-schema-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError:
        write_failed = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    if write_failed:
        raise SchemaGenerationError("schema file write failed")


def _read_regular_file_without_following(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def generate_schemas(
    output_dir: Path,
    registry: EventRegistry = PRODUCTION_EVENT_REGISTRY,
) -> tuple[Path, ...]:
    expected = schema_file_bytes(registry)
    _prepare_output_directory(output_dir)
    entries = {entry.name: entry for entry in output_dir.iterdir()}
    if set(entries) - set(expected):
        raise SchemaGenerationError("schema output directory has unmanaged entries")
    for filename in set(entries) & set(expected):
        state = _lstat(entries[filename])
        if state is None or not stat.S_ISREG(state.st_mode):
            raise SchemaGenerationError("schema target must be a regular file")

    paths: list[Path] = []
    for filename, raw in expected.items():
        path = output_dir / filename
        _write_atomic(path, raw)
        paths.append(path)
    return tuple(paths)


def check_schemas(
    output_dir: Path,
    registry: EventRegistry = PRODUCTION_EVENT_REGISTRY,
) -> SchemaCheckResult:
    expected = schema_file_bytes(registry)
    expected_names = set(expected)
    output_state = _lstat(output_dir)
    if output_state is None:
        return SchemaCheckResult(tuple(sorted(expected_names)), (), ())
    if not stat.S_ISDIR(output_state.st_mode) or stat.S_ISLNK(output_state.st_mode):
        return SchemaCheckResult(
            tuple(sorted(expected_names)), (), (_INVALID_OUTPUT_DIRECTORY,)
        )

    entries = {entry.name: entry for entry in output_dir.iterdir()}
    actual_names = set(entries)
    missing = tuple(sorted(expected_names - actual_names))
    unexpected = tuple(sorted(actual_names - expected_names))
    changed_names: list[str] = []
    for filename in sorted(expected_names & actual_names):
        actual = _read_regular_file_without_following(entries[filename])
        if actual is None or actual != expected[filename]:
            changed_names.append(filename)
    changed = tuple(changed_names)
    return SchemaCheckResult(missing, changed, unexpected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate V2 event JSON Schemas")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare exact committed filenames and bytes without writing",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_schema_directory(),
        help="schema output directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    operation_failed = False
    result: SchemaCheckResult | None = None
    try:
        if arguments.check:
            result = check_schemas(arguments.output_dir)
        else:
            generate_schemas(arguments.output_dir)
    except (OSError, EventRegistryError, SchemaGenerationError):
        operation_failed = True
    if operation_failed:
        print("schema operation failed", file=sys.stderr)
        return 1
    if result is not None and not result.ok:
        for category in ("missing", "changed", "unexpected"):
            for filename in getattr(result, category):
                print(f"{category}: {filename}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
