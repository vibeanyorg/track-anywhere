# Write Performance Benchmark

Track Anywhere write paths are expected to persist only command-scoped changes.
The benchmark script compares the current incremental `record_transaction`
path with a benchmark-only simulation of the old full-state persistence model.

Run from the repository root:

```bash
uv run python scripts/benchmark-write-performance.py --seed-transactions 500 --iterations 20 --json
```

Latest local result on 2026-05-27:

```json
{
  "current_incremental_ms": {
    "max": 4.223,
    "median": 2.272,
    "min": 1.623,
    "p95": 3.299
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 498.575,
    "median": 413.254,
    "min": 398.066,
    "p95": 461.473
  },
  "seed_transactions": 500,
  "speedup_median": 181.89,
  "speedup_p95": 139.88
}
```

Interpretation:

- Current command-scoped incremental write median: 2.272 ms.
- Legacy full-state baseline median: 413.254 ms.
- Median speedup: 181.89x.
- p95 speedup: 139.88x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
