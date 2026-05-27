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
    "max": 2.965,
    "median": 1.87,
    "min": 1.339,
    "p95": 2.552
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 421.622,
    "median": 390.278,
    "min": 371.931,
    "p95": 420.085
  },
  "seed_transactions": 500,
  "speedup_median": 208.7,
  "speedup_p95": 164.61
}
```

Interpretation:

- Current command-scoped incremental write median: 1.870 ms.
- Legacy full-state baseline median: 390.278 ms.
- Median speedup: 208.7x.
- p95 speedup: 164.61x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
