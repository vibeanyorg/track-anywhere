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
    "max": 2.313,
    "median": 1.873,
    "min": 1.521,
    "p95": 2.211
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 251.603,
    "median": 210.166,
    "min": 203.012,
    "p95": 231.719
  },
  "seed_transactions": 500,
  "speedup_median": 112.21,
  "speedup_p95": 104.8
}
```

Interpretation:

- Current command-scoped incremental write median: 1.873 ms.
- Legacy full-state baseline median: 210.166 ms.
- Median speedup: 112.21x.
- p95 speedup: 104.8x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
