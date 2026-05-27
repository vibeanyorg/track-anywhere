#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend/app"))

from track_anywhere.ledger import Posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark incremental writes against legacy full-state persistence.")
    parser.add_argument("--seed-transactions", type=int, default=500)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.seed_transactions < 1:
        raise SystemExit("--seed-transactions must be positive")
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")

    with tempfile.TemporaryDirectory(prefix="track-anywhere-write-bench-") as tmp:
        tmpdir = Path(tmp)
        current = FinanceService(
            DeploymentSecurityConfig(),
            database_url=f"sqlite:///{tmpdir / 'current.sqlite3'}",
        )
        legacy = FinanceService(
            DeploymentSecurityConfig(),
            database_url=f"sqlite:///{tmpdir / 'legacy.sqlite3'}",
        )
        current_state = seed_service(current, args.seed_transactions, "current")
        legacy_state = seed_service(legacy, args.seed_transactions, "legacy")

        current_samples = benchmark_current_incremental(current, current_state, args.iterations)
        legacy_samples = benchmark_legacy_full_state(legacy, legacy_state, args.iterations)

    result = summarize(args.seed_transactions, args.iterations, current_samples, legacy_samples)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_report(result)


def seed_service(service: FinanceService, seed_transactions: int, prefix: str) -> dict[str, Any]:
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {
            "name": f"{prefix} Cash",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": str(seed_transactions + 1000),
        },
        idempotency_key=f"{prefix}-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": f"{prefix} Expense", "type": "expense", "currency": "CNY"},
        idempotency_key=f"{prefix}-expense",
    )
    for index in range(seed_transactions):
        service.record_transaction(
            token,
            {
                "amount": "1",
                "currency": "CNY",
                "from_account_id": cash.account_id,
                "to_account_id": expense.account_id,
                "purpose": f"{prefix} seed {index}",
            },
            idempotency_key=f"{prefix}-seed-{index}",
        )
    return {"token": token, "cash_id": cash.account_id, "expense_id": expense.account_id}


def benchmark_current_incremental(service: FinanceService, state: dict[str, Any], iterations: int) -> list[float]:
    samples: list[float] = []
    for index in range(iterations):
        started = time.perf_counter()
        service.record_transaction(
            state["token"],
            {
                "amount": "1",
                "currency": "CNY",
                "from_account_id": state["cash_id"],
                "to_account_id": state["expense_id"],
                "purpose": f"incremental benchmark {index}",
            },
            idempotency_key=f"incremental-benchmark-{index}",
        )
        samples.append(time.perf_counter() - started)
    return samples


def benchmark_legacy_full_state(service: FinanceService, state: dict[str, Any], iterations: int) -> list[float]:
    samples: list[float] = []
    hydrate_legacy_full_state_for_benchmark(service)
    for index in range(iterations):
        started = time.perf_counter()
        service.ledger.create_transaction(
            memo="",
            purpose=f"legacy full-state benchmark {index}",
            postings=[
                Posting(state["cash_id"], Decimal("-1"), "CNY"),
                Posting(state["expense_id"], Decimal("1"), "CNY"),
            ],
        )
        persist_legacy_full_state_for_benchmark(service)
        samples.append(time.perf_counter() - started)
    return samples


def hydrate_legacy_full_state_for_benchmark(service: FinanceService) -> None:
    service.ledger.accounts = {
        account.account_id: account
        for account in service.storage.list_accounts(book_id=None)
    }
    transactions = {}
    for book in service.books.books.values():
        for transaction in service.storage.list_all_confirmed_transactions(book_id=book.book_id):
            transactions[transaction.transaction_id] = transaction
    service.ledger.transactions = transactions


def persist_legacy_full_state_for_benchmark(service: FinanceService) -> None:
    storage = service.storage
    with storage.unit_of_work() as uow:
        uow.books.save(service.books.books.values(), service.books.members.values())
        uow.assets.save(service.assets.assets.values())
        uow.accounts.save(service.ledger.accounts.values())
        uow.ledger.save_transactions(service.ledger.transactions.values())
        uow.categories.save(service.categories.categories.values())
        uow.categories.save_history(
            aliases=service.categories.aliases.values(),
            versions=service.categories.versions.values(),
            events=service.categories.events.values(),
        )
        uow.credentials.save(service.credentials._credentials.values())
        uow.audit.save_events(service.audit.events)
        uow.idempotency.save_receipts(service.idempotency._receipts.values())


def summarize(
    seed_transactions: int,
    iterations: int,
    current_samples: list[float],
    legacy_samples: list[float],
) -> dict[str, Any]:
    current = sample_stats(current_samples)
    legacy = sample_stats(legacy_samples)
    return {
        "seed_transactions": seed_transactions,
        "iterations": iterations,
        "current_incremental_ms": current,
        "legacy_full_state_ms": legacy,
        "speedup_median": round(legacy["median"] / current["median"], 2),
        "speedup_p95": round(legacy["p95"] / current["p95"], 2),
    }


def sample_stats(samples: list[float]) -> dict[str, float]:
    milliseconds = [sample * 1000 for sample in samples]
    return {
        "min": round(min(milliseconds), 3),
        "median": round(statistics.median(milliseconds), 3),
        "p95": round(percentile(milliseconds, 95), 3),
        "max": round(max(milliseconds), 3),
    }


def percentile(values: list[float], percentile_value: int) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def print_report(result: dict[str, Any]) -> None:
    print("Track Anywhere write performance benchmark")
    print(f"seed_transactions={result['seed_transactions']} iterations={result['iterations']}")
    print("")
    print("path                         min_ms  median_ms  p95_ms  max_ms")
    print_row("current incremental", result["current_incremental_ms"])
    print_row("legacy full-state", result["legacy_full_state_ms"])
    print("")
    print(f"median_speedup={result['speedup_median']}x")
    print(f"p95_speedup={result['speedup_p95']}x")


def print_row(label: str, stats: dict[str, float]) -> None:
    print(f"{label:<28} {stats['min']:>7.3f} {stats['median']:>10.3f} {stats['p95']:>7.3f} {stats['max']:>7.3f}")


if __name__ == "__main__":
    main()
