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
    "max": 2.827,
    "median": 1.601,
    "min": 1.356,
    "p95": 2.294
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 582.489,
    "median": 406.786,
    "min": 392.354,
    "p95": 459.873
  },
  "seed_transactions": 500,
  "speedup_median": 254.08,
  "speedup_p95": 200.47
}
```

Interpretation:

- Current command-scoped incremental write median: 1.601 ms.
- Legacy full-state baseline median: 406.786 ms.
- Median speedup: 254.08x.
- p95 speedup: 200.47x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
