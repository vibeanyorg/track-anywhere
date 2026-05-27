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
    "max": 3.025,
    "median": 2.079,
    "min": 1.605,
    "p95": 3.014
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 460.247,
    "median": 410.192,
    "min": 391.438,
    "p95": 447.751
  },
  "seed_transactions": 500,
  "speedup_median": 197.3,
  "speedup_p95": 148.56
}
```

Interpretation:

- Current command-scoped incremental write median: 2.079 ms.
- Legacy full-state baseline median: 410.192 ms.
- Median speedup: 197.3x.
- p95 speedup: 148.56x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
