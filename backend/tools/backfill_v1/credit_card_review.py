from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .extract import canonicalize_row
from .manifest import FrozenSourceManifest, canonical_json_bytes
from .normalize import decimal_to_units
from .reversal_links import resolve_reversal_links


_SCHEMA_VERSION = 1
_POST_IMPORT_ACTIONS = {"none", "exact_reversal"}
_ACCOUNT_ACTIONS = {"close_historical_alias"}
_SIDES = {"debit", "credit"}


@dataclass(frozen=True, slots=True)
class CreditCardPostingDecision:
    source_posting_id: str
    target_account_id: str
    target_side: str


@dataclass(frozen=True, slots=True)
class CreditCardTransactionDecision:
    book_id: str
    source_transaction_id: str
    source_transaction_sha256: str
    source_postings_sha256: str
    postings: tuple[CreditCardPostingDecision, ...]
    post_import_action: str


@dataclass(frozen=True, slots=True)
class CreditCardAccountDecision:
    book_id: str
    source_account_id: str
    source_account_sha256: str
    action: str


@dataclass(frozen=True, slots=True)
class ExpectedCreditCardBalance:
    book_id: str
    source_account_id: str
    asset_code: str
    natural_units: int


@dataclass(frozen=True, slots=True)
class CreditCardSemanticReview:
    snapshot_id: str
    source_manifest_sha256: str
    reviewer: str
    reviewed_at: datetime
    content_sha256: str
    transactions: tuple[CreditCardTransactionDecision, ...]
    accounts: tuple[CreditCardAccountDecision, ...]
    expected_balances: tuple[ExpectedCreditCardBalance, ...]

    @property
    def transaction_index(
        self,
    ) -> Mapping[tuple[str, str], CreditCardTransactionDecision]:
        return {
            (decision.book_id, decision.source_transaction_id): decision
            for decision in self.transactions
        }

    @property
    def account_index(
        self,
    ) -> Mapping[tuple[str, str], CreditCardAccountDecision]:
        return {
            (decision.book_id, decision.source_account_id): decision
            for decision in self.accounts
        }

    def posting_decision(
        self, *, book_id: object, transaction_id: object, posting_id: object
    ) -> CreditCardPostingDecision | None:
        transaction = self.transaction_index.get((str(book_id), str(transaction_id)))
        if transaction is None:
            return None
        return next(
            (
                decision
                for decision in transaction.postings
                if decision.source_posting_id == str(posting_id)
            ),
            None,
        )


def source_row_sha256(row: Mapping[str, object]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(canonicalize_row(row))
    ).hexdigest()


def source_postings_sha256(rows: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (_source_int(row["position"], "position"), str(row["id"])),
    )
    return hashlib.sha256(
        canonical_json_bytes([canonicalize_row(row) for row in ordered])
    ).hexdigest()


