from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib

from .constants import EXPECTED_SOURCE_RECEIPTS, EXPECTED_SOURCE_TABLE_COUNTS
from .reversal_links import ReversalResolutionError, resolve_reversal_links
from .normalize import (
    HistoricalAssetScale,
    normalize_explicit_amount,
    normalize_legacy_signed_amount,
)


Row = Mapping[str, object]


@dataclass(frozen=True, order=True, slots=True)
class InventoryIssue:
    code: str
    source_table: str
    source_ref: str
    relation: str


@dataclass(frozen=True, order=True, slots=True)
class InventoryResolution:
    code: str
    source_table: str
    source_ref: str
    relation: str


@dataclass(frozen=True, slots=True)
class InventoryReport:
    counts: tuple[tuple[str, int], ...]
    issues: tuple[InventoryIssue, ...]
    resolutions: tuple[InventoryResolution, ...]
    reversal_relation_count: int

    @property
    def ok(self) -> bool:
        return not self.issues


_IDENTITY_FIELDS = {
    "ledger_books": "book_id",
    "assets": "asset_code",
    "accounts": "account_id",
    "categories": "category_id",
    "category_versions": "category_version_id",
    "transactions": "transaction_id",
    "postings": "id",
    "transaction_lines": "line_id",
    "classification_events": "classification_event_id",
    "investment_events": "event_id",
    "investment_valuations": "valuation_id",
    "counterparties": "counterparty_id",
}


def _ref(table: str, row: Row) -> str:
    field = _IDENTITY_FIELDS.get(table)
    raw = "" if field is None else str(row.get(field, ""))
    return hashlib.sha256(f"{table}\x00{raw}".encode()).hexdigest()[:16]


def _issue(
    issues: list[InventoryIssue], code: str, table: str, row: Row, relation: str
) -> None:
    issues.append(InventoryIssue(code, table, _ref(table, row), relation))


def _index(
    rows: Sequence[Row], field: str, table: str, issues: list[InventoryIssue]
) -> dict[str, Row]:
    result: dict[str, Row] = {}
    for row in rows:
        value = row.get(field)
        if value is None or not str(value).strip():
            _issue(issues, "missing_identity", table, row, field)
            continue
        key = str(value)
        if key in result:
            _issue(issues, "duplicate_identity", table, row, field)
            continue
        result[key] = row
    return result


def _book(row: Row | None) -> str | None:
    if row is None or row.get("book_id") is None:
        return None
    return str(row["book_id"])


def _finite_nonzero(value: object) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed != 0


