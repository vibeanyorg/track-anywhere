from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
from typing import TypeAlias
from uuid import UUID

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    PlannedCategory,
    PlannedCategoryVersion,
    PlannedProtectedContent,
)
from track_anywhere.application.imports.event_compiler import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    compile_current_v2_events,
)
from track_anywhere.application.privacy.protected_content import TransactionDescription
from track_anywhere.application.privacy.service import ImportArchiveRecordCounts
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes

from .constants import (
    EXPECTED_CREDIT_CARD_REVIEW_SHA256,
    EXPECTED_DUMP_SHA256,
    EXPECTED_FULL_MANIFEST_SHA256,
    EXPECTED_SOURCE_REVISION,
    EXPECTED_SOURCE_TABLE_COUNTS,
    TARGET_BOOK_ID,
)
from .credit_card_review import (
    CreditCardSemanticReview,
    canonical_source_identity,
    source_postings_sha256,
    source_row_sha256,
    validate_approved_credit_card_review_object,
)
from .extract import FrozenSourceRows, verify_frozen_source_rows
from .inventory import inventory_rows, validate_fixed_inventory
from .namespaces import deterministic_uuid
from .normalize import (
    HistoricalAssetScale,
    normalize_explicit_amount,
    normalize_legacy_signed_amount,
)
from .reversal_links import resolve_reversal_links


Row: TypeAlias = Mapping[str, object]
RowsByTable: TypeAlias = Mapping[str, Sequence[Row]]
_OPAQUE_REFERENCE_DOMAIN = b"track-anywhere:frozen-v1:opaque-reference:v1\0"
_INSTITUTION_METADATA_HASH_DOMAIN = (
    b"track-anywhere:frozen-v1:institution-metadata:v1\0"
)
_OMISSION_CODES = (
    "classification_audit_not_current_event",
    "investment_activity_lacks_lot_semantics",
    "uncategorized_fx_lacks_category",
    "institution_metadata_no_v2_catalog",
    "counterparty_metadata_no_v2_catalog",
)
_NORMAL_CREDIT_TYPES = frozenset({"liability", "equity", "income"})


class FrozenPlanCompilationError(ValueError):
    """Stable data-free failure for the approved one-shot planner."""


def _fail(message: str) -> None:
    raise FrozenPlanCompilationError(message)