def credit_card_transaction_scope(
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> frozenset[tuple[str, str]]:
    accounts = {
        (str(row["book_id"]), str(row["account_id"]))
        for row in rows_by_table.get("accounts", ())
        if str(row.get("type")) == "liability"
        and str(row.get("subtype")) in {"credit_card", "legacy_credit_card"}
    }
    transactions = {
        str(row["transaction_id"]): str(row["book_id"])
        for row in rows_by_table.get("transactions", ())
    }
    return frozenset(
        (transactions[str(row["transaction_id"])], str(row["transaction_id"]))
        for row in rows_by_table.get("postings", ())
        if (
            transactions.get(str(row["transaction_id"])),
            str(row["account_id"]),
        )
        in accounts
    )


def review_content_payload(raw: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in raw.items()
        if key != "content_sha256"
    }


def calculated_review_sha256(raw: Mapping[str, object]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(review_content_payload(raw))
    ).hexdigest()


def build_credit_card_review_document(
    *,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    posting_decisions: Mapping[str, tuple[str, str]],
    neutralized_transaction_ids: frozenset[str],
    closed_account_ids: frozenset[str],
    expected_balances: Mapping[tuple[str, str, str], int],
    reviewer: str,
    reviewed_at: datetime,
) -> dict[str, object]:
    """Build a hash-bound document from explicit operator decisions.

    This helper deliberately has no sign-based default. Every posting in every
    card-touching transaction must be supplied by the caller.
    """

    scope = credit_card_transaction_scope(rows_by_table)
    transactions = {
        (str(row["book_id"]), str(row["transaction_id"])): row
        for row in rows_by_table.get("transactions", ())
    }
    transaction_book = {
        str(row["transaction_id"]): str(row["book_id"])
        for row in rows_by_table.get("transactions", ())
    }
    postings_by_transaction: dict[tuple[str, str], list[Mapping[str, object]]] = (
        defaultdict(list)
    )
    for row in rows_by_table.get("postings", ()):
        transaction_id = str(row["transaction_id"])
        postings_by_transaction[(transaction_book[transaction_id], transaction_id)].append(
            row
        )
    reviewed_posting_ids = {
        str(row["id"])
        for key in scope
        for row in postings_by_transaction[key]
    }
    if set(posting_decisions) != reviewed_posting_ids:
        raise ValueError("explicit posting decisions do not exactly cover card scope")
    if not neutralized_transaction_ids <= {transaction_id for _, transaction_id in scope}:
        raise ValueError("neutralization decision is outside card scope")

    accounts = {
        (str(row["book_id"]), str(row["account_id"])): row
        for row in rows_by_table.get("accounts", ())
    }
    closed_rows = [
        row
        for (_, account_id), row in accounts.items()
        if account_id in closed_account_ids
    ]
    if {str(row["account_id"]) for row in closed_rows} != set(closed_account_ids):
        raise ValueError("closed account decision references an unknown account")

    raw: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "snapshot_id": manifest.snapshot_id,
        "source_manifest_sha256": (
            manifest.content_sha256 or manifest.calculated_content_sha256()
        ),
        "reviewer": _nonblank(reviewer, "reviewer"),
        "reviewed_at": reviewed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "transactions": [
            {
                "book_id": book_id,
                "source_transaction_id": transaction_id,
                "source_transaction_sha256": source_row_sha256(
                    transactions[(book_id, transaction_id)]
                ),
                "source_postings_sha256": source_postings_sha256(
                    postings_by_transaction[(book_id, transaction_id)]
                ),
                "postings": [
                    {
                        "source_posting_id": str(row["id"]),
                        "target_account_id": posting_decisions[str(row["id"])][0],
                        "target_side": posting_decisions[str(row["id"])][1],
                    }
                    for row in sorted(
                        postings_by_transaction[(book_id, transaction_id)],
                        key=lambda item: (
                            _source_int(item["position"], "position"),
                            str(item["id"]),
                        ),
                    )
                ],
                "post_import_action": (
                    "exact_reversal"
                    if transaction_id in neutralized_transaction_ids
                    else "none"
                ),
            }
            for book_id, transaction_id in sorted(scope)
        ],
        "accounts": [
            {
                "book_id": str(row["book_id"]),
                "source_account_id": str(row["account_id"]),
                "source_account_sha256": source_row_sha256(row),
                "action": "close_historical_alias",
            }
            for row in sorted(
                closed_rows,
                key=lambda item: (str(item["book_id"]), str(item["account_id"])),
            )
        ],
        "expected_card_balances": [
            {
                "book_id": book_id,
                "source_account_id": account_id,
                "asset_code": asset_code,
                "natural_units": str(units),
            }
            for (book_id, account_id, asset_code), units in sorted(
                expected_balances.items()
            )
        ],
    }
    raw["content_sha256"] = calculated_review_sha256(raw)
    return raw


