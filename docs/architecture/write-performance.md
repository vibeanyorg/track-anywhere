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
    "max": 1.748,
    "median": 1.46,
    "min": 1.34,
    "p95": 1.584
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 412.763,
    "median": 361.15,
    "min": 357.232,
    "p95": 387.124
  },
  "seed_transactions": 500,
  "speedup_median": 247.36,
  "speedup_p95": 244.4
}
```

Interpretation:

- Current command-scoped incremental write median: 1.46 ms.
- Legacy full-state baseline median: 361.15 ms.
- Median speedup: 247.36x.
- p95 speedup: 244.4x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
