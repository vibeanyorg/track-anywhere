from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

from .constants import (
    EXPECTED_CREDIT_CARD_REVIEW_SHA256,
    EXPECTED_FULL_MANIFEST_SHA256,
)
from .extract import (
    FrozenSourceRows,
    canonicalize_value,
    verify_frozen_source_rows,
)
from .manifest import (
    FrozenSourceManifest,
    assert_approved_manifest,
    canonical_json_bytes,
)
from .normalize import HistoricalAssetScale, normalize_legacy_signed_amount
from .reversal_links import resolve_reversal_links


Row = Mapping[str, object]
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = {
    "schema_version",
    "snapshot_id",
    "source_manifest_sha256",
    "reviewer",
    "reviewed_at",
    "transactions",
    "accounts",
    "expected_card_balances",
    "content_sha256",
}


@dataclass(frozen=True, slots=True)
class CreditCardPostingDecision:
    source_posting_id: str = field(repr=False)
    target_account_id: str = field(repr=False)
    target_side: str


@dataclass(frozen=True, slots=True)
class CreditCardTransactionDecision:
    book_id: str = field(repr=False)
    source_transaction_id: str = field(repr=False)
    source_transaction_sha256: str
    source_postings_sha256: str
    postings: tuple[CreditCardPostingDecision, ...] = field(repr=False)
    post_import_action: str


@dataclass(frozen=True, slots=True)
class CreditCardAccountDecision:
    book_id: str = field(repr=False)
    source_account_id: str = field(repr=False)
    source_account_sha256: str
    action: str


@dataclass(frozen=True, slots=True)
class ExpectedCreditCardBalance:
    book_id: str = field(repr=False)
    source_account_id: str = field(repr=False)
    asset_code: str
    natural_units: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class CreditCardSemanticReview:
    snapshot_id: str
    source_manifest_sha256: str
    reviewer: str = field(repr=False)
    reviewed_at: datetime
    content_sha256: str
    transactions: tuple[CreditCardTransactionDecision, ...] = field(repr=False)
    accounts: tuple[CreditCardAccountDecision, ...] = field(repr=False)
    expected_balances: tuple[ExpectedCreditCardBalance, ...] = field(repr=False)
    card_source_posting_count: int
    reviewed_at_text: str = field(default="", repr=False)

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)

    @property
    def reviewed_posting_count(self) -> int:
        return sum(len(transaction.postings) for transaction in self.transactions)

    @property
    def exact_reversal_action_count(self) -> int:
        return sum(
            transaction.post_import_action == "exact_reversal"
            for transaction in self.transactions
        )

    @property
    def account_action_count(self) -> int:
        return len(self.accounts)

    @property
    def expected_balance_count(self) -> int:
        return len(self.expected_balances)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("credit-card review contains a duplicate JSON key")
        result[key] = value
    return result


def _read_strict_json(path: Path) -> object:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("credit-card review contains a non-finite number")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("credit-card review must be readable strict JSON") from error


def review_content_payload(raw: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in raw.items() if key != "content_sha256"}


def calculated_review_sha256(raw: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(review_content_payload(raw))).hexdigest()