def calculate_reviewed_card_balances(
    *,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    posting_decisions: Mapping[str, tuple[str, str]],
    neutralized_transaction_ids: frozenset[str],
) -> dict[tuple[str, str, str], int]:
    """Reduce explicit reviewed sides/accounts into liability-natural units."""

    card_accounts = {
        (str(row["book_id"]), str(row["account_id"]), str(row["currency"]))
        for row in rows_by_table.get("accounts", ())
        if str(row.get("type")) == "liability"
        and str(row.get("subtype")) in {"credit_card", "legacy_credit_card"}
    }
    scales = {
        str(row["asset_code"]): _target_review_scale(
            str(row["asset_code"]), _source_int(row["scale"], "scale")
        )
        for row in rows_by_table.get("assets", ())
    }
    transaction_book = {
        str(row["transaction_id"]): str(row["book_id"])
        for row in rows_by_table.get("transactions", ())
    }
    balances: Counter[tuple[str, str, str]] = Counter()
    for row in rows_by_table.get("postings", ()):
        posting_id = str(row["id"])
        decision = posting_decisions.get(posting_id)
        if decision is None:
            continue
        transaction_id = str(row["transaction_id"])
        if transaction_id in neutralized_transaction_ids:
            continue
        target_account_id, target_side = decision
        asset_code = str(row["currency"])
        key = (transaction_book[transaction_id], target_account_id, asset_code)
        if key not in card_accounts:
            continue
        units = abs(
            decimal_to_units(
                str(row["amount"]),
                asset_code=asset_code,
                ledger_scale=scales[asset_code],
                backfill_mode=True,
            )
        )
        balances[key] += units if target_side == "credit" else -units
    return {key: balances.get(key, 0) for key in sorted(card_accounts)}


