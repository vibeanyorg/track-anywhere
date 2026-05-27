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
    "max": 4.687,
    "median": 1.901,
    "min": 1.253,
    "p95": 3.615
  },
  "iterations": 20,
  "legacy_full_state_ms": {
    "max": 390.952,
    "median": 362.121,
    "min": 355.242,
    "p95": 385.836
  },
  "seed_transactions": 500,
  "speedup_median": 190.49,
  "speedup_p95": 106.73
}
```

Interpretation:

- Current command-scoped incremental write median: 1.901 ms.
- Legacy full-state baseline median: 362.121 ms.
- Median speedup: 190.49x.
- p95 speedup: 106.73x.

The legacy baseline is intentionally kept in the benchmark script rather than
production storage code, so full-state persistence cannot be called by API write
paths.
