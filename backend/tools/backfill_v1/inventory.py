from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Sequence


Row = Mapping[str, object]


@dataclass(frozen=True, order=True)
class InventoryIssue:
    code: str
    source_table: str
    source_primary_key: str
    relation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "relation": self.relation,
            "source_primary_key": self.source_primary_key,
            "source_table": self.source_table,
        }


@dataclass(frozen=True)
class InventoryReport:
    counts: tuple[tuple[str, int], ...]
    issues: tuple[InventoryIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
            "status": "PASS" if self.ok else "BLOCKED",
        }


_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "accounts": ("account_id",),
    "assets": ("asset_code",),
    "categories": ("category_id",),
    "category_versions": ("category_version_id",),
    "classification_events": ("classification_event_id",),
    "investment_events": ("event_id",),
    "investment_valuations": ("valuation_id",),
    "ledger_books": ("book_id",),
    "postings": ("id",),
    "transaction_lines": ("line_id",),
    "transactions": ("transaction_id",),
}


def _key(table: str, row: Row) -> str:
    values = [str(row.get(column, "")) for column in _PRIMARY_KEYS.get(table, ())]
    if any(values):
        return ":".join(values)
    return "sha256-unavailable"


def _identifier_index(rows: Iterable[Row], key: str) -> dict[str, Row]:
    return {str(row[key]): row for row in rows if row.get(key) is not None}


def _book(row: Row) -> str | None:
    value = row.get("book_id")
    return None if value is None else str(value)


def _issue(
    issues: list[InventoryIssue],
    code: str,
    table: str,
    row: Row,
    relation: str,
) -> None:
    issues.append(
        InventoryIssue(
            code=code,
            source_table=table,
            source_primary_key=_key(table, row),
            relation=relation,
        )
    )


def _check_reference(
    issues: list[InventoryIssue],
    *,
    table: str,
    row: Row,
    field: str,
    targets: Mapping[str, Row],
    target_label: str,
    nullable: bool = False,
) -> Row | None:
    value = row.get(field)
    if value is None and nullable:
        return None
    target = targets.get(str(value))
    if target is None:
        _issue(issues, "orphan_reference", table, row, f"{field}->{target_label}")
    return target


def _check_same_book(
    issues: list[InventoryIssue],
    *,
    table: str,
    row: Row,
    other: Row | None,
    relation: str,
    expected_book: str | None = None,
) -> None:
    if other is None:
        return
    left_book = expected_book if expected_book is not None else _book(row)
    right_book = _book(other)
    if left_book is not None and right_book is not None and left_book != right_book:
        _issue(issues, "cross_book_reference", table, row, relation)


def _check_asset(
    issues: list[InventoryIssue],
    *,
    table: str,
    row: Row,
    field: str,
    assets: set[str],
) -> None:
    value = row.get(field)
    if value is not None and str(value) not in assets:
        _issue(issues, "unknown_asset", table, row, field)


def _check_amount(
    issues: list[InventoryIssue],
    *,
    table: str,
    row: Row,
    field: str,
    nullable: bool = False,
) -> None:
    value = row.get(field)
    if value is None and nullable:
        return
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _issue(issues, "invalid_amount", table, row, field)
        return
    if not amount.is_finite() or amount == 0:
        _issue(issues, "invalid_amount", table, row, field)


def _check_duplicate_positions(
    issues: list[InventoryIssue], table: str, rows: Sequence[Row]
) -> None:
    seen: set[tuple[str, str]] = set()
    duplicate_reported: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("transaction_id")), str(row.get("position")))
        if key in seen and key not in duplicate_reported:
            _issue(
                issues,
                "duplicate_position",
                table,
                row,
                "transaction_id+position",
            )
            duplicate_reported.add(key)
        seen.add(key)