def _review_as_raw(review: CreditCardSemanticReview) -> dict[str, object]:
    try:
        if (
            type(review) is not CreditCardSemanticReview
            or any(
                type(item) is not CreditCardTransactionDecision
                for item in review.transactions
            )
            or any(
                type(posting) is not CreditCardPostingDecision
                for item in review.transactions
                for posting in item.postings
            )
            or any(
                type(item) is not CreditCardAccountDecision for item in review.accounts
            )
            or any(
                type(item) is not ExpectedCreditCardBalance
                for item in review.expected_balances
            )
            or not review.reviewed_at_text
            or _timestamp(review.reviewed_at_text) != review.reviewed_at
        ):
            raise ValueError
        return {
            "schema_version": 1,
            "snapshot_id": review.snapshot_id,
            "source_manifest_sha256": review.source_manifest_sha256,
            "reviewer": review.reviewer,
            "reviewed_at": review.reviewed_at_text,
            "transactions": [
                {
                    "book_id": transaction.book_id,
                    "source_transaction_id": transaction.source_transaction_id,
                    "source_transaction_sha256": transaction.source_transaction_sha256,
                    "source_postings_sha256": transaction.source_postings_sha256,
                    "postings": [
                        {
                            "source_posting_id": posting.source_posting_id,
                            "target_account_id": posting.target_account_id,
                            "target_side": posting.target_side,
                        }
                        for posting in transaction.postings
                    ],
                    "post_import_action": transaction.post_import_action,
                }
                for transaction in review.transactions
            ],
            "accounts": [
                {
                    "book_id": account.book_id,
                    "source_account_id": account.source_account_id,
                    "source_account_sha256": account.source_account_sha256,
                    "action": account.action,
                }
                for account in review.accounts
            ],
            "expected_card_balances": [
                {
                    "book_id": balance.book_id,
                    "source_account_id": balance.source_account_id,
                    "asset_code": balance.asset_code,
                    "natural_units": str(balance.natural_units),
                }
                for balance in review.expected_balances
            ],
            "content_sha256": review.content_sha256,
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValueError("credit-card review object is invalid") from None


def _nonblank(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"credit-card review {field_name} must be nonblank")
    return value


def canonical_source_identity(value: object) -> str:
    """Return the exact review identity for a text or integer source key."""

    if type(value) is str and value.strip():
        return value
    if type(value) is int and value >= 0:
        return str(value)
    raise ValueError("credit-card source identity is invalid")


def _sha256(value: object, *, field_name: str) -> str:
    if type(value) is not str or _LOWER_SHA256.fullmatch(value) is None:
        raise ValueError(f"credit-card review {field_name} must be lowercase SHA-256")
    return value


def _timestamp(value: object) -> datetime:
    raw = _nonblank(value, field_name="reviewed_at")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("credit-card review reviewed_at must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("credit-card review reviewed_at must include an offset")
    return parsed.astimezone(UTC)


def _source_int(value: object, *, field_name: str) -> int:
    if type(value) is int:
        return value
    raise ValueError(f"credit-card source {field_name} must be an integer")


def _parse_transactions(value: object) -> tuple[CreditCardTransactionDecision, ...]:
    if type(value) is not list:
        raise ValueError("credit-card review transactions must be an array")
    result: list[CreditCardTransactionDecision] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "book_id",
            "source_transaction_id",
            "source_transaction_sha256",
            "source_postings_sha256",
            "postings",
            "post_import_action",
        }:
            raise ValueError("credit-card transaction decision shape is invalid")
        action = _nonblank(item["post_import_action"], field_name="post_import_action")
        if action not in {"none", "exact_reversal"}:
            raise ValueError("credit-card post-import action is unsupported")
        raw_postings = item["postings"]
        if type(raw_postings) is not list or not raw_postings:
            raise ValueError("credit-card transaction postings must be nonempty")
        postings: list[CreditCardPostingDecision] = []
        for raw_posting in raw_postings:
            if type(raw_posting) is not dict or set(raw_posting) != {
                "source_posting_id",
                "target_account_id",
                "target_side",
            }:
                raise ValueError("credit-card posting decision shape is invalid")
            side = _nonblank(raw_posting["target_side"], field_name="target_side")
            if side not in {"debit", "credit"}:
                raise ValueError("credit-card posting target side is invalid")
            postings.append(
                CreditCardPostingDecision(
                    source_posting_id=_nonblank(
                        raw_posting["source_posting_id"], field_name="source_posting_id"
                    ),
                    target_account_id=_nonblank(
                        raw_posting["target_account_id"], field_name="target_account_id"
                    ),
                    target_side=side,
                )
            )
        result.append(
            CreditCardTransactionDecision(
                book_id=_nonblank(item["book_id"], field_name="book_id"),
                source_transaction_id=_nonblank(
                    item["source_transaction_id"], field_name="source_transaction_id"
                ),
                source_transaction_sha256=_sha256(
                    item["source_transaction_sha256"],
                    field_name="source_transaction_sha256",
                ),
                source_postings_sha256=_sha256(
                    item["source_postings_sha256"],
                    field_name="source_postings_sha256",
                ),
                postings=tuple(postings),
                post_import_action=action,
            )
        )
    return tuple(result)


def _parse_accounts(value: object) -> tuple[CreditCardAccountDecision, ...]:
    if type(value) is not list:
        raise ValueError("credit-card review accounts must be an array")
    result: list[CreditCardAccountDecision] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "book_id",
            "source_account_id",
            "source_account_sha256",
            "action",
        }:
            raise ValueError("credit-card account decision shape is invalid")
        if item["action"] != "close_historical_alias":
            raise ValueError("credit-card account action is unsupported")
        result.append(
            CreditCardAccountDecision(
                book_id=_nonblank(item["book_id"], field_name="book_id"),
                source_account_id=_nonblank(
                    item["source_account_id"], field_name="source_account_id"
                ),
                source_account_sha256=_sha256(
                    item["source_account_sha256"], field_name="source_account_sha256"
                ),
                action="close_historical_alias",
            )
        )
    return tuple(result)


