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
    "max": 3.886,
    "median": 1.916,
    "min": 1.433,
    "p95": 2.515
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 230.383,
    "median": 210.325,
    "min": 202.453,
    "p95": 227.271
  },
  "seed_transactions": 500,
  "speedup_median": 109.77,
  "speedup_p95": 90.37
}
```

Interpretation:

- Current command-scoped incremental write median: 1.916 ms.
- Legacy full-state baseline median: 210.325 ms.
- Median speedup: 109.77x.
- p95 speedup: 90.37x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