def _required_text(row: Row, field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value.strip():
        _fail("frozen source contains invalid required text")
    return value


def _required_source_identity(row: Row, field: str) -> str:
    try:
        return canonical_source_identity(row.get(field))
    except ValueError:
        _fail("frozen source identity is invalid")


def _optional_text(row: Row, field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if type(value) is not str:
        _fail("frozen source contains invalid optional text")
    return value


def _required_int(row: Row, field: str, *, minimum: int = 0) -> int:
    value = row.get(field)
    if type(value) is not int or value < minimum:
        _fail("frozen source contains invalid integer metadata")
    return value


def _utc_timestamp(row: Row, field: str) -> datetime:
    value = row.get(field)
    if type(value) is not str:
        _fail("frozen source timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("frozen source timestamp is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("frozen source timestamp is invalid")
    return parsed.astimezone(UTC)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _opaque_reference(kind: str, *parts: str) -> str:
    if not parts or any(type(part) is not str or not part for part in parts):
        _fail("frozen source identity is invalid")
    digest = hashlib.sha256(
        _OPAQUE_REFERENCE_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + b"\0".join(part.encode("utf-8") for part in parts)
    ).hexdigest()
    return f"sha256:{digest}"


def _index(rows: Sequence[Row], field: str) -> dict[str, Row]:
    result: dict[str, Row] = {}
    for row in rows:
        identity = _required_text(row, field)
        if identity in result:
            _fail("frozen source identity is duplicated")
        result[identity] = row
    return result


def _asset_policies(rows: Sequence[Row]) -> dict[str, HistoricalAssetScale]:
    result: dict[str, HistoricalAssetScale] = {}
    for row in rows:
        code = _required_text(row, "asset_code")
        if code in result:
            _fail("frozen asset identity is duplicated")
        try:
            result[code] = HistoricalAssetScale.for_source(
                asset_code=code,
                source_scale=_required_int(row, "scale"),
                source_display_scale=_required_int(row, "display_scale"),
            )
        except ValueError:
            _fail("frozen asset scale is invalid")
    return result


def _normalize_posting(
    row: Row, *, policies: Mapping[str, HistoricalAssetScale]
) -> tuple[PostingSide, int]:
    asset_code = _required_text(row, "currency")
    policy = policies.get(asset_code)
    if policy is None:
        _fail("frozen posting asset is invalid")
    semantics = row.get("amount_semantics")
    amount = row.get("amount")
    if type(amount) is not str:
        _fail("frozen posting amount is invalid")
    try:
        if semantics in {None, "legacy_signed"}:
            normalized = normalize_legacy_signed_amount(
                amount, ledger_scale=policy.ledger_scale
            )
        elif semantics == "debit_credit":
            normalized = normalize_explicit_amount(
                amount,
                side=_required_text(row, "side"),
                ledger_scale=policy.ledger_scale,
            )
        else:
            _fail("frozen posting semantics are unsupported")
    except (TypeError, ValueError):
        _fail("frozen posting amount is invalid")
    return PostingSide(normalized.side), normalized.units


def _review_indexes(
    review: CreditCardSemanticReview,
    *,
    rows: RowsByTable,
) -> tuple[dict[str, object], dict[str, str], set[str], dict[str, int]]:
    if (
        type(review) is not CreditCardSemanticReview
        or review.source_manifest_sha256 != EXPECTED_FULL_MANIFEST_SHA256
        or review.content_sha256 != EXPECTED_CREDIT_CARD_REVIEW_SHA256
        or (
            review.transaction_count,
            review.reviewed_posting_count,
            review.card_source_posting_count,
            review.exact_reversal_action_count,
            review.account_action_count,
            review.expected_balance_count,
        )
        != (22, 48, 23, 3, 1, 5)
    ):
        _fail("approved credit-card review binding is invalid")
    transactions = _index(rows["transactions"], "transaction_id")
    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for posting in rows["postings"]:
        postings_by_transaction[_required_text(posting, "transaction_id")].append(
            posting
        )
    transaction_decisions: dict[str, object] = {}
    posting_decisions: dict[str, str] = {}
    action_ids: set[str] = set()
    for decision in review.transactions:
        source_id = decision.source_transaction_id
        source = transactions.get(source_id)
        postings = postings_by_transaction.get(source_id, [])
        if (
            source is None
            or source_row_sha256(source) != decision.source_transaction_sha256
            or source_postings_sha256(postings) != decision.source_postings_sha256
            or source_id in transaction_decisions
        ):
            _fail("approved credit-card review transaction binding is invalid")
        transaction_decisions[source_id] = decision
        source_posting_ids = {_required_source_identity(row, "id") for row in postings}
        reviewed_ids = {item.source_posting_id for item in decision.postings}
        if source_posting_ids != reviewed_ids or len(reviewed_ids) != len(
            decision.postings
        ):
            _fail("approved credit-card review posting coverage is invalid")
        for item in decision.postings:
            if item.source_posting_id in posting_decisions:
                _fail("approved credit-card review posting is duplicated")
            posting_decisions[item.source_posting_id] = (
                item.target_account_id + "\0" + item.target_side
            )
        if decision.post_import_action == "exact_reversal":
            action_ids.add(source_id)
    alias_ids = {item.source_account_id for item in review.accounts}
    expected_balances = {
        item.source_account_id + "\0" + item.asset_code: item.natural_units
        for item in review.expected_balances
    }
    if len(alias_ids) != 1 or len(action_ids) != 3 or len(expected_balances) != 5:
        _fail("approved credit-card review summary is invalid")
    return transaction_decisions, posting_decisions, alias_ids, expected_balances


def _pure_fx_accounts(
    *,
    transactions: Sequence[Row],
    postings: Sequence[Row],
    accounts: Mapping[str, Row],
    policies: Mapping[str, HistoricalAssetScale],
) -> tuple[set[str], dict[str, str]]:
    by_transaction: dict[str, list[Row]] = defaultdict(list)
    for posting in postings:
        by_transaction[_required_text(posting, "transaction_id")].append(posting)
    pure_fx: set[str] = set()
    trading_by_asset: dict[str, str] = {}
    for transaction in transactions:
        transaction_id = _required_text(transaction, "transaction_id")
        candidates = by_transaction.get(transaction_id, [])
        by_asset: dict[str, list[Row]] = defaultdict(list)
        for posting in candidates:
            by_asset[_required_text(posting, "currency")].append(posting)
        if (
            len(candidates) != 4
            or len(by_asset) != 2
            or any(len(group) != 2 for group in by_asset.values())
        ):
            continue
        system_sides: set[PostingSide] = set()
        selected: list[tuple[str, str]] = []
        valid = True
        for asset_code, group in by_asset.items():
            system = [
                posting
                for posting in group
                if _required_text(
                    accounts[_required_text(posting, "account_id")], "type"
                )
                == "system"
            ]
            if len(system) != 1:
                valid = False
                break
            other = group[0] if group[1] is system[0] else group[1]
            system_side, _ = _normalize_posting(system[0], policies=policies)
            other_side, _ = _normalize_posting(other, policies=policies)
            if system_side is other_side:
                valid = False
                break
            system_sides.add(system_side)
            selected.append((asset_code, _required_text(system[0], "account_id")))
        if not valid or system_sides != {PostingSide.DEBIT, PostingSide.CREDIT}:
            continue
        pure_fx.add(transaction_id)
        for asset_code, account_id in selected:
            existing = trading_by_asset.setdefault(asset_code, account_id)
            if existing != account_id:
                _fail("frozen FX trading account mapping is ambiguous")
    if len(pure_fx) != 3 or len(trading_by_asset) != 4:
        _fail("frozen FX topology does not match the approved source")
    return pure_fx, trading_by_asset


def _planned_assets(
    rows: Sequence[Row], policies: Mapping[str, HistoricalAssetScale]
) -> tuple[PlannedAsset, ...]:
    planned = []
    for row in rows:
        code = _required_text(row, "asset_code")
        policy = policies[code]
        planned.append(
            PlannedAsset(
                asset_code=code,
                kind=_required_text(row, "kind"),
                ledger_scale=policy.ledger_scale,
                input_scale=policy.input_scale,
                display_scale=policy.display_scale,
                current_name=_required_text(row, "name"),
                status=_required_text(row, "status"),  # type: ignore[arg-type]
            )
        )
    return tuple(sorted(planned, key=lambda item: item.asset_code.encode("utf-8")))


def _planned_categories(
    *, source_book_id: str, categories: Sequence[Row], versions: Sequence[Row]
) -> tuple[PlannedCategory, ...]:
    category_rows = _index(categories, "category_id")
    versions_by_category: dict[str, list[Row]] = defaultdict(list)
    for version in versions:
        versions_by_category[_required_text(version, "category_id")].append(version)
    if set(versions_by_category) != set(category_rows) or any(
        len(items) != 1 for items in versions_by_category.values()
    ):
        _fail("frozen category versions do not have one current snapshot")

    planned_by_source: dict[str, PlannedCategory] = {}
    parent_by_source: dict[str, str | None] = {}
    for source_id, row in category_rows.items():
        version = versions_by_category[source_id][0]
        parent_value = version.get("parent_id")
        parent_source = None if parent_value is None else str(parent_value)
        if parent_source is not None and parent_source not in category_rows:
            _fail("frozen category parent is invalid")
        target_id = deterministic_uuid("category", source_book_id, source_id)
        target_parent = (
            None
            if parent_source is None
            else deterministic_uuid("category", source_book_id, parent_source)
        )
        version_source_id = _required_text(version, "category_version_id")
        version_id = deterministic_uuid(
            "category_version", source_book_id, version_source_id
        )
        status = (
            "archived"
            if version.get("valid_to") is not None
            or _required_text(row, "status") == "archived"
            else "active"
        )
        reason_value = version.get("change_reason")
        reason = "frozen_import" if reason_value is None else str(reason_value)
        if not reason.strip() or len(reason) > 64:
            _fail("frozen category change reason is invalid")
        name = _required_text(version, "name")
        planned_version = PlannedCategoryVersion(
            category_version_id=version_id,
            parent_category_id=target_parent,
            name=name,
            status=status,
            change_reason_code=reason,
        )
        planned_by_source[source_id] = PlannedCategory(
            category_id=target_id,
            parent_category_id=target_parent,
            current_name=name,
            current_version_id=version_id,
            status=status,
            version=planned_version,
        )
        parent_by_source[source_id] = parent_source

    ordered: list[PlannedCategory] = []
    remaining = set(planned_by_source)
    emitted: set[str] = set()
    while remaining:
        available = sorted(
            (
                source_id
                for source_id in remaining
                if parent_by_source[source_id] is None
                or parent_by_source[source_id] in emitted
            ),
            key=lambda source_id: planned_by_source[source_id].category_id.bytes,
        )
        if not available:
            _fail("frozen category dependency graph is invalid")
        for source_id in available:
            ordered.append(planned_by_source[source_id])
            emitted.add(source_id)
            remaining.remove(source_id)
    return tuple(ordered)


def _transaction_kind(purpose: object, *, pure_fx: bool) -> TransactionKind:
    if pure_fx:
        return TransactionKind.FX
    normalized = str(purpose or "").casefold()
    if "opening" in normalized:
        return TransactionKind.OPENING
    if "transfer" in normalized:
        return TransactionKind.TRANSFER
    if "adjust" in normalized:
        return TransactionKind.ADJUSTMENT
    return TransactionKind.STANDARD


def _reporting_line_kind(value: object) -> ReportingLineKind:
    normalized = str(value or "").casefold()
    if "income" in normalized or "dividend" in normalized:
        return ReportingLineKind.INCOME
    if "transfer" in normalized:
        return ReportingLineKind.TRANSFER
    if "tax" in normalized:
        return ReportingLineKind.TAX
    if "invest" in normalized or normalized in {"buy", "sell"}:
        return ReportingLineKind.INVESTMENT
    return ReportingLineKind.EXPENSE


def _source_sort_key(
    row: Row, identity_field: str, time_field: str
) -> tuple[datetime, bytes]:
    return _utc_timestamp(row, time_field), _required_text(row, identity_field).encode(
        "utf-8"
    )


def _description_content(transaction: Row, *, lines: Sequence[Row]) -> bytes:
    ordered_lines = sorted(
        lines,
        key=lambda row: (
            _required_int(row, "position"),
            _required_text(row, "line_id").encode("utf-8"),
        ),
    )
    contract = TransactionDescription(
        purpose=_optional_text(transaction, "purpose"),
        transaction_memo=_optional_text(transaction, "memo"),
        line_memos=tuple(_optional_text(line, "memo") for line in ordered_lines),
    )
    return canonical_json_bytes(contract.model_dump(mode="json"))


def _correction_description() -> bytes:
    return canonical_json_bytes(
        TransactionDescription(
            purpose="reviewed_card_direction_correction",
            transaction_memo=None,
            line_memos=(),
        ).model_dump(mode="json")
    )


def _journal_posting_facts(
    *,
    transaction: Row,
    postings: Sequence[Row],
    policies: Mapping[str, HistoricalAssetScale],
    posting_decisions: Mapping[str, str],
) -> tuple[JournalPostingFact, ...]:
    source_book_id = _required_text(transaction, "book_id")
    source_transaction_id = _required_text(transaction, "transaction_id")
    result: list[JournalPostingFact] = []
    for position, row in enumerate(
        sorted(
            postings,
            key=lambda item: (
                _required_int(item, "position"),
                _required_source_identity(item, "id").encode("utf-8"),
            ),
        )
    ):
        if _required_int(row, "position") != position:
            _fail("frozen posting positions are not canonical")
        side, units = _normalize_posting(row, policies=policies)
        source_account_id = _required_text(row, "account_id")
        source_posting_id = _required_source_identity(row, "id")
        decision = posting_decisions.get(source_posting_id)
        if decision is not None:
            target_source_account, target_side = decision.split("\0", 1)
            source_account_id = target_source_account
            side = PostingSide(target_side)
        result.append(
            JournalPostingFact(
                posting_id=deterministic_uuid(
                    "posting",
                    source_book_id,
                    source_transaction_id,
                    source_posting_id,
                ),
                position=position,
                account_id=deterministic_uuid(
                    "account", source_book_id, source_account_id
                ),
                asset_code=_required_text(row, "currency"),
                side=side,
                units=str(units),
            )
        )
    return tuple(result)


def _inverse_postings(
    source: JournalTransactionPosted | JournalTransactionReversed,
    *,
    source_book_id: str,
    source_transaction_id: str,
    correction: bool,
) -> tuple[JournalPostingFact, ...]:
    original = (
        source.postings
        if type(source) is JournalTransactionPosted
        else source.inverse_postings
    )
    suffix = "reviewed-card-correction" if correction else "source-reversal"
    return tuple(
        JournalPostingFact(
            posting_id=deterministic_uuid(
                "posting",
                source_book_id,
                source_transaction_id,
                suffix,
                str(posting.position),
            ),
            position=posting.position,
            account_id=posting.account_id,
            asset_code=posting.asset_code,
            side=(
                PostingSide.CREDIT
                if posting.side is PostingSide.DEBIT
                else PostingSide.DEBIT
            ),
            units=posting.units,
        )
        for posting in original
    )


def _archive_content(
    *,
    source: FrozenSourceRows,
    rows: RowsByTable,
    uncategorized_fx: Sequence[Row],
) -> tuple[bytes, ImportArchiveRecordCounts]:
    institution_rows = tuple(
        row
        for row in rows["accounts"]
        if row.get("institution") is not None or row.get("institution_type") is not None
    )
    record_counts = ImportArchiveRecordCounts(
        classification_audit_records=len(rows["classification_events"]),
        investment_activities=len(rows["investment_events"]),
        investment_valuations=len(rows["investment_valuations"]),
        uncategorized_fx_reporting_facts=len(uncategorized_fx),
        institution_metadata_records=len(institution_rows),
        counterparty_records=len(rows["counterparties"]),
        omission_records=len(_OMISSION_CODES),
    )
    if record_counts != ImportArchiveRecordCounts(
        classification_audit_records=43,
        investment_activities=6,
        investment_valuations=0,
        uncategorized_fx_reporting_facts=5,
        institution_metadata_records=117,
        counterparty_records=2,
        omission_records=5,
    ):
        _fail("frozen archive collection counts are invalid")

    records: list[dict[str, object]] = [
        {
            "record_type": "archive_manifest",
            "contract_version": 1,
            "source_dump_hash": EXPECTED_DUMP_SHA256,
            "source_manifest_hash": EXPECTED_FULL_MANIFEST_SHA256,
            "card_review_hash": EXPECTED_CREDIT_CARD_REVIEW_SHA256,
            "source_revision": EXPECTED_SOURCE_REVISION,
            "source_counts": dict(sorted(EXPECTED_SOURCE_TABLE_COUNTS.items())),
            "table_hashes": {
                table.table: table.ndjson_sha256
                for table in sorted(source.manifest.tables)
            },
        }
    ]

    def append_source_records(
        record_type: str, source_rows: Sequence[Row], identity_field: str
    ) -> None:
        ordered = sorted(
            source_rows,
            key=lambda row: _opaque_reference(
                record_type, _required_text(row, identity_field)
            ),
        )
        for row in ordered:
            records.append(
                {
                    "record_type": record_type,
                    "source_ref": _opaque_reference(
                        record_type, _required_text(row, identity_field)
                    ),
                    "source_row_hash": source_row_sha256(row),
                    "source": _thaw(row),
                }
            )

    append_source_records(
        "classification_audit",
        rows["classification_events"],
        "classification_event_id",
    )
    append_source_records("investment_activity", rows["investment_events"], "event_id")
    records.append(
        {
            "record_type": "investment_valuation_summary",
            "source_count": 0,
        }
    )
    append_source_records(
        "uncategorized_fx_reporting_fact", uncategorized_fx, "line_id"
    )
    for row in sorted(
        institution_rows,
        key=lambda item: _opaque_reference(
            "institution_metadata", _required_text(item, "account_id")
        ),
    ):
        source_account_ref = _opaque_reference(
            "institution_metadata", _required_text(row, "account_id")
        )
        archived_metadata = {
            "source_account_ref": source_account_ref,
            "institution_type": _thaw(row.get("institution_type")),
            "institution": _thaw(row.get("institution")),
        }
        records.append(
            {
                "record_type": "institution_metadata",
                "source_row_hash": source_row_sha256(row),
                "archived_metadata_hash": hashlib.sha256(
                    _INSTITUTION_METADATA_HASH_DOMAIN
                    + canonical_json_bytes(archived_metadata)
                ).hexdigest(),
                **archived_metadata,
            }
        )
    append_source_records(
        "counterparty_metadata", rows["counterparties"], "counterparty_id"
    )
    omission_counts = (
        len(rows["classification_events"]),
        len(rows["investment_events"]),
        len(uncategorized_fx),
        len(institution_rows),
        len(rows["counterparties"]),
    )
    records.extend(
        {
            "record_type": "omission_reason",
            "reason_code": code,
            "source_count": count,
        }
        for code, count in zip(_OMISSION_CODES, omission_counts, strict=True)
    )
    return b"\n".join(
        canonical_json_bytes(record) for record in records
    ) + b"\n", record_counts


def _compile_frozen_financial_history_plan(
    *, source: FrozenSourceRows, review: CreditCardSemanticReview
) -> FrozenFinancialHistoryPlan:
    try:
        verify_frozen_source_rows(source)
        rows: RowsByTable = {name: table.rows for name, table in source.tables.items()}
        validate_fixed_inventory(
            inventory_rows(rows, attachments_count=source.attachments_count)
        )
    except (TypeError, ValueError):
        _fail("frozen source contract is invalid")
    books = rows["ledger_books"]
    if len(books) != 1:
        _fail("frozen source Book contract is invalid")
    source_book_id = _required_text(books[0], "book_id")
    snapshot_id = source.manifest.snapshot_id

    policies = _asset_policies(rows["assets"])
    accounts_by_source = _index(rows["accounts"], "account_id")
    transactions_by_source = _index(rows["transactions"], "transaction_id")
    categories_by_source = _index(rows["categories"], "category_id")
    versions_by_source = _index(rows["category_versions"], "category_version_id")
    (
        _transaction_decisions,
        posting_decisions,
        alias_source_ids,
        expected_card_balances,
    ) = _review_indexes(review, rows=rows)
    pure_fx, trading_by_asset = _pure_fx_accounts(
        transactions=rows["transactions"],
        postings=rows["postings"],
        accounts=accounts_by_source,
        policies=policies,
    )

    planned_assets = _planned_assets(rows["assets"], policies)
    planned_categories = _planned_categories(
        source_book_id=source_book_id,
        categories=rows["categories"],
        versions=rows["category_versions"],
    )
    category_plan_by_id = {item.category_id: item for item in planned_categories}
    if len(category_plan_by_id) != 37:
        _fail("frozen category plan is invalid")

    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    lines_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for row in rows["postings"]:
        postings_by_transaction[_required_text(row, "transaction_id")].append(row)
    for row in rows["transaction_lines"]:
        lines_by_transaction[_required_text(row, "transaction_id")].append(row)
    reversal_links = resolve_reversal_links(rows["transactions"], rows["postings"])
    reversal_source_ids = {source for source, _target in reversal_links.links}
    original_rows = sorted(
        (
            row
            for source_id, row in transactions_by_source.items()
            if source_id not in reversal_source_ids
        ),
        key=lambda row: _source_sort_key(row, "transaction_id", "occurred_at"),
    )
    if len(original_rows) != 130:
        _fail("frozen original journal topology is invalid")

    command_id = deterministic_uuid(
        "command", snapshot_id, str(TARGET_BOOK_ID), "full-financial-history-import-v1"
    )
    description_by_source: dict[str, UUID] = {}
    descriptions: list[PlannedProtectedContent] = []
    pending_journal: list[PendingEvent] = []
    target_transaction_by_source: dict[str, UUID] = {}
    event_id_by_source: dict[str, UUID] = {}
    for transaction in original_rows:
        source_id = _required_text(transaction, "transaction_id")
        target_transaction_id = deterministic_uuid(
            "transaction", snapshot_id, source_book_id, source_id
        )
        description_id = deterministic_uuid(
            "description", snapshot_id, source_book_id, source_id
        )
        event_id = deterministic_uuid(
            "event", snapshot_id, source_book_id, source_id, "journal.post"
        )
        target_transaction_by_source[source_id] = target_transaction_id
        event_id_by_source[source_id] = event_id
        description_by_source[source_id] = description_id
        descriptions.append(
            PlannedProtectedContent(
                sidecar_id=description_id,
                kind="transaction_description",
                canonical_plaintext=_description_content(
                    transaction, lines=lines_by_transaction.get(source_id, ())
                ),
                record_counts=None,
            )
        )
        payload = JournalTransactionPosted(
            transaction_id=target_transaction_id,
            kind=_transaction_kind(
                transaction.get("purpose"), pure_fx=source_id in pure_fx
            ),
            postings=_journal_posting_facts(
                transaction=transaction,
                postings=postings_by_transaction[source_id],
                policies=policies,
                posting_decisions=posting_decisions,
            ),
            description_ref=description_id,
            external_references=(
                FinancialExternalReference(
                    provider_code="v1_history",
                    kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                    reference=_opaque_reference(
                        "transaction", source_book_id, source_id
                    ),
                ),
            ),
        )
        pending_journal.append(
            PendingEvent(
                event_id=event_id,
                stream_type="journal_transaction",
                stream_id=target_transaction_id,
                payload=payload,
                command_id=command_id,
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                correlation_id=command_id,
                causation_event_id=None,
                effective_at=_utc_timestamp(transaction, "occurred_at"),
            )
        )

    compiled_journal = compile_current_v2_events(
        target_book_id=TARGET_BOOK_ID, events=tuple(pending_journal)
    )
    compiled_by_transaction = {
        event.payload.transaction_id: event
        for event in compiled_journal
        if type(event.payload) is JournalTransactionPosted
    }
    remaining_reversals = set(reversal_source_ids)
    reversal_target_by_source = dict(reversal_links.links)
    while remaining_reversals:
        available = sorted(
            (
                source_id
                for source_id in remaining_reversals
                if reversal_target_by_source[source_id] in target_transaction_by_source
            ),
            key=lambda source_id: _source_sort_key(
                transactions_by_source[source_id], "transaction_id", "occurred_at"
            ),
        )
        if not available:
            _fail("frozen reversal dependency topology is invalid")
        for source_id in available:
            transaction = transactions_by_source[source_id]
            original_source_id = reversal_target_by_source[source_id]
            original_target_id = target_transaction_by_source[original_source_id]
            original_event = compiled_by_transaction.get(original_target_id)
            if original_event is None or type(original_event.payload) not in (
                JournalTransactionPosted,
                JournalTransactionReversed,
            ):
                _fail("frozen reversal source event is invalid")
            target_transaction_id = deterministic_uuid(
                "transaction", snapshot_id, source_book_id, source_id
            )
            description_id = deterministic_uuid(
                "description", snapshot_id, source_book_id, source_id
            )
            event_id = deterministic_uuid(
                "event", snapshot_id, source_book_id, source_id, "journal.reverse"
            )
            actual_inverse = _journal_posting_facts(
                transaction=transaction,
                postings=postings_by_transaction[source_id],
                policies=policies,
                posting_decisions=posting_decisions,
            )
            expected_inverse = _inverse_postings(
                original_event.payload,
                source_book_id=source_book_id,
                source_transaction_id=source_id,
                correction=False,
            )
            if tuple(
                (item.position, item.account_id, item.asset_code, item.side, item.units)
                for item in actual_inverse
            ) != tuple(
                (item.position, item.account_id, item.asset_code, item.side, item.units)
                for item in expected_inverse
            ):
                _fail("frozen source reversal postings are not an exact inverse")
            descriptions.append(
                PlannedProtectedContent(
                    sidecar_id=description_id,
                    kind="transaction_description",
                    canonical_plaintext=_description_content(
                        transaction, lines=lines_by_transaction.get(source_id, ())
                    ),
                    record_counts=None,
                )
            )
            pending_journal.append(
                PendingEvent(
                    event_id=event_id,
                    stream_type="journal_transaction",
                    stream_id=target_transaction_id,
                    payload=JournalTransactionReversed(
                        reversal_transaction_id=target_transaction_id,
                        reverses_transaction_id=original_target_id,
                        original_event_id=original_event.event_id,
                        original_event_hash=original_event.event_hash,
                        reason_code=ReversalReasonCode.IMPORT_CORRECTION,
                        inverse_postings=actual_inverse,
                        description_ref=description_id,
                    ),
                    command_id=command_id,
                    actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                    correlation_id=command_id,
                    causation_event_id=original_event.event_id,
                    effective_at=_utc_timestamp(transaction, "occurred_at"),
                )
            )
            target_transaction_by_source[source_id] = target_transaction_id
            event_id_by_source[source_id] = event_id
            description_by_source[source_id] = description_id
            remaining_reversals.remove(source_id)
            compiled_journal = compile_current_v2_events(
                target_book_id=TARGET_BOOK_ID, events=tuple(pending_journal)
            )
            compiled_by_transaction = {
                (
                    event.payload.transaction_id
                    if type(event.payload) is JournalTransactionPosted
                    else event.payload.reversal_transaction_id
                ): event
                for event in compiled_journal
            }

    action_source_ids = sorted(
        (
            item.source_transaction_id
            for item in review.transactions
            if item.post_import_action == "exact_reversal"
        ),
        key=lambda source_id: _source_sort_key(
            transactions_by_source[source_id], "transaction_id", "occurred_at"
        ),
    )
    already_reversed_targets = set(reversal_target_by_source.values())
    if any(source_id in already_reversed_targets for source_id in action_source_ids):
        _fail("reviewed correction targets an already reversed transaction")
    for source_id in action_source_ids:
        source_event = compiled_by_transaction.get(
            target_transaction_by_source[source_id]
        )
        if source_event is None or type(source_event.payload) not in (
            JournalTransactionPosted,
            JournalTransactionReversed,
        ):
            _fail("reviewed correction source is invalid")
        correction_transaction_id = deterministic_uuid(
            "transaction",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        correction_description_id = deterministic_uuid(
            "description",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        correction_event_id = deterministic_uuid(
            "event",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        descriptions.append(
            PlannedProtectedContent(
                sidecar_id=correction_description_id,
                kind="transaction_description",
                canonical_plaintext=_correction_description(),
                record_counts=None,
            )
        )
        pending_journal.append(
            PendingEvent(
                event_id=correction_event_id,
                stream_type="journal_transaction",
                stream_id=correction_transaction_id,
                payload=JournalTransactionReversed(
                    reversal_transaction_id=correction_transaction_id,
                    reverses_transaction_id=(
                        source_event.payload.transaction_id
                        if type(source_event.payload) is JournalTransactionPosted
                        else source_event.payload.reversal_transaction_id
                    ),
                    original_event_id=source_event.event_id,
                    original_event_hash=source_event.event_hash,
                    reason_code=ReversalReasonCode.IMPORT_CORRECTION,
                    inverse_postings=_inverse_postings(
                        source_event.payload,
                        source_book_id=source_book_id,
                        source_transaction_id=source_id,
                        correction=True,
                    ),
                    description_ref=correction_description_id,
                ),
                command_id=command_id,
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                correlation_id=command_id,
                causation_event_id=source_event.event_id,
                effective_at=_utc_timestamp(
                    transactions_by_source[source_id], "occurred_at"
                ),
            )
        )
        compiled_journal = compile_current_v2_events(
            target_book_id=TARGET_BOOK_ID, events=tuple(pending_journal)
        )
        compiled_by_transaction = {
            (
                event.payload.transaction_id
                if type(event.payload) is JournalTransactionPosted
                else event.payload.reversal_transaction_id
            ): event
            for event in compiled_journal
        }

    if len(pending_journal) != 138 or len(descriptions) != 138:
        _fail("frozen journal or description count is invalid")

    categorized_lines: list[Row] = []
    uncategorized_fx: list[Row] = []
    for line in rows["transaction_lines"]:
        if (
            line.get("category_id") is not None
            or line.get("category_version_id") is not None
        ):
            categorized_lines.append(line)
        else:
            normalized_type = str(line.get("line_type") or "").casefold()
            if "fx" not in normalized_type and "exchange" not in normalized_type:
                _fail("uncategorized reporting fact is not approved FX context")
            uncategorized_fx.append(line)
    reporting_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for line in categorized_lines:
        reporting_by_transaction[_required_text(line, "transaction_id")].append(line)
    if (
        len(categorized_lines) != 38
        or len(uncategorized_fx) != 5
        or len(reporting_by_transaction) != 38
        or any(len(items) != 1 for items in reporting_by_transaction.values())
    ):
        _fail("frozen current reporting topology is invalid")

    pending_all = list(pending_journal)
    for source_id in sorted(
        reporting_by_transaction,
        key=lambda item: _source_sort_key(
            transactions_by_source[item], "transaction_id", "occurred_at"
        ),
    ):
        if source_id in reversal_source_ids:
            _fail("reversal transaction cannot receive current reporting")
        line = reporting_by_transaction[source_id][0]
        category_source = (
            None if line.get("category_id") is None else str(line["category_id"])
        )
        version_source = (
            None
            if line.get("category_version_id") is None
            else str(line["category_version_id"])
        )
        if category_source is None and version_source is not None:
            version_row = versions_by_source.get(version_source)
            category_source = (
                None
                if version_row is None
                else _required_text(version_row, "category_id")
            )
        if category_source is None or category_source not in categories_by_source:
            _fail("frozen reporting category binding is invalid")
        category_id = deterministic_uuid("category", source_book_id, category_source)
        category_plan = category_plan_by_id.get(category_id)
        if category_plan is None:
            _fail("frozen reporting category plan is invalid")
        if version_source is None:
            catalog_id = category_plan.current_version_id
        else:
            version_row = versions_by_source.get(version_source)
            if (
                version_row is None
                or _required_text(version_row, "category_id") != category_source
            ):
                _fail("frozen reporting category version binding is invalid")
            catalog_id = deterministic_uuid(
                "category_version", source_book_id, version_source
            )
        line_asset = _required_text(line, "currency")
        policy = policies.get(line_asset)
        if policy is None or type(line.get("amount")) is not str:
            _fail("frozen reporting amount is invalid")
        try:
            line_units = normalize_legacy_signed_amount(
                line["amount"],
                ledger_scale=policy.ledger_scale,  # type: ignore[arg-type]
            ).units
        except (TypeError, ValueError):
            _fail("frozen reporting amount is invalid")
        target_transaction_id = target_transaction_by_source[source_id]
        source_event = compiled_by_transaction[target_transaction_id]
        source_line_id = _required_text(line, "line_id")
        payload = ReportingLinesAssigned(
            transaction_id=target_transaction_id,
            classification_revision=1,
            lines=(
                ReportingLine(
                    line_id=deterministic_uuid(
                        "line", source_book_id, source_id, source_line_id
                    ),
                    line_version_id=deterministic_uuid(
                        "line_version",
                        source_book_id,
                        source_id,
                        source_line_id,
                        str(_required_int(line, "version", minimum=1)),
                    ),
                    catalog_id=catalog_id,
                    position=0,
                    asset_code=line_asset,
                    units=str(line_units),
                    line_kind=_reporting_line_kind(line.get("line_type")),
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=category_id,
                    description_ref=None,
                ),
            ),
        )
        pending_all.append(
            PendingEvent(
                event_id=deterministic_uuid(
                    "event", snapshot_id, source_book_id, source_id, "reporting.assign"
                ),
                stream_type="reporting_lines",
                stream_id=target_transaction_id,
                payload=payload,
                command_id=command_id,
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                correlation_id=command_id,
                causation_event_id=source_event.event_id,
                effective_at=_utc_timestamp(
                    transactions_by_source[source_id], "occurred_at"
                ),
            )
        )

    planned_events = compile_current_v2_events(
        target_book_id=TARGET_BOOK_ID, events=tuple(pending_all)
    )
    balances: Counter[UUID] = Counter()
    for event in planned_events:
        payload = event.payload
        if type(payload) is JournalTransactionPosted:
            event_postings = payload.postings
        elif type(payload) is JournalTransactionReversed:
            event_postings = payload.inverse_postings
        else:
            continue
        for posting in event_postings:
            balances[posting.account_id] += (
                int(posting.units)
                if posting.side is PostingSide.DEBIT
                else -int(posting.units)
            )

    trading_accounts = set(trading_by_asset.values())
    planned_accounts: list[PlannedAccount] = []
    natural_by_source: dict[str, int] = {}
    for source_id, row in accounts_by_source.items():
        target_id = deterministic_uuid("account", source_book_id, source_id)
        account_type = _required_text(row, "type")
        raw_balance = balances.get(target_id, 0)
        natural = -raw_balance if account_type in _NORMAL_CREDIT_TYPES else raw_balance
        natural_by_source[source_id] = natural
        subtype_value = row.get("subtype")
        subtype = (
            None
            if subtype_value is None or not str(subtype_value).strip()
            else str(subtype_value).strip()
        )
        if subtype == "legacy_credit_card":
            subtype = "credit_card"
        planned_accounts.append(
            PlannedAccount(
                account_id=target_id,
                asset_code=_required_text(row, "currency"),
                account_type=account_type,  # type: ignore[arg-type]
                account_subtype=subtype,
                system_role=("fx_trading" if source_id in trading_accounts else None),
                current_name=_required_text(row, "name"),
                status="active",
                expected_natural_units=natural,
                close_after_import=source_id in alias_source_ids,
            )
        )
    for key, expected in expected_card_balances.items():
        source_id, asset_code = key.split("\0", 1)
        account = accounts_by_source.get(source_id)
        if (
            account is None
            or _required_text(account, "currency") != asset_code
            or natural_by_source.get(source_id) != expected
        ):
            _fail("reviewed credit-card terminal balance is invalid")
    if any(natural_by_source[source_id] != 0 for source_id in alias_source_ids):
        _fail("reviewed retired alias is not zero")

    archive_plaintext, archive_counts = _archive_content(
        source=source, rows=rows, uncategorized_fx=uncategorized_fx
    )
    archive = PlannedProtectedContent(
        sidecar_id=deterministic_uuid(
            "archive", snapshot_id, str(TARGET_BOOK_ID), "full-financial-history"
        ),
        kind="import_archive",
        canonical_plaintext=archive_plaintext,
        record_counts=archive_counts,
    )
    plan = FrozenFinancialHistoryPlan(
        contract_version=1,
        target_book_id=TARGET_BOOK_ID,
        source_dump_hash=EXPECTED_DUMP_SHA256,
        manifest_hash=EXPECTED_FULL_MANIFEST_SHA256,
        card_review_hash=EXPECTED_CREDIT_CARD_REVIEW_SHA256,
        assets=planned_assets,
        accounts=tuple(
            sorted(planned_accounts, key=lambda account: account.account_id.bytes)
        ),
        categories=planned_categories,
        descriptions=tuple(
            sorted(descriptions, key=lambda content: content.sidecar_id.bytes)
        ),
        archive=archive,
        events=planned_events,
        expected_terminal_hash=planned_events[-1].event_hash,
        quarantine_count=0,
    )
    return plan


def compile_frozen_financial_history_plan(
    *, source: FrozenSourceRows, review: CreditCardSemanticReview
) -> FrozenFinancialHistoryPlan:
    """Compile only a fully revalidated approved object, with safe failures."""

    failure_message: str | None = None
    try:
        validated_review = validate_approved_credit_card_review_object(
            review,
            source=source,
        )
        return _compile_frozen_financial_history_plan(
            source=source,
            review=validated_review,
        )
    except FrozenPlanCompilationError as error:
        failure_message = str(error)
    except (KeyError, OverflowError, TypeError, UnicodeError, ValueError):
        failure_message = "frozen financial history plan compilation failed"
    if failure_message is None:  # pragma: no cover - exception exhaustiveness
        failure_message = "frozen financial history plan compilation failed"
    raise FrozenPlanCompilationError(failure_message) from None


__all__ = ["FrozenPlanCompilationError", "compile_frozen_financial_history_plan"]