def _parse_expected_balances(value: object) -> tuple[ExpectedCreditCardBalance, ...]:
    if type(value) is not list or not value:
        raise ValueError("expected credit-card balances must be a nonempty array")
    result: list[ExpectedCreditCardBalance] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "book_id",
            "source_account_id",
            "asset_code",
            "natural_units",
        }:
            raise ValueError("expected credit-card balance shape is invalid")
        raw_units = item["natural_units"]
        if type(raw_units) is not str or not re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)", raw_units
        ):
            raise ValueError("expected credit-card natural units must be canonical")
        units = int(raw_units)
        if str(units) != raw_units or len(raw_units.lstrip("-")) > 48:
            raise ValueError("expected credit-card natural units must be canonical")
        result.append(
            ExpectedCreditCardBalance(
                book_id=_nonblank(item["book_id"], field_name="book_id"),
                source_account_id=_nonblank(
                    item["source_account_id"], field_name="source_account_id"
                ),
                asset_code=_nonblank(item["asset_code"], field_name="asset_code"),
                natural_units=units,
            )
        )
    return tuple(result)


def _canonical_source_row(row: Row) -> dict[str, object]:
    if any(type(key) is not str for key in row):
        raise ValueError("credit-card source row columns are invalid")
    return {key: canonicalize_value(row[key]) for key in sorted(row)}


def source_row_sha256(row: Row) -> str:
    return hashlib.sha256(canonical_json_bytes(_canonical_source_row(row))).hexdigest()


def source_postings_sha256(rows: Sequence[Row]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (
            _source_int(row.get("position"), field_name="position"),
            canonical_source_identity(row.get("id")),
        ),
    )
    return hashlib.sha256(
        canonical_json_bytes([_canonical_source_row(row) for row in ordered])
    ).hexdigest()


def _card_accounts(
    rows_by_table: Mapping[str, Sequence[Row]],
) -> dict[tuple[str, str], Row]:
    return {
        (str(row.get("book_id")), str(row.get("account_id"))): row
        for row in rows_by_table.get("accounts", ())
        if row.get("type") == "liability"
        and row.get("subtype") in {"credit_card", "legacy_credit_card"}
    }