def _check_reversals(issues: list[InventoryIssue], transactions: Sequence[Row]) -> None:
    by_id = _identifier_index(transactions, "transaction_id")
    reverse_edges: dict[str, str] = {}
    target_counts: Counter[str] = Counter()
    for row in transactions:
        source = str(row.get("transaction_id"))
        raw_target = row.get("reverses_transaction_id")
        if raw_target is None:
            continue
        target = str(raw_target)
        reverse_edges[source] = target
        target_counts[target] += 1
        target_row = _check_reference(
            issues,
            table="transactions",
            row=row,
            field="reverses_transaction_id",
            targets=by_id,
            target_label="transactions.transaction_id",
        )
        _check_same_book(
            issues,
            table="transactions",
            row=row,
            other=target_row,
            relation="reversal transaction",
        )
        target_reversed_by = (
            None if target_row is None else target_row.get("reversed_by")
        )
        if target_row is not None and target_reversed_by not in {None, source}:
            _issue(
                issues,
                "reversal_inconsistent",
                "transactions",
                row,
                "target reversed_by points elsewhere",
            )

    for row in transactions:
        reversed_by = row.get("reversed_by")
        if reversed_by is None:
            continue
        reverse_row = _check_reference(
            issues,
            table="transactions",
            row=row,
            field="reversed_by",
            targets=by_id,
            target_label="transactions.transaction_id",
        )
        _check_same_book(
            issues,
            table="transactions",
            row=row,
            other=reverse_row,
            relation="reversed_by transaction",
        )
        if reverse_row is not None and str(
            reverse_row.get("reverses_transaction_id")
        ) != str(row.get("transaction_id")):
            _issue(
                issues,
                "reversal_inconsistent",
                "transactions",
                row,
                "reversed_by transaction does not reverse source",
            )

    for target, count in sorted(target_counts.items()):
        if count > 1:
            representative = next(
                row
                for row in transactions
                if str(row.get("reverses_transaction_id")) == target
            )
            _issue(
                issues,
                "reversal_multiplicity",
                "transactions",
                representative,
                f"reverses_transaction_id:{target}",
            )

    cycle_members: set[str] = set()
    for start in sorted(reverse_edges):
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in reverse_edges and current not in positions:
            positions[current] = len(path)
            path.append(current)
            current = reverse_edges[current]
        if current in positions:
            cycle_members.update(path[positions[current] :])
    for transaction_id in sorted(cycle_members):
        _issue(
            issues,
            "reversal_cycle",
            "transactions",
            by_id[transaction_id],
            "reverses_transaction_id cycle",
        )


