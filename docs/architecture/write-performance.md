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
    "max": 6.641,
    "median": 2.511,
    "min": 1.767,
    "p95": 3.589
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 550.421,
    "median": 443.77,
    "min": 401.272,
    "p95": 532.044
  },
  "seed_transactions": 500,
  "speedup_median": 176.73,
  "speedup_p95": 148.24
}
```

Interpretation:

- Current command-scoped incremental write median: 2.511 ms.
- Legacy full-state baseline median: 443.770 ms.
- Median speedup: 176.73x.
- p95 speedup: 148.24x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