def _valid_timestamp(value: object, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def inventory_rows(
    rows_by_table: Mapping[str, Sequence[Row]], *, attachments_count: int | None = None
) -> InventoryReport:
    rows = {table: tuple(values) for table, values in rows_by_table.items()}
    issues: list[InventoryIssue] = []
    indexes = {
        table: _index(rows.get(table, ()), field, table, issues)
        for table, field in _IDENTITY_FIELDS.items()
    }
    books = indexes["ledger_books"]
    assets = set(indexes["assets"])
    accounts = indexes["accounts"]
    transactions = indexes["transactions"]
    categories = indexes["categories"]
    versions = indexes["category_versions"]
    counterparties = indexes["counterparties"]

    def require_book(table: str, row: Row) -> None:
        if str(row.get("book_id")) not in books:
            _issue(issues, "orphan_reference", table, row, "book_id")

    def require_asset(table: str, row: Row, field: str) -> None:
        if row.get(field) is not None and str(row[field]) not in assets:
            _issue(issues, "unknown_asset", table, row, field)

    for row in rows.get("ledger_books", ()):
        require_asset("ledger_books", row, "base_currency")
    asset_scales: dict[str, int] = {}
    for row in rows.get("assets", ()):
        try:
            policy = HistoricalAssetScale.for_source(
                asset_code=str(row["asset_code"]),
                source_scale=row["scale"],  # type: ignore[arg-type]
                source_display_scale=row["display_scale"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            _issue(issues, "invalid_scale", "assets", row, "scale")
        else:
            asset_scales[str(row["asset_code"])] = policy.ledger_scale
    for table in ("accounts", "categories", "category_versions", "transactions", "counterparties"):
        for row in rows.get(table, ()):
            require_book(table, row)
    for row in rows.get("accounts", ()):
        require_asset("accounts", row, "currency")
    for row in rows.get("transactions", ()):
        if not _valid_timestamp(row.get("occurred_at")):
            _issue(issues, "invalid_timestamp", "transactions", row, "occurred_at")

    for row in rows.get("categories", ()):
        parent_id = row.get("parent_id")
        if parent_id is not None:
            parent = categories.get(str(parent_id))
            if parent is None:
                _issue(issues, "orphan_reference", "categories", row, "parent_id")
            elif _book(parent) != _book(row):
                _issue(issues, "cross_book_reference", "categories", row, "parent_id")

    for row in rows.get("category_versions", ()):
        category = categories.get(str(row.get("category_id")))
        if category is None or _book(category) != _book(row):
            _issue(issues, "invalid_category_version", "category_versions", row, "category_id")
        parent_id = row.get("parent_id")
        if parent_id is not None:
            parent = categories.get(str(parent_id))
            if parent is None:
                _issue(issues, "orphan_reference", "category_versions", row, "parent_id")
            elif _book(parent) != _book(row):
                _issue(issues, "cross_book_reference", "category_versions", row, "parent_id")
        if not _valid_timestamp(row.get("valid_from")) or not _valid_timestamp(
            row.get("valid_to"), nullable=True
        ):
            _issue(issues, "invalid_timestamp", "category_versions", row, "validity")

    positions: set[tuple[str, str]] = set()
    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    posting_facts: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for row in rows.get("postings", ()):
        transaction_id = str(row.get("transaction_id"))
        transaction = transactions.get(transaction_id)
        account = accounts.get(str(row.get("account_id")))
        if transaction is None:
            _issue(issues, "orphan_reference", "postings", row, "transaction_id")
        if account is None:
            _issue(issues, "orphan_reference", "postings", row, "account_id")
        if transaction is not None and account is not None and _book(transaction) != _book(account):
            _issue(issues, "cross_book_reference", "postings", row, "account_id")
        row_book = _book(row)
        if transaction is not None and row_book != _book(transaction):
            _issue(issues, "cross_book_reference", "postings", row, "book_id")
        if account is not None and row_book != _book(account):
            _issue(issues, "cross_book_reference", "postings", row, "book_id")
        require_asset("postings", row, "currency")
        asset_code = str(row.get("currency"))
        if account is not None and str(account.get("currency")) != asset_code:
            _issue(issues, "asset_mismatch", "postings", row, "currency")
        semantics = row.get("amount_semantics")
        try:
            if semantics in {None, "legacy_signed"}:
                normalized = normalize_legacy_signed_amount(
                    str(row.get("amount")), ledger_scale=asset_scales[asset_code]
                )
            elif semantics == "debit_credit":
                normalized = normalize_explicit_amount(
                    str(row.get("amount")),
                    side=str(row.get("side")),
                    ledger_scale=asset_scales[asset_code],
                )
            else:
                _issue(
                    issues,
                    "invalid_amount_semantics",
                    "postings",
                    row,
                    "amount_semantics",
                )
                normalized = None
        except (KeyError, TypeError, ValueError):
            _issue(issues, "invalid_amount", "postings", row, "amount")
            normalized = None
        if normalized is not None:
            posting_facts[transaction_id].append(
                (asset_code, normalized.side, normalized.units)
            )
        if type(row.get("position")) is not int or row["position"] < 0:
            _issue(issues, "invalid_position", "postings", row, "position")
        position = (transaction_id, str(row.get("position")))
        if position in positions:
            _issue(issues, "duplicate_position", "postings", row, "transaction_id+position")
        positions.add(position)
        postings_by_transaction[transaction_id].append(row)

    for transaction_id, transaction in transactions.items():
        postings = postings_by_transaction.get(transaction_id, [])
        if len(postings) < 2:
            _issue(issues, "insufficient_postings", "transactions", transaction, "postings")
            continue
        balance: Counter[tuple[str, str]] = Counter()
        valid = len(posting_facts.get(transaction_id, ())) == len(postings)
        for asset, side, units in posting_facts.get(transaction_id, ()):
            balance[(asset, side)] += units
        for asset in {asset for asset, _ in balance}:
            if balance[(asset, "debit")] != balance[(asset, "credit")]:
                valid = False
        if not valid:
            _issue(issues, "unbalanced_transaction", "transactions", transaction, "postings")

    for row in rows.get("transaction_lines", ()):
        transaction = transactions.get(str(row.get("transaction_id")))
        category = None if row.get("category_id") is None else categories.get(str(row["category_id"]))
        version = None if row.get("category_version_id") is None else versions.get(str(row["category_version_id"]))
        counterparty = None if row.get("counterparty_id") is None else counterparties.get(str(row["counterparty_id"]))
        if transaction is None:
            _issue(issues, "orphan_reference", "transaction_lines", row, "transaction_id")
        if row.get("category_id") is not None and category is None:
            _issue(issues, "orphan_reference", "transaction_lines", row, "category_id")
        if row.get("category_version_id") is not None and (
            version is None
            or category is None
            or str(version.get("category_id")) != str(category.get("category_id"))
        ):
            _issue(issues, "invalid_category_version", "transaction_lines", row, "category_version_id")
        if row.get("counterparty_id") is not None and counterparty is None:
            _issue(issues, "missing_counterparty", "transaction_lines", row, "counterparty_id")
        for related in (category, version, counterparty):
            if transaction is not None and related is not None and _book(transaction) != _book(related):
                _issue(issues, "cross_book_reference", "transaction_lines", row, "book relation")
        if transaction is not None and _book(row) != _book(transaction):
            _issue(issues, "cross_book_reference", "transaction_lines", row, "book_id")
        for related in (category, version, counterparty):
            if related is not None and _book(row) != _book(related):
                _issue(issues, "cross_book_reference", "transaction_lines", row, "book_id")
        require_asset("transaction_lines", row, "currency")
        asset_code = str(row.get("currency"))
        try:
            normalize_legacy_signed_amount(
                str(row.get("amount")), ledger_scale=asset_scales[asset_code]
            )
        except (KeyError, TypeError, ValueError):
            _issue(issues, "invalid_amount", "transaction_lines", row, "amount")

    line_positions: set[tuple[str, str]] = set()
    for row in rows.get("transaction_lines", ()):
        if type(row.get("position")) is not int or row["position"] < 0:
            _issue(issues, "invalid_position", "transaction_lines", row, "position")
        key = (str(row.get("transaction_id")), str(row.get("position")))
        if key in line_positions:
            _issue(issues, "duplicate_position", "transaction_lines", row, "transaction_id+position")
        line_positions.add(key)

    for table in ("classification_events", "investment_events", "investment_valuations"):
        for row in rows.get(table, ()):
            require_book(table, row)
    for row in rows.get("classification_events", ()):
        if not _valid_timestamp(row.get("created_at")):
            _issue(issues, "invalid_timestamp", "classification_events", row, "created_at")
        for field in ("source_category_id", "target_category_id"):
            if row.get(field) is not None:
                category = categories.get(str(row[field]))
                if category is None:
                    _issue(issues, "orphan_reference", "classification_events", row, field)
                elif _book(category) != _book(row):
                    _issue(issues, "cross_book_reference", "classification_events", row, field)
    for table in ("investment_events", "investment_valuations"):
        for row in rows.get(table, ()):
            account = accounts.get(str(row.get("account_id")))
            if account is None:
                _issue(issues, "orphan_reference", table, row, "account_id")
            elif _book(account) != _book(row):
                _issue(issues, "cross_book_reference", table, row, "account_id")
            require_asset(table, row, "currency")
            if account is not None and str(account.get("currency")) != str(row.get("currency")):
                _issue(issues, "asset_mismatch", table, row, "currency")
    for row in rows.get("investment_events", ()):
        if not _valid_timestamp(row.get("occurred_at")):
            _issue(issues, "invalid_timestamp", "investment_events", row, "occurred_at")
        transaction_id = row.get("transaction_id")
        if transaction_id is not None:
            transaction = transactions.get(str(transaction_id))
            if transaction is None:
                _issue(issues, "orphan_reference", "investment_events", row, "transaction_id")
            elif _book(transaction) != _book(row):
                _issue(issues, "cross_book_reference", "investment_events", row, "transaction_id")
        asset_code = str(row.get("currency"))
        try:
            normalize_legacy_signed_amount(
                str(row.get("amount")), ledger_scale=asset_scales[asset_code]
            )
        except (KeyError, TypeError, ValueError):
            _issue(issues, "invalid_amount", "investment_events", row, "amount")
        for field in ("units", "nav"):
            if row.get(field) is not None and not _finite_nonzero(row[field]):
                _issue(issues, "invalid_amount", "investment_events", row, field)
    for row in rows.get("investment_valuations", ()):
        if not _valid_timestamp(row.get("observed_at")):
            _issue(issues, "invalid_timestamp", "investment_valuations", row, "observed_at")
        asset_code = str(row.get("currency"))
        try:
            normalize_legacy_signed_amount(
                str(row.get("value")), ledger_scale=asset_scales[asset_code]
            )
        except (KeyError, TypeError, ValueError):
            _issue(issues, "invalid_amount", "investment_valuations", row, "value")

    if attachments_count is None:
        issues.append(
            InventoryIssue(
                "missing_attachment_proof",
                "attachments",
                "catalog",
                "attachment absence must be proved in the source snapshot",
            )
        )
        proven_attachment_count = -1
    elif type(attachments_count) is not int or attachments_count != 0:
        issues.append(
            InventoryIssue(
                "unsupported_attachment",
                "attachments",
                "catalog",
                "attachments must be zero",
            )
        )
        proven_attachment_count = attachments_count if type(attachments_count) is int else -1
    else:
        proven_attachment_count = 0

    resolutions: tuple[InventoryResolution, ...] = ()
    relation_count = 0
    try:
        reversal = resolve_reversal_links(
            rows.get("transactions", ()), rows.get("postings", ())
        )
        relation_count = len(reversal.links)
        resolutions = tuple(
            InventoryResolution(
                "inferred_reversal_link",
                "transactions",
                hashlib.sha256(item.reversal_transaction_id.encode()).hexdigest()[:16],
                "exact inverse source pointer",
            )
            for item in reversal.inferred
        )
    except ReversalResolutionError as error:
        issues.append(
            InventoryIssue(
                f"reversal_{error.code}",
                "transactions",
                "graph",
                "reversal graph",
            )
        )

    counts = dict((table, len(values)) for table, values in rows.items())
    counts["attachments"] = proven_attachment_count
    return InventoryReport(
        counts=tuple(sorted(counts.items())),
        issues=tuple(sorted(set(issues))),
        resolutions=tuple(sorted(resolutions)),
        reversal_relation_count=relation_count,
    )


def validate_fixed_inventory(report: InventoryReport) -> None:
    counts = dict(report.counts)
    expected = {**EXPECTED_SOURCE_TABLE_COUNTS, "attachments": 0}
    if (
        not report.ok
        or counts != expected
        or sum(counts[table] for table in EXPECTED_SOURCE_TABLE_COUNTS)
        != EXPECTED_SOURCE_RECEIPTS
        or report.reversal_relation_count != 5
        or len(report.resolutions) != 1
    ):
        raise ValueError("fixed source inventory contract is blocked")


__all__ = [
    "InventoryIssue",
    "InventoryReport",
    "InventoryResolution",
    "inventory_rows",
    "validate_fixed_inventory",
]