def _validate_against_source(
    review: CreditCardSemanticReview,
    rows_by_table: Mapping[str, Sequence[Row]],
) -> int:
    source_accounts = {
        (str(row.get("book_id")), str(row.get("account_id"))): row
        for row in rows_by_table.get("accounts", ())
    }
    card_accounts = _card_accounts(rows_by_table)
    transactions = {
        (str(row.get("book_id")), str(row.get("transaction_id"))): row
        for row in rows_by_table.get("transactions", ())
    }
    transaction_books = {
        str(row.get("transaction_id")): str(row.get("book_id"))
        for row in rows_by_table.get("transactions", ())
    }
    postings_by_transaction: dict[tuple[str, str], list[Row]] = defaultdict(list)
    source_card_postings = 0
    for row in rows_by_table.get("postings", ()):
        transaction_id = str(row.get("transaction_id"))
        book_id = transaction_books.get(transaction_id)
        if book_id is None:
            raise ValueError("credit-card source contains an orphan posting")
        postings_by_transaction[(book_id, transaction_id)].append(row)
        if (book_id, str(row.get("account_id"))) in card_accounts:
            source_card_postings += 1
    scope = {
        key
        for key, postings in postings_by_transaction.items()
        if any(
            (key[0], str(posting.get("account_id"))) in card_accounts
            for posting in postings
        )
    }
    transaction_index = {
        (item.book_id, item.source_transaction_id): item for item in review.transactions
    }
    if (
        len(transaction_index) != len(review.transactions)
        or set(transaction_index) != scope
    ):
        raise ValueError("credit-card review transaction coverage mismatch")

    scales: dict[str, int] = {}
    for row in rows_by_table.get("assets", ()):
        asset_code = str(row.get("asset_code"))
        source_scale = _source_int(row.get("scale"), field_name="scale")
        source_display = _source_int(
            row.get("display_scale"), field_name="display_scale"
        )
        scales[asset_code] = HistoricalAssetScale.for_source(
            asset_code=asset_code,
            source_scale=source_scale,
            source_display_scale=source_display,
        ).ledger_scale

    expected_natural: Counter[tuple[str, str, str]] = Counter()
    resolved_postings: dict[tuple[str, str], list[tuple[str, str, str, int]]] = {}
    for key in sorted(scope):
        transaction = transactions.get(key)
        if transaction is None:
            raise ValueError("credit-card review source transaction is missing")
        source_postings = sorted(
            postings_by_transaction[key],
            key=lambda row: (
                _source_int(row.get("position"), field_name="position"),
                canonical_source_identity(row.get("id")),
            ),
        )
        decision = transaction_index[key]
        if decision.source_transaction_sha256 != source_row_sha256(transaction):
            raise ValueError("credit-card review transaction source hash mismatch")
        if decision.source_postings_sha256 != source_postings_sha256(source_postings):
            raise ValueError("credit-card review posting-set source hash mismatch")
        posting_index = {
            posting.source_posting_id: posting for posting in decision.postings
        }
        source_ids = {
            canonical_source_identity(row.get("id")) for row in source_postings
        }
        if (
            len(posting_index) != len(decision.postings)
            or set(posting_index) != source_ids
        ):
            raise ValueError("credit-card review posting coverage mismatch")

        balance: Counter[tuple[str, str]] = Counter()
        resolved: list[tuple[str, str, str, int]] = []
        for row in source_postings:
            posting = posting_index[canonical_source_identity(row.get("id"))]
            target_key = (key[0], posting.target_account_id)
            target_account = source_accounts.get(target_key)
            asset_code = str(row.get("currency"))
            if (
                target_account is None
                or str(target_account.get("currency")) != asset_code
            ):
                raise ValueError("credit-card review posting target is invalid")
            if asset_code not in scales:
                raise ValueError("credit-card review posting asset is unknown")
            units = normalize_legacy_signed_amount(
                str(row.get("amount")), ledger_scale=scales[asset_code]
            ).units
            balance[(asset_code, posting.target_side)] += units
            resolved.append(
                (posting.target_account_id, asset_code, posting.target_side, units)
            )
            if target_key in card_accounts and decision.post_import_action == "none":
                expected_natural[(key[0], posting.target_account_id, asset_code)] += (
                    units if posting.target_side == "credit" else -units
                )
        for asset_code in {asset for asset, _ in balance}:
            if balance[(asset_code, "debit")] != balance[(asset_code, "credit")]:
                raise ValueError("credit-card review target postings are unbalanced")
        resolved_postings[key] = resolved

    source_transactions_by_id = {
        str(row.get("transaction_id")): row
        for row in rows_by_table.get("transactions", ())
    }
    for reversal_id, original_id in resolve_reversal_links(
        rows_by_table.get("transactions", ()), rows_by_table.get("postings", ())
    ).links:
        reversal_row = source_transactions_by_id.get(reversal_id)
        original_row = source_transactions_by_id.get(original_id)
        if reversal_row is None or original_row is None:
            continue
        book_id = str(reversal_row.get("book_id"))
        reversal_key = (book_id, reversal_id)
        original_key = (book_id, original_id)
        if (
            reversal_key not in resolved_postings
            or original_key not in resolved_postings
        ):
            continue
        expected_inverse = sorted(
            (
                account,
                asset,
                "credit" if side == "debit" else "debit",
                units,
            )
            for account, asset, side, units in resolved_postings[original_key]
        )
        if sorted(resolved_postings[reversal_key]) != expected_inverse:
            raise ValueError("credit-card review reversal mapping is not exact")

    account_index = {
        (item.book_id, item.source_account_id): item for item in review.accounts
    }
    if len(account_index) != len(review.accounts):
        raise ValueError("credit-card review contains duplicate account decisions")
    for key, decision in account_index.items():
        source = card_accounts.get(key)
        if source is None or source.get("subtype") != "legacy_credit_card":
            raise ValueError("credit-card account action target is invalid")
        if decision.source_account_sha256 != source_row_sha256(source):
            raise ValueError("credit-card account source hash mismatch")
        if any(
            units
            for (book, account, _asset), units in expected_natural.items()
            if (book, account) == key
        ):
            raise ValueError("credit-card alias is not zero before close")

    expected_index = {
        (item.book_id, item.source_account_id, item.asset_code): item.natural_units
        for item in review.expected_balances
    }
    required_expected = {
        (book, account, str(row.get("currency")))
        for (book, account), row in card_accounts.items()
    }
    if (
        len(expected_index) != len(review.expected_balances)
        or set(expected_index) != required_expected
    ):
        raise ValueError("credit-card expected balance coverage mismatch")
    observed = {key: expected_natural.get(key, 0) for key in required_expected}
    if expected_index != observed:
        raise ValueError("credit-card expected balances disagree with decisions")
    return source_card_postings


