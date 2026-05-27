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
    "max": 2.553,
    "median": 1.683,
    "min": 1.425,
    "p95": 2.529
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 268.89,
    "median": 205.282,
    "min": 198.759,
    "p95": 234.206
  },
  "seed_transactions": 500,
  "speedup_median": 121.97,
  "speedup_p95": 92.61
}
```

Interpretation:

- Current command-scoped incremental write median: 1.683 ms.
- Legacy full-state baseline median: 205.282 ms.
- Median speedup: 121.97x.
- p95 speedup: 92.61x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
