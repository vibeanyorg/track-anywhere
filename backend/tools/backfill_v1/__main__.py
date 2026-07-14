from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config import BackfillConfig
from .extract import extract_database, load_extracted_rows
from .inventory import inventory_rows, write_inventory
from .manifest import build_manifest, sha256_file, write_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.tools.backfill_v1",
        description="Offline deterministic V1 backfill extraction tools",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    freeze = commands.add_parser(
        "freeze-manifest", help="bind a dump hash to one V1 schema revision"
    )
    freeze.add_argument("--dump", type=Path, required=True)
    freeze.add_argument("--source-revision", required=True)
    freeze.add_argument("--output", type=Path, required=True)

    extract = commands.add_parser(
        "extract", help="extract canonical NDJSON from a read-only restored V1 database"
    )
    extract.add_argument("--source-url", required=True)
    extract.add_argument("--target-url", required=True)
    extract.add_argument("--dump", type=Path, required=True)
    extract.add_argument("--frozen-manifest", type=Path, required=True)
    extract.add_argument("--source-revision", required=True)
    extract.add_argument("--output-dir", type=Path, required=True)
    extract.add_argument("--batch-size", type=int, default=500)
    extract.add_argument("--workers", type=int, default=1)
    extract.add_argument("--shuffle-seed", type=int, default=0)

    inventory = commands.add_parser(
        "inventory", help="re-run referential inventory over a canonical extraction"
    )
    inventory.add_argument("--extraction-dir", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)

    run = commands.add_parser(
        "run", help="extract and resumably load one frozen V1 snapshot into V2"
    )
    run.add_argument("--source-url", required=True)
    run.add_argument("--target-url", required=True)
    run.add_argument("--dump", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--batch-size", type=int, default=500)
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--shuffle-seed", type=int, default=0)
    return parser


def _freeze_manifest(args: argparse.Namespace) -> int:
    if not args.dump.is_file():
        raise ValueError("dump path must be an existing regular file")
    if args.output.exists():
        raise FileExistsError(f"manifest output already exists: {args.output}")
    manifest = build_manifest(
        dump_sha256=sha256_file(args.dump),
        source_revision=args.source_revision,
        tables=(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, args.output)
    print(manifest.snapshot_id)
    return 0


def _extract(args: argparse.Namespace) -> int:
    result = extract_database(
        BackfillConfig(
            source_url=args.source_url,
            target_url=args.target_url,
            dump_path=args.dump,
            source_revision=args.source_revision,
            output_dir=args.output_dir,
            batch_size=args.batch_size,
            workers=args.workers,
            shuffle_seed=args.shuffle_seed,
            frozen_manifest_path=args.frozen_manifest,
        )
    )
    print(result.manifest.snapshot_id)
    if not result.inventory.ok:
        print(
            f"inventory blocked with {len(result.inventory.issues)} issue(s)",
            file=sys.stderr,
        )
        return 2
    return 0


def _inventory(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(f"inventory output already exists: {args.output}")
    _, rows = load_extracted_rows(args.extraction_dir)
    report = inventory_rows(rows)
    write_inventory(report, args.output)
    return 0 if report.ok else 2


def _run(args: argparse.Namespace) -> int:
    from .pipeline import run_backfill

    result = run_backfill(
        source_url=args.source_url,
        target_url=args.target_url,
        dump_path=args.dump,
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        workers=args.workers,
        shuffle_seed=args.shuffle_seed,
    )
    print(result.seal.snapshot_id)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze-manifest":
            return _freeze_manifest(args)
        if args.command == "extract":
            return _extract(args)
        if args.command == "inventory":
            return _inventory(args)
        if args.command == "run":
            return _run(args)
    except (FileExistsError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
