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
    "max": 1.685,
    "median": 1.362,
    "min": 1.235,
    "p95": 1.676
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 416.312,
    "median": 359.546,
    "min": 350.854,
    "p95": 403.79
  },
  "seed_transactions": 500,
  "speedup_median": 263.98,
  "speedup_p95": 240.92
}
```

Interpretation:

- Current command-scoped incremental write median: 1.362 ms.
- Legacy full-state baseline median: 359.546 ms.
- Median speedup: 263.98x.
- p95 speedup: 240.92x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