def inventory_rows(rows_by_table: Mapping[str, Sequence[Row]]) -> InventoryReport:
    rows = {table: tuple(values) for table, values in rows_by_table.items()}
    issues: list[InventoryIssue] = []
    assets = {
        str(row["asset_code"])
        for row in rows.get("assets", ())
        if row.get("asset_code") is not None
    }
    books = _identifier_index(rows.get("ledger_books", ()), "book_id")
    accounts = _identifier_index(rows.get("accounts", ()), "account_id")
    transactions = _identifier_index(rows.get("transactions", ()), "transaction_id")
    categories = _identifier_index(rows.get("categories", ()), "category_id")
    category_versions = _identifier_index(
        rows.get("category_versions", ()), "category_version_id"
    )

    for row in rows.get("ledger_books", ()):
        _check_asset(
            issues,
            table="ledger_books",
            row=row,
            field="base_currency",
            assets=assets,
        )

    for row in rows.get("accounts", ()):
        _check_reference(
            issues,
            table="accounts",
            row=row,
            field="book_id",
            targets=books,
            target_label="ledger_books.book_id",
        )
        _check_asset(issues, table="accounts", row=row, field="currency", assets=assets)

    for table in ("transactions", "categories", "category_versions"):
        for row in rows.get(table, ()):
            _check_reference(
                issues,
                table=table,
                row=row,
                field="book_id",
                targets=books,
                target_label="ledger_books.book_id",
            )

    for row in rows.get("categories", ()):
        parent = _check_reference(
            issues,
            table="categories",
            row=row,
            field="parent_id",
            targets=categories,
            target_label="categories.category_id",
            nullable=True,
        )
        _check_same_book(
            issues,
            table="categories",
            row=row,
            other=parent,
            relation="parent category",
        )

    for row in rows.get("category_versions", ()):
        category = _check_reference(
            issues,
            table="category_versions",
            row=row,
            field="category_id",
            targets=categories,
            target_label="categories.category_id",
        )
        _check_same_book(
            issues,
            table="category_versions",
            row=row,
            other=category,
            relation="category version",
        )
        parent = _check_reference(
            issues,
            table="category_versions",
            row=row,
            field="parent_id",
            targets=categories,
            target_label="categories.category_id",
            nullable=True,
        )
        _check_same_book(
            issues,
            table="category_versions",
            row=row,
            other=parent,
            relation="category version parent",
        )

    for row in rows.get("postings", ()):
        transaction = _check_reference(
            issues,
            table="postings",
            row=row,
            field="transaction_id",
            targets=transactions,
            target_label="transactions.transaction_id",
        )
        account = _check_reference(
            issues,
            table="postings",
            row=row,
            field="account_id",
            targets=accounts,
            target_label="accounts.account_id",
        )
        expected_book = _book(transaction) if transaction is not None else _book(row)
        _check_same_book(
            issues,
            table="postings",
            row=row,
            other=account,
            expected_book=expected_book,
            relation="posting account",
        )
        if transaction is not None and _book(row) not in {None, _book(transaction)}:
            _issue(
                issues,
                "cross_book_reference",
                "postings",
                row,
                "posting transaction",
            )
        _check_asset(issues, table="postings", row=row, field="currency", assets=assets)
        if account is not None and str(account.get("currency")) != str(
            row.get("currency")
        ):
            _issue(
                issues,
                "asset_mismatch",
                "postings",
                row,
                "posting currency differs from account currency",
            )
        _check_amount(issues, table="postings", row=row, field="amount")

    for row in rows.get("transaction_lines", ()):
        transaction = _check_reference(
            issues,
            table="transaction_lines",
            row=row,
            field="transaction_id",
            targets=transactions,
            target_label="transactions.transaction_id",
        )
        category = _check_reference(
            issues,
            table="transaction_lines",
            row=row,
            field="category_id",
            targets=categories,
            target_label="categories.category_id",
            nullable=True,
        )
        category_version = _check_reference(
            issues,
            table="transaction_lines",
            row=row,
            field="category_version_id",
            targets=category_versions,
            target_label="category_versions.category_version_id",
            nullable=True,
        )
        for other, relation in (
            (transaction, "transaction line transaction"),
            (category, "transaction line category"),
            (category_version, "transaction line category version"),
        ):
            _check_same_book(
                issues,
                table="transaction_lines",
                row=row,
                other=other,
                relation=relation,
            )
        _check_asset(
            issues,
            table="transaction_lines",
            row=row,
            field="currency",
            assets=assets,
        )
        _check_amount(issues, table="transaction_lines", row=row, field="amount")

    for row in rows.get("classification_events", ()):
        _check_reference(
            issues,
            table="classification_events",
            row=row,
            field="book_id",
            targets=books,
            target_label="ledger_books.book_id",
        )
        for field in ("source_category_id", "target_category_id"):
            category = _check_reference(
                issues,
                table="classification_events",
                row=row,
                field=field,
                targets=categories,
                target_label="categories.category_id",
                nullable=True,
            )
            _check_same_book(
                issues,
                table="classification_events",
                row=row,
                other=category,
                relation=field,
            )

    for table in ("investment_events", "investment_valuations"):
        for row in rows.get(table, ()):
            _check_reference(
                issues,
                table=table,
                row=row,
                field="book_id",
                targets=books,
                target_label="ledger_books.book_id",
            )
            account = _check_reference(
                issues,
                table=table,
                row=row,
                field="account_id",
                targets=accounts,
                target_label="accounts.account_id",
            )
            _check_same_book(
                issues,
                table=table,
                row=row,
                other=account,
                relation="investment account",
            )
            _check_asset(issues, table=table, row=row, field="currency", assets=assets)
            if account is not None and str(account.get("currency")) != str(
                row.get("currency")
            ):
                _issue(
                    issues,
                    "asset_mismatch",
                    table,
                    row,
                    "investment currency differs from account currency",
                )
            if table == "investment_events":
                transaction = _check_reference(
                    issues,
                    table=table,
                    row=row,
                    field="transaction_id",
                    targets=transactions,
                    target_label="transactions.transaction_id",
                    nullable=True,
                )
                _check_same_book(
                    issues,
                    table=table,
                    row=row,
                    other=transaction,
                    relation="investment transaction",
                )
            for field in (
                ("amount", "units", "nav")
                if table == "investment_events"
                else ("value",)
            ):
                _check_amount(
                    issues,
                    table=table,
                    row=row,
                    field=field,
                    nullable=field in {"units", "nav"},
                )

    _check_duplicate_positions(issues, "postings", rows.get("postings", ()))
    _check_duplicate_positions(
        issues, "transaction_lines", rows.get("transaction_lines", ())
    )
    _check_reversals(issues, rows.get("transactions", ()))

    unique_issues = tuple(sorted(set(issues)))
    counts = tuple(sorted((table, len(values)) for table, values in rows.items()))
    return InventoryReport(counts=counts, issues=unique_issues)


def write_inventory(report: InventoryReport, path: Path) -> None:
    Path(path).write_text(
        json.dumps(
            report.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = [
    "InventoryIssue",
    "InventoryReport",
    "inventory_rows",
    "write_inventory",
]
