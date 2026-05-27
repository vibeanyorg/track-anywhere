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
    "max": 1.752,
    "median": 1.408,
    "min": 1.288,
    "p95": 1.607
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 263.388,
    "median": 205.04,
    "min": 199.672,
    "p95": 227.578
  },
  "seed_transactions": 500,
  "speedup_median": 145.62,
  "speedup_p95": 141.62
}
```

Interpretation:

- Current command-scoped incremental write median: 1.408 ms.
- Legacy full-state baseline median: 205.040 ms.
- Median speedup: 145.62x.
- p95 speedup: 141.62x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