def _parse_credit_card_review(
    raw: object,
    *,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Row]],
    expected_content_sha256: str,
    expected_summary: tuple[int, int, int, int, int, int],
) -> CreditCardSemanticReview:
    assert_approved_manifest(manifest)
    if type(raw) is not dict or set(raw) != _REQUIRED_FIELDS:
        raise ValueError("credit-card review fields do not match schema version 1")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("credit-card review schema version is unsupported")
    if raw["snapshot_id"] != manifest.snapshot_id:
        raise ValueError("credit-card review snapshot binding mismatch")
    if raw["source_manifest_sha256"] != EXPECTED_FULL_MANIFEST_SHA256:
        raise ValueError("credit-card review manifest binding mismatch")
    content_sha256 = _sha256(raw["content_sha256"], field_name="content_sha256")
    if (
        calculated_review_sha256(raw) != content_sha256
        or content_sha256 != expected_content_sha256
    ):
        raise ValueError("credit-card review content hash mismatch")
    transactions = _parse_transactions(raw["transactions"])
    accounts = _parse_accounts(raw["accounts"])
    expected_balances = _parse_expected_balances(raw["expected_card_balances"])
    reviewed_at_text = _nonblank(raw["reviewed_at"], field_name="reviewed_at")
    provisional = CreditCardSemanticReview(
        snapshot_id=manifest.snapshot_id,
        source_manifest_sha256=EXPECTED_FULL_MANIFEST_SHA256,
        reviewer=_nonblank(raw["reviewer"], field_name="reviewer"),
        reviewed_at=_timestamp(reviewed_at_text),
        content_sha256=content_sha256,
        transactions=transactions,
        accounts=accounts,
        expected_balances=expected_balances,
        card_source_posting_count=0,
        reviewed_at_text=reviewed_at_text,
    )
    card_source_posting_count = _validate_against_source(provisional, rows_by_table)
    review = CreditCardSemanticReview(
        snapshot_id=provisional.snapshot_id,
        source_manifest_sha256=provisional.source_manifest_sha256,
        reviewer=provisional.reviewer,
        reviewed_at=provisional.reviewed_at,
        content_sha256=provisional.content_sha256,
        transactions=provisional.transactions,
        accounts=provisional.accounts,
        expected_balances=provisional.expected_balances,
        card_source_posting_count=card_source_posting_count,
        reviewed_at_text=provisional.reviewed_at_text,
    )
    observed_summary = (
        review.transaction_count,
        review.reviewed_posting_count,
        review.card_source_posting_count,
        review.exact_reversal_action_count,
        review.account_action_count,
        review.expected_balance_count,
    )
    if observed_summary != expected_summary:
        raise ValueError("credit-card review fixed coverage summary mismatch")
    return review


