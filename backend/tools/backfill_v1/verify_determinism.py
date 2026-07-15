from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .reference_reducer import canonical_json_bytes


_COMPARABLE_FIELDS = (
    "snapshot_id",
    "manifest_hash",
    "credit_card_review_hash",
    "source_counts",
    "receipt_count",
    "quarantine_count",
    "counts",
    "book_terminal_hashes",
    "projection_hashes",
)


@dataclass(frozen=True, slots=True)
class DeterminismDifference:
    field: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field}


@dataclass(frozen=True, slots=True)
class DeterminismReport:
    differences: tuple[DeterminismDifference, ...]
    run_a_status: str
    run_b_status: str

    @property
    def status(self) -> str:
        return (
            "PASS"
            if self.run_a_status == self.run_b_status == "PASS" and not self.differences
            else "FAIL"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "differences": [difference.to_dict() for difference in self.differences],
            "run_a_status": self.run_a_status,
            "run_b_status": self.run_b_status,
            "status": self.status,
        }


def _read_report(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"verification report is unreadable: {path}") from error
    if type(raw) is not dict or type(raw.get("status")) is not str:
        raise ValueError(f"verification report has an invalid shape: {path}")
    return raw


def compare_verification_reports(
    run_a_path: Path,
    run_b_path: Path,
    *,
    output_path: Path | None = None,
) -> DeterminismReport:
    run_a = _read_report(run_a_path)
    run_b = _read_report(run_b_path)
    differences = tuple(
        DeterminismDifference(field)
        for field in _COMPARABLE_FIELDS
        if run_a.get(field) != run_b.get(field)
    )
    report = DeterminismReport(
        differences=differences,
        run_a_status=str(run_a["status"]),
        run_b_status=str(run_b["status"]),
    )
    if output_path is not None:
        output = Path(output_path)
        if output.exists():
            raise FileExistsError(f"determinism output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(report.to_dict()) + b"\n")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two independent V2 backfill verification reports"
    )
    parser.add_argument("--run-a", type=Path, required=True)
    parser.add_argument("--run-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = compare_verification_reports(
            args.run_a,
            args.run_b,
            output_path=args.output,
        )
    except (FileExistsError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DeterminismDifference",
    "DeterminismReport",
    "compare_verification_reports",
    "main",
]