def write_credit_card_review(path: Path, document: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(canonical_json_bytes(document) + b"\n")


def read_credit_card_review(
    path: Path,
    *,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> CreditCardSemanticReview:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("credit-card review must be readable JSON") from error
    return parse_credit_card_review(
        raw,
        manifest=manifest,
        rows_by_table=rows_by_table,
    )


def parse_credit_card_review(
    raw: object,
    *,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> CreditCardSemanticReview:
    if not isinstance(raw, dict):
        raise ValueError("credit-card review must contain one JSON object")
    required = {
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
    if set(raw) != required:
        raise ValueError("credit-card review fields do not match schema version 1")
    if raw["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("credit-card review schema version is unsupported")

    manifest_hash = (
        manifest.content_sha256 or manifest.calculated_content_sha256()
    )
    if raw["snapshot_id"] != manifest.snapshot_id:
        raise ValueError("credit-card review snapshot ID mismatch")
    if raw["source_manifest_sha256"] != manifest_hash:
        raise ValueError("credit-card review source manifest hash mismatch")
    content_sha256 = _nonblank(raw["content_sha256"], "content_sha256")
    if len(content_sha256) != 64:
        raise ValueError("credit-card review content hash must be SHA-256")
    try:
        bytes.fromhex(content_sha256)
    except ValueError:
        raise ValueError("credit-card review content hash must be SHA-256") from None
    if content_sha256 != calculated_review_sha256(raw):
        raise ValueError("credit-card review content SHA-256 mismatch")

    reviewer = _nonblank(raw["reviewer"], "reviewer")
    reviewed_at = _timestamp(raw["reviewed_at"], "reviewed_at")
    transactions = _parse_transactions(raw["transactions"])
    accounts = _parse_accounts(raw["accounts"])
    expected = _parse_expected_balances(raw["expected_card_balances"])
    review = CreditCardSemanticReview(
        snapshot_id=manifest.snapshot_id,
        source_manifest_sha256=manifest_hash,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        content_sha256=content_sha256,
        transactions=transactions,
        accounts=accounts,
        expected_balances=expected,
    )
    _validate_review_against_source(review, rows_by_table)
    return review


def _parse_transactions(value: object) -> tuple[CreditCardTransactionDecision, ...]:
    if not isinstance(value, list):
        raise ValueError("credit-card review transactions must be an array")
    decisions: list[CreditCardTransactionDecision] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "book_id",
            "source_transaction_id",
            "source_transaction_sha256",
            "source_postings_sha256",
            "postings",
            "post_import_action",
        }:
            raise ValueError("credit-card transaction decision shape is invalid")
        post_import_action = _nonblank(
            raw["post_import_action"], "post_import_action"
        )
        if post_import_action not in _POST_IMPORT_ACTIONS:
            raise ValueError("credit-card post-import action is unsupported")
        postings_raw = raw["postings"]
        if not isinstance(postings_raw, list) or not postings_raw:
            raise ValueError("credit-card transaction postings must be nonempty")
        postings: list[CreditCardPostingDecision] = []
        for posting in postings_raw:
            if not isinstance(posting, dict) or set(posting) != {
                "source_posting_id",
                "target_account_id",
                "target_side",
            }:
                raise ValueError("credit-card posting decision shape is invalid")
            target_side = _nonblank(posting["target_side"], "target_side")
            if target_side not in _SIDES:
                raise ValueError("credit-card posting target side is invalid")
            postings.append(
                CreditCardPostingDecision(
                    source_posting_id=_nonblank(
                        posting["source_posting_id"], "source_posting_id"
                    ),
                    target_account_id=_nonblank(
                        posting["target_account_id"], "target_account_id"
                    ),
                    target_side=target_side,
                )
            )
        decisions.append(
            CreditCardTransactionDecision(
                book_id=_nonblank(raw["book_id"], "book_id"),
                source_transaction_id=_nonblank(
                    raw["source_transaction_id"], "source_transaction_id"
                ),
                source_transaction_sha256=_sha256(
                    raw["source_transaction_sha256"],
                    "source_transaction_sha256",
                ),
                source_postings_sha256=_sha256(
                    raw["source_postings_sha256"], "source_postings_sha256"
                ),
                postings=tuple(postings),
                post_import_action=post_import_action,
            )
        )
    return tuple(decisions)


def _parse_accounts(value: object) -> tuple[CreditCardAccountDecision, ...]:
    if not isinstance(value, list):
        raise ValueError("credit-card review accounts must be an array")
    decisions: list[CreditCardAccountDecision] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "book_id",
            "source_account_id",
            "source_account_sha256",
            "action",
        }:
            raise ValueError("credit-card account decision shape is invalid")
        action = _nonblank(raw["action"], "action")
        if action not in _ACCOUNT_ACTIONS:
            raise ValueError("credit-card account action is unsupported")
        decisions.append(
            CreditCardAccountDecision(
                book_id=_nonblank(raw["book_id"], "book_id"),
                source_account_id=_nonblank(
                    raw["source_account_id"], "source_account_id"
                ),
                source_account_sha256=_sha256(
                    raw["source_account_sha256"], "source_account_sha256"
                ),
                action=action,
            )
        )
    return tuple(decisions)


def _parse_expected_balances(
    value: object,
) -> tuple[ExpectedCreditCardBalance, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("expected credit-card balances must be a nonempty array")
    balances: list[ExpectedCreditCardBalance] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "book_id",
            "source_account_id",
            "asset_code",
            "natural_units",
        }:
            raise ValueError("expected credit-card balance shape is invalid")
        units = raw["natural_units"]
        if type(units) is not str or not units or units in {"+", "-"}:
            raise ValueError("expected credit-card natural units must be a string integer")
        try:
            parsed_units = int(units)
        except ValueError:
            raise ValueError(
                "expected credit-card natural units must be a string integer"
            ) from None
        if str(parsed_units) != units:
            raise ValueError(
                "expected credit-card natural units must be canonical"
            )
        balances.append(
            ExpectedCreditCardBalance(
                book_id=_nonblank(raw["book_id"], "book_id"),
                source_account_id=_nonblank(
                    raw["source_account_id"], "source_account_id"
                ),
                asset_code=_nonblank(raw["asset_code"], "asset_code"),
                natural_units=parsed_units,
            )
        )
    return tuple(balances)


def _validate_review_against_source(
    review: CreditCardSemanticReview,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    source_accounts = {
        (str(row["book_id"]), str(row["account_id"])): row
        for row in rows_by_table.get("accounts", ())
    }
    card_accounts = {
        key: row
        for key, row in source_accounts.items()
        if str(row.get("type")) == "liability"
        and str(row.get("subtype")) in {"credit_card", "legacy_credit_card"}
    }
    transactions = {
        (str(row["book_id"]), str(row["transaction_id"])): row
        for row in rows_by_table.get("transactions", ())
    }
    postings_by_transaction: dict[tuple[str, str], list[Mapping[str, object]]] = (
        defaultdict(list)
    )
    transaction_book = {
        str(row["transaction_id"]): str(row["book_id"])
        for row in rows_by_table.get("transactions", ())
    }
    for row in rows_by_table.get("postings", ()):
        transaction_id = str(row["transaction_id"])
        postings_by_transaction[(transaction_book[transaction_id], transaction_id)].append(
            row
        )

    scope = credit_card_transaction_scope(rows_by_table)
    transaction_index = review.transaction_index
    if len(transaction_index) != len(review.transactions):
        raise ValueError("credit-card review contains duplicate transaction decisions")
    if set(transaction_index) != set(scope):
        missing = sorted(set(scope) - set(transaction_index))
        extra = sorted(set(transaction_index) - set(scope))
        raise ValueError(
            "credit-card review transaction coverage mismatch "
            f"(missing={missing}, extra={extra})"
        )

    scales = {
        str(row["asset_code"]): _target_review_scale(
            str(row["asset_code"]), _source_int(row["scale"], "scale")
        )
        for row in rows_by_table.get("assets", ())
    }
    expected_natural: Counter[tuple[str, str, str]] = Counter()
    resolved_postings: dict[
        tuple[str, str], list[tuple[str, str, str, int]]
    ] = {}
    source_transactions_by_id = {
        str(row["transaction_id"]): row
        for row in rows_by_table.get("transactions", ())
    }

    for key in sorted(scope):
        source_transaction = transactions[key]
        source_postings = sorted(
            postings_by_transaction[key],
            key=lambda row: (
                _source_int(row["position"], "position"),
                str(row["id"]),
            ),
        )
        decision = transaction_index[key]
        if decision.source_transaction_sha256 != source_row_sha256(source_transaction):
            raise ValueError("credit-card review transaction source hash mismatch")
        if decision.source_postings_sha256 != source_postings_sha256(source_postings):
            raise ValueError("credit-card review posting-set source hash mismatch")
        posting_index = {
            posting.source_posting_id: posting for posting in decision.postings
        }
        if len(posting_index) != len(decision.postings):
            raise ValueError("credit-card review contains duplicate posting decisions")
        source_ids = {str(row["id"]) for row in source_postings}
        if set(posting_index) != source_ids:
            raise ValueError("credit-card review posting coverage mismatch")

        asset_sides: Counter[tuple[str, str]] = Counter()
        resolved: list[tuple[str, str, str, int]] = []
        for row in source_postings:
            posting = posting_index[str(row["id"])]
            target_key = (key[0], posting.target_account_id)
            if target_key not in source_accounts:
                raise ValueError("credit-card review targets an unknown source account")
            asset_code = str(row["currency"])
            target_account = source_accounts[target_key]
            if str(target_account["currency"]) != asset_code:
                raise ValueError("credit-card review changes a posting asset")
            units = decimal_to_units(
                str(row["amount"]),
                asset_code=asset_code,
                ledger_scale=scales[asset_code],
                backfill_mode=True,
            )
            units = abs(units)
            if units == 0:
                raise ValueError("credit-card review contains a zero posting")
            asset_sides[(asset_code, posting.target_side)] += units
            resolved.append(
                (
                    posting.target_account_id,
                    asset_code,
                    posting.target_side,
                    units,
                )
            )
            if target_key in card_accounts:
                natural_delta = units if posting.target_side == "credit" else -units
                if decision.post_import_action == "none":
                    expected_natural[(key[0], posting.target_account_id, asset_code)] += (
                        natural_delta
                    )
        for asset_code in {asset for asset, _ in asset_sides}:
            if asset_sides[(asset_code, "debit")] != asset_sides[
                (asset_code, "credit")
            ]:
                raise ValueError("credit-card review target postings are unbalanced")
        resolved_postings[key] = resolved

    reversal_links = resolve_reversal_links(
        rows_by_table.get("transactions", ()), rows_by_table.get("postings", ())
    ).links
    for reversal_id, original_id in reversal_links:
        reversal_row = source_transactions_by_id.get(str(reversal_id))
        original_row = source_transactions_by_id.get(str(original_id))
        if reversal_row is None or original_row is None:
            continue
        book_id = str(reversal_row["book_id"])
        reversal_key = (book_id, str(reversal_id))
        original_key = (book_id, str(original_id))
        if reversal_key not in resolved_postings or original_key not in resolved_postings:
            continue
        inverse = sorted(
            (
                account,
                asset,
                "credit" if side == "debit" else "debit",
                units,
            )
            for account, asset, side, units in resolved_postings[original_key]
        )
        if sorted(resolved_postings[reversal_key]) != inverse:
            raise ValueError("credit-card review reversal is not an exact inverse")

    account_index = review.account_index
    if len(account_index) != len(review.accounts):
        raise ValueError("credit-card review contains duplicate account decisions")
    for key, account_decision in account_index.items():
        source = card_accounts.get(key)
        if source is None or str(source.get("subtype")) != "legacy_credit_card":
            raise ValueError("credit-card account decision must target a legacy card")
        if account_decision.source_account_sha256 != source_row_sha256(source):
            raise ValueError("credit-card account source hash mismatch")
        if any(
            units
            for (book_id, account_id, _), units in expected_natural.items()
            if (book_id, account_id) == key
        ):
            raise ValueError("historical card alias must be zero before close")

    expected_index = {
        (balance.book_id, balance.source_account_id, balance.asset_code): (
            balance.natural_units
        )
        for balance in review.expected_balances
    }
    if len(expected_index) != len(review.expected_balances):
        raise ValueError("credit-card review contains duplicate expected balances")
    required_expected = {
        (book_id, account_id, str(row["currency"]))
        for (book_id, account_id), row in card_accounts.items()
    }
    if set(expected_index) != required_expected:
        raise ValueError("credit-card expected balance coverage mismatch")
    observed = {key: expected_natural.get(key, 0) for key in required_expected}
    if expected_index != observed:
        raise ValueError("credit-card expected balances disagree with decisions")


def _nonblank(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"credit-card review {field} must be nonblank")
    return value


def _sha256(value: object, field: str) -> str:
    parsed = _nonblank(value, field)
    if len(parsed) != 64:
        raise ValueError(f"credit-card review {field} must be SHA-256")
    try:
        bytes.fromhex(parsed)
    except ValueError:
        raise ValueError(f"credit-card review {field} must be SHA-256") from None
    return parsed.lower()


def _timestamp(value: object, field: str) -> datetime:
    parsed_value = _nonblank(value, field)
    try:
        parsed = datetime.fromisoformat(parsed_value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"credit-card review {field} must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"credit-card review {field} must include an offset")
    return parsed.astimezone(UTC)


def _source_int(value: object, field: str) -> int:
    if type(value) is int:
        return value
    if type(value) is str:
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError(f"credit-card source {field} must be an integer")


def _target_review_scale(asset_code: str, source_scale: int) -> int:
    return max(source_scale, 8) if asset_code == "USDT" else source_scale


__all__ = [
    "CreditCardAccountDecision",
    "CreditCardPostingDecision",
    "CreditCardSemanticReview",
    "CreditCardTransactionDecision",
    "ExpectedCreditCardBalance",
    "calculated_review_sha256",
    "build_credit_card_review_document",
    "calculate_reviewed_card_balances",
    "credit_card_transaction_scope",
    "parse_credit_card_review",
    "read_credit_card_review",
    "review_content_payload",
    "source_postings_sha256",
    "source_row_sha256",
    "write_credit_card_review",
]