def validate_credit_card_review_object(
    review: CreditCardSemanticReview,
    *,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Row]],
    expected_content_sha256: str,
    expected_summary: tuple[int, int, int, int, int, int],
) -> CreditCardSemanticReview:
    """Reparse a hand-built object through the complete hash/source contract."""

    try:
        reparsed = _parse_credit_card_review(
            _review_as_raw(review),
            manifest=manifest,
            rows_by_table=rows_by_table,
            expected_content_sha256=expected_content_sha256,
            expected_summary=expected_summary,
        )
        if reparsed != review:
            raise ValueError
        return reparsed
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("credit-card review object is invalid") from None


def parse_approved_credit_card_review(
    raw: object, *, source: FrozenSourceRows
) -> CreditCardSemanticReview:
    verify_frozen_source_rows(source)
    return _parse_credit_card_review(
        raw,
        manifest=source.manifest,
        rows_by_table={name: table.rows for name, table in source.tables.items()},
        expected_content_sha256=EXPECTED_CREDIT_CARD_REVIEW_SHA256,
        expected_summary=(22, 48, 23, 3, 1, 5),
    )


def validate_approved_credit_card_review_object(
    review: CreditCardSemanticReview, *, source: FrozenSourceRows
) -> CreditCardSemanticReview:
    verify_frozen_source_rows(source)
    return validate_credit_card_review_object(
        review,
        manifest=source.manifest,
        rows_by_table={name: table.rows for name, table in source.tables.items()},
        expected_content_sha256=EXPECTED_CREDIT_CARD_REVIEW_SHA256,
        expected_summary=(22, 48, 23, 3, 1, 5),
    )


def read_approved_credit_card_review(
    path: Path,
    *,
    source: FrozenSourceRows,
) -> CreditCardSemanticReview:
    return parse_approved_credit_card_review(
        _read_strict_json(path),
        source=source,
    )


__all__ = [
    "CreditCardAccountDecision",
    "CreditCardPostingDecision",
    "CreditCardSemanticReview",
    "CreditCardTransactionDecision",
    "ExpectedCreditCardBalance",
    "calculated_review_sha256",
    "canonical_source_identity",
    "parse_approved_credit_card_review",
    "read_approved_credit_card_review",
    "review_content_payload",
    "source_postings_sha256",
    "source_row_sha256",
    "validate_approved_credit_card_review_object",
    "validate_credit_card_review_object",
]
