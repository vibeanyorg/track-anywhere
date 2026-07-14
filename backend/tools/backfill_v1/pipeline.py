from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.catalogs.create_account import (
    CreateAccount,
    create_account,
)
from track_anywhere.application.catalogs.create_asset import CreateAsset, create_asset
from track_anywhere.application.catalogs.create_book import CreateBook, create_book
from track_anywhere.application.catalogs.create_category import (
    CreateCategory,
    create_category,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    execute_assign_reporting_lines,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.backfill.events import (
    ExactImportedDecimal,
    HistoricalCategoryActivityImported,
    HistoricalCategoryActivityKind,
    HistoricalInvestmentActivityImported,
    HistoricalInvestmentActivityKind,
    HistoricalReportingLineImported,
    HistoricalReportingLineKind,
)
from track_anywhere.domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
)
from track_anywhere.infrastructure.db.models.auth import UserRecord
from track_anywhere.infrastructure.db.models.backfill import (
    BackfillCheckpointRecord,
    BackfillQuarantineRecord,
    BackfillSealRecord,
    BackfillSourceReceiptRecord,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AssetRecord,
    BookRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import BookEventHeadRecord
from track_anywhere.infrastructure.db.models.projections import (
    JournalTransactionRecord,
    ReportingLineRecord,
)

from .extract import (
    TABLE_SPECS,
    canonicalize_row,
    extract_database,
    load_extracted_rows,
)
from .inventory import inventory_rows
from .load import (
    BackfillChangedSourceError,
    BackfillSeal,
    LoadResult,
    ResumableBackfillLoader,
    SourceLoadItem,
    seal_backfill,
    write_verification,
)
from .manifest import (
    FrozenSourceManifest,
    canonical_json_bytes,
    read_target_state,
    read_manifest,
    validate_target_state,
    verify_frozen_source,
)
from .namespaces import deterministic_uuid
from .normalize import decimal_to_units, normalize_legacy_signed_posting
from .quarantine import record_quarantine
from .config import BackfillConfig, current_v2_head


_PRIMARY_KEYS = {spec.table: spec.primary_key for spec in TABLE_SPECS}
_TABLE_ORDER = tuple(spec.table for spec in TABLE_SPECS)
_SOURCE_ACTOR_HASH_DOMAIN_V1 = b"track-anywhere:v2:backfill:source-actor:v1\x00"


class BackfillMappingError(ValueError):
    def __init__(
        self,
        source_table: str,
        source_primary_key: str,
        reason_code: str,
        detail: str,
    ) -> None:
        self.source_table = source_table
        self.source_primary_key = source_primary_key
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(
            f"{source_table}:{source_primary_key}: {reason_code}: {detail}"
        )


class BackfillRunBlocked(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackfillPipelineResult:
    applied_receipts: int
    replayed_receipts: int
    seal: BackfillSeal


@dataclass(frozen=True, slots=True)
class _ScheduledAggregate:
    kind: str
    row: Mapping[str, object]
    rank: int


class _BorrowedUnitOfWork:
    """Expose the loader-owned transaction to normal V2 application commands."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def _assert_target_control_scope(
    factory: sessionmaker[Session],
    *,
    snapshot_id: str,
    manifest_hash: bytes,
) -> None:
    with factory() as session:
        actual_revision, target_counts = read_target_state(session.connection())
        expected_revision = current_v2_head()
        if actual_revision != expected_revision:
            raise ValueError(
                "target is not at the exact V2 Alembic head "
                f"(expected {expected_revision}, found {actual_revision})"
            )
        observed_snapshot_ids: set[str] = set()
        for model in (
            BackfillSourceReceiptRecord,
            BackfillCheckpointRecord,
            BackfillQuarantineRecord,
            BackfillSealRecord,
        ):
            observed_snapshot_ids.update(
                str(value)
                for value in session.scalars(select(model.snapshot_id).distinct())
            )
        foreign = sorted(observed_snapshot_ids - {snapshot_id})
        if foreign:
            raise BackfillChangedSourceError(
                "target contains foreign backfill snapshot control state"
            )
        if not observed_snapshot_ids:
            validate_target_state(
                actual_revision=actual_revision,
                expected_revision=expected_revision,
                row_counts=target_counts,
            )
        mismatched_checkpoint = session.scalar(
            select(func.count())
            .select_from(BackfillCheckpointRecord)
            .where(
                BackfillCheckpointRecord.snapshot_id == snapshot_id,
                BackfillCheckpointRecord.manifest_hash != manifest_hash,
            )
        )
        if mismatched_checkpoint:
            raise BackfillChangedSourceError(
                "snapshot checkpoint belongs to a different manifest"
            )
        mismatched_seal = session.scalar(
            select(func.count())
            .select_from(BackfillSealRecord)
            .where(
                BackfillSealRecord.snapshot_id == snapshot_id,
                BackfillSealRecord.manifest_hash != manifest_hash,
            )
        )
        if mismatched_seal:
            raise BackfillChangedSourceError(
                "snapshot seal belongs to a different manifest"
            )


def _assert_target_url_control_scope(
    target_url: str,
    *,
    snapshot_id: str,
    manifest_hash: bytes,
) -> None:
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        _assert_target_control_scope(
            sessionmaker(engine, expire_on_commit=False),
            snapshot_id=snapshot_id,
            manifest_hash=manifest_hash,
        )
    finally:
        engine.dispose()


def _source_key(table: str, row: Mapping[str, object]) -> str:
    columns = _PRIMARY_KEYS.get(table, ())
    values = [row.get(column) for column in columns]
    return canonical_json_bytes(values if columns else canonicalize_row(row)).decode(
        "utf-8"
    )


def _row_hash(row: Mapping[str, object]) -> bytes:
    return sha256(canonical_json_bytes(canonicalize_row(row))).digest()


def _source_actor_hash(value: object, *, primary_key: str) -> str:
    if type(value) is not str or not value.strip():
        raise BackfillMappingError(
            "classification_events",
            primary_key,
            "invalid_catalog_value",
            "created_by must be nonblank",
        )
    return sha256(_SOURCE_ACTOR_HASH_DOMAIN_V1 + value.encode("utf-8")).hexdigest()


def _classification_value_hash(value: Mapping[str, object]) -> str:
    return sha256(canonical_json_bytes(canonicalize_row(value))).hexdigest()


def _classification_snapshot_state(
    value: Mapping[str, object],
) -> tuple[str, str, str, str, bytes]:
    return (
        str(value["transaction_id"]),
        str(value["line_id"]),
        str(value["category_id"]),
        str(value["category_version_id"]),
        canonical_json_bytes(value.get("category_path_snapshot")),
    )


def _book_id(source_book_id: object) -> UUID:
    return deterministic_uuid("book", str(source_book_id))


def _account_id(source_book_id: object, source_account_id: object) -> UUID:
    return deterministic_uuid("account", str(source_book_id), str(source_account_id))


def _category_id(source_book_id: object, source_category_id: object) -> UUID:
    return deterministic_uuid("category", str(source_book_id), str(source_category_id))


def _category_version_id(
    source_book_id: object, source_category_version_id: object
) -> UUID:
    return deterministic_uuid(
        "category_version",
        str(source_book_id),
        str(source_category_version_id),
    )


def _transaction_id(
    snapshot_id: str, source_book_id: object, source_transaction_id: object
) -> UUID:
    return deterministic_uuid(
        "transaction",
        snapshot_id,
        str(source_book_id),
        str(source_transaction_id),
    )


def _parse_time(value: object, *, table: str, primary_key: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise BackfillMappingError(
                table, primary_key, "invalid_time", "timestamp is not ISO-8601"
            ) from None
    else:
        raise BackfillMappingError(
            table, primary_key, "invalid_time", "timestamp has an invalid type"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BackfillMappingError(
            table, primary_key, "invalid_time", "timestamp must include an offset"
        )
    return parsed.astimezone(UTC)


def _units_to_amount(units: int, scale: int) -> str:
    digits = str(units).rjust(scale + 1, "0")
    if scale == 0:
        return digits
    whole, fraction = digits[:-scale], digits[-scale:]
    fraction = fraction.rstrip("0")
    return whole if not fraction else f"{whole}.{fraction}"


def _source_decimal(
    value: object,
    *,
    table: str,
    primary_key: str,
    field: str,
) -> ExactImportedDecimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BackfillMappingError(
            table, primary_key, "invalid_amount", f"{field} is not an exact decimal"
        ) from None
    if not parsed.is_finite() or parsed <= 0:
        raise BackfillMappingError(
            table, primary_key, "invalid_amount", f"{field} must be positive"
        )
    sign, digits, exponent = parsed.as_tuple()
    if sign:
        raise BackfillMappingError(
            table, primary_key, "invalid_amount", f"{field} must be unsigned"
        )
    unscaled = "".join(str(digit) for digit in digits) or "0"
    if exponent >= 0:
        unscaled += "0" * exponent
        scale = 0
    else:
        scale = -exponent
    unscaled = str(int(unscaled))
    if scale > 30 or len(unscaled) > 38:
        raise BackfillMappingError(
            table, primary_key, "amount_overflow", f"{field} exceeds V2 bounds"
        )
    return ExactImportedDecimal(unscaled_units=unscaled, scale=scale)


def _append_historical_event(
    session: Session,
    *,
    book_id: UUID,
    pending: PendingEvent,
) -> None:
    committer = LedgerCommitter()
    locked_head = committer.execute_under_book_lock(session, book_id)
    committer.append_and_project(
        session,
        locked_head=locked_head,
        expected_stream_versions={pending.stream_key: 0},
        events=(pending,),
    )


def _transaction_kind(purpose: object) -> TransactionKind:
    normalized = str(purpose or "").casefold()
    if "opening" in normalized:
        return TransactionKind.OPENING
    if "transfer" in normalized:
        return TransactionKind.TRANSFER
    if "adjust" in normalized:
        return TransactionKind.ADJUSTMENT
    return TransactionKind.STANDARD


def _line_kind(value: object) -> ReportingLineKind:
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


def _line_category_source_id(
    line: Mapping[str, object],
    category_versions: Mapping[str, Mapping[str, object]],
) -> str | None:
    category_id = line.get("category_id")
    if category_id is not None:
        return str(category_id)
    version_id = line.get("category_version_id")
    if version_id is None:
        return None
    version = category_versions.get(str(version_id))
    return None if version is None else str(version["category_id"])


def _line_catalog_version_id(line: Mapping[str, object]) -> UUID:
    source_version = line.get("category_version_id")
    if source_version is None:
        source_version = f"line-snapshot:{line['line_id']}"
    return _category_version_id(line["book_id"], source_version)


def _historical_reporting_line_kind(
    line: Mapping[str, object],
) -> HistoricalReportingLineKind | None:
    if (
        line.get("category_id") is not None
        or line.get("category_version_id") is not None
    ):
        return None
    try:
        return HistoricalReportingLineKind(str(line.get("line_type")))
    except ValueError:
        return None


def _posting_fact(
    row: Mapping[str, object],
    *,
    transaction: Mapping[str, object],
    scales: Mapping[str, int],
) -> tuple[UUID, UUID, str, PostingSide, int]:
    table = "postings"
    primary_key = _source_key(table, row)
    asset_code = str(row.get("currency", ""))
    if asset_code not in scales:
        raise BackfillMappingError(
            table, primary_key, "unknown_asset", "posting asset is absent"
        )
    source_book_id = str(transaction["book_id"])
    semantics = row.get("amount_semantics")
    if semantics == "legacy_signed":
        normalized = normalize_legacy_signed_posting(
            source_book_id=source_book_id,
            source_transaction_id=str(row["transaction_id"]),
            source_posting_id=str(row["id"]),
            source_account_id=str(row["account_id"]),
            asset_code=asset_code,
            amount=str(row["amount"]),
            ledger_scale=scales[asset_code],
            backfill_mode=True,
        )
        side = PostingSide(normalized.side)
        units = normalized.units
        posting_id = normalized.posting_id
        account_id = normalized.account_id
    elif semantics == "debit_credit":
        try:
            side = PostingSide(str(row.get("side")))
        except ValueError:
            raise BackfillMappingError(
                table,
                primary_key,
                "ambiguous_posting_semantics",
                "debit/credit posting has no valid side",
            ) from None
        units = decimal_to_units(
            str(row.get("amount")),
            asset_code=asset_code,
            ledger_scale=scales[asset_code],
            backfill_mode=True,
        )
        if units <= 0:
            raise BackfillMappingError(
                table,
                primary_key,
                "invalid_amount",
                "debit/credit amount must be positive",
            )
        posting_id = deterministic_uuid(
            "posting", source_book_id, str(row["transaction_id"]), str(row["id"])
        )
        account_id = _account_id(source_book_id, row["account_id"])
    else:
        raise BackfillMappingError(
            table,
            primary_key,
            "ambiguous_posting_semantics",
            "posting semantics are not recognized",
        )
    if len(str(units)) > 38:
        raise BackfillMappingError(
            table, primary_key, "amount_overflow", "V2 posting units exceed NUMERIC(38)"
        )
    return posting_id, account_id, asset_code, side, units


def _preflight_mapping(
    rows: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    for table in ("investment_valuations",):
        if rows.get(table):
            row = rows[table][0]
            raise BackfillMappingError(
                table,
                _source_key(table, row),
                "unsupported_investment_semantics",
                "V1 cash-flow/valuation rows do not identify the distinct "
                "instrument and settlement assets required by V2 lots",
            )

    for row in rows.get("investment_events", ()):
        key = _source_key("investment_events", row)
        try:
            HistoricalInvestmentActivityKind(str(row.get("event_type")))
        except ValueError:
            raise BackfillMappingError(
                "investment_events",
                key,
                "unsupported_investment_semantics",
                "investment activity must be buy or sell",
            ) from None
        version = row.get("version")
        if type(version) is not int or version < 1:
            raise BackfillMappingError(
                "investment_events",
                key,
                "invalid_catalog_value",
                "investment source version must be positive",
            )

    def require_nonblank(table: str, row: Mapping[str, object], field: str) -> str:
        value = row.get(field)
        if type(value) is not str or not value.strip():
            raise BackfillMappingError(
                table,
                _source_key(table, row),
                "invalid_catalog_value",
                f"{field} must be nonblank",
            )
        return value

    for row in rows.get("ledger_books", ()):
        require_nonblank("ledger_books", row, "book_id")
        require_nonblank("ledger_books", row, "name")
        require_nonblank("ledger_books", row, "base_currency")
    if rows.get("assets") and not rows.get("ledger_books"):
        first_asset = rows["assets"][0]
        raise BackfillMappingError(
            "assets",
            _source_key("assets", first_asset),
            "missing_book",
            "global assets require at least one imported Book authorization scope",
        )

    assets = {str(row["asset_code"]): row for row in rows.get("assets", ())}
    scales: dict[str, int] = {}
    for code, row in assets.items():
        require_nonblank("assets", row, "kind")
        require_nonblank("assets", row, "name")
        if not code or code != code.upper() or len(code) > 16:
            raise BackfillMappingError(
                "assets", _source_key("assets", row), "invalid_asset", "asset code"
            )
        scale = row.get("scale")
        display_scale = row.get("display_scale")
        if type(scale) is not int or not 0 <= scale <= 30:
            raise BackfillMappingError(
                "assets", _source_key("assets", row), "invalid_asset", "asset scale"
            )
        if type(display_scale) is not int or not 0 <= display_scale <= scale:
            raise BackfillMappingError(
                "assets",
                _source_key("assets", row),
                "invalid_asset",
                "asset display scale",
            )
        scales[code] = scale

    for row in rows.get("accounts", ()):
        require_nonblank("accounts", row, "account_id")
        require_nonblank("accounts", row, "name")
        require_nonblank("accounts", row, "type")
        require_nonblank("accounts", row, "currency")
    for row in rows.get("categories", ()):
        require_nonblank("categories", row, "category_id")
        require_nonblank("categories", row, "name")
    for row in rows.get("category_versions", ()):
        require_nonblank("category_versions", row, "category_version_id")
        require_nonblank("category_versions", row, "name")
    for row in rows.get("transactions", ()):
        _parse_time(
            row.get("occurred_at"),
            table="transactions",
            primary_key=_source_key("transactions", row),
        )

    transactions = {
        str(row["transaction_id"]): row for row in rows.get("transactions", ())
    }
    postings: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    lines: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    category_versions = {
        str(row["category_version_id"]): row
        for row in rows.get("category_versions", ())
    }
    for row in rows.get("postings", ()):
        postings[str(row["transaction_id"])].append(row)
    for row in rows.get("transaction_lines", ()):
        lines[str(row["transaction_id"])].append(row)

    source_line_ids = {
        (str(row["transaction_id"]), str(row["line_id"]))
        for row in rows.get("transaction_lines", ())
    }
    source_category_ids = {
        str(row["category_id"]) for row in rows.get("categories", ())
    }
    for row in rows.get("classification_events", ()):
        key = _source_key("classification_events", row)
        try:
            activity_kind = HistoricalCategoryActivityKind(str(row.get("event_type")))
        except ValueError:
            raise BackfillMappingError(
                "classification_events",
                key,
                "unsupported_classification_semantics",
                "classification activity must be create or reclassify",
            ) from None
        source_category_id = require_nonblank(
            "classification_events", row, "source_category_id"
        )
        if source_category_id not in source_category_ids:
            raise BackfillMappingError(
                "classification_events",
                key,
                "invalid_catalog_value",
                "source category is absent",
            )
        target_category_id = row.get("target_category_id")
        if activity_kind is HistoricalCategoryActivityKind.CREATE:
            if target_category_id is not None:
                raise BackfillMappingError(
                    "classification_events",
                    key,
                    "unsupported_classification_semantics",
                    "category create target must be null",
                )
        elif (
            type(target_category_id) is not str
            or not target_category_id.strip()
            or target_category_id not in source_category_ids
        ):
            raise BackfillMappingError(
                "classification_events",
                key,
                "unsupported_classification_semantics",
                "reclassification target category is required",
            )
        affected_line_count = row.get("affected_line_count")
        if type(affected_line_count) is not int or affected_line_count < 0:
            raise BackfillMappingError(
                "classification_events",
                key,
                "invalid_catalog_value",
                "affected_line_count must be nonnegative",
            )
        version = row.get("version")
        if type(version) is not int or version < 1:
            raise BackfillMappingError(
                "classification_events",
                key,
                "invalid_catalog_value",
                "classification source version must be positive",
            )
        _source_actor_hash(row.get("created_by"), primary_key=key)
        _parse_time(
            row.get("created_at"), table="classification_events", primary_key=key
        )
        snapshots: dict[str, Mapping[str, object]] = {}
        for field in ("before", "after", "rollback"):
            value = row.get(field)
            if not isinstance(value, Mapping):
                raise BackfillMappingError(
                    "classification_events",
                    key,
                    "unsupported_classification_semantics",
                    f"{field} must be an object",
                )
            snapshots[field] = value
        if activity_kind is HistoricalCategoryActivityKind.RECLASSIFY:
            for field in ("before", "after"):
                snapshot = snapshots[field]
                required = (
                    "transaction_id",
                    "line_id",
                    "category_id",
                    "category_version_id",
                )
                if any(
                    type(snapshot.get(name)) is not str
                    or not str(snapshot[name]).strip()
                    for name in required
                ):
                    raise BackfillMappingError(
                        "classification_events",
                        key,
                        "unsupported_classification_semantics",
                        f"{field} lacks line/category source identity",
                    )
                transaction_id = str(snapshot["transaction_id"])
                line_id = str(snapshot["line_id"])
                if (transaction_id, line_id) not in source_line_ids:
                    raise BackfillMappingError(
                        "classification_events",
                        key,
                        "unsupported_classification_semantics",
                        f"{field} references an absent transaction line",
                    )
                if str(snapshot["category_version_id"]) not in category_versions:
                    raise BackfillMappingError(
                        "classification_events",
                        key,
                        "unsupported_classification_semantics",
                        f"{field} references an absent category version",
                    )
            if (
                snapshots["before"]["transaction_id"]
                != snapshots["after"]["transaction_id"]
                or snapshots["before"]["line_id"] != snapshots["after"]["line_id"]
                or str(snapshots["before"]["category_id"]) != source_category_id
                or str(snapshots["after"]["category_id"]) != target_category_id
            ):
                raise BackfillMappingError(
                    "classification_events",
                    key,
                    "unsupported_classification_semantics",
                    "reclassification source/target snapshots disagree with the event",
                )

    reclassifications_by_line: dict[
        tuple[str, str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    for row in rows.get("classification_events", ()):
        if row.get("event_type") != HistoricalCategoryActivityKind.RECLASSIFY.value:
            continue
        after = row["after"]
        if not isinstance(after, Mapping):
            continue
        source_transaction_id = str(after["transaction_id"])
        transaction = transactions[source_transaction_id]
        event_time = _parse_time(
            row["created_at"],
            table="classification_events",
            primary_key=_source_key("classification_events", row),
        )
        transaction_time = _parse_time(
            transaction["occurred_at"],
            table="transactions",
            primary_key=_source_key("transactions", transaction),
        )
        if event_time < transaction_time:
            raise BackfillMappingError(
                "classification_events",
                _source_key("classification_events", row),
                "classification_precedes_transaction",
                "reclassification cannot precede its target transaction",
            )
        reclassifications_by_line[
            (str(row["book_id"]), source_transaction_id, str(after["line_id"]))
        ].append(row)

    source_lines = {
        (str(row["book_id"]), str(row["transaction_id"]), str(row["line_id"])): row
        for row in rows.get("transaction_lines", ())
    }
    for line_key, events in reclassifications_by_line.items():
        ordered = sorted(
            events,
            key=lambda row: (
                _parse_time(
                    row["created_at"],
                    table="classification_events",
                    primary_key=_source_key("classification_events", row),
                ),
                str(row["classification_event_id"]).encode("utf-8"),
            ),
        )
        current_after: tuple[str, str, str, str, bytes] | None = None
        previous_transition: (
            tuple[
                tuple[str, str, str, str, bytes],
                tuple[str, str, str, str, bytes],
            ]
            | None
        ) = None
        for event in ordered:
            before_value = event["before"]
            after_value = event["after"]
            if not isinstance(before_value, Mapping) or not isinstance(
                after_value, Mapping
            ):
                continue
            before = _classification_snapshot_state(before_value)
            after = _classification_snapshot_state(after_value)
            transition = (before, after)
            if (
                current_after is not None
                and before != current_after
                and transition != previous_transition
            ):
                raise BackfillMappingError(
                    "classification_events",
                    _source_key("classification_events", event),
                    "classification_chain_inconsistent",
                    "reclassification before-state does not follow prior after-state",
                )
            current_after = after
            previous_transition = transition
        source_line = source_lines[line_key]
        final_line_state = (
            str(source_line["transaction_id"]),
            str(source_line["line_id"]),
            str(source_line["category_id"]),
            str(source_line["category_version_id"]),
            canonical_json_bytes(source_line.get("category_path_snapshot")),
        )
        if final_line_state != current_after:
            raise BackfillMappingError(
                "transaction_lines",
                _source_key("transaction_lines", source_line),
                "classification_final_state_mismatch",
                "reclassification final after-state differs from current source line",
            )

    normalized_by_transaction: dict[
        str, list[tuple[UUID, UUID, str, PostingSide, int]]
    ] = {}
    for transaction_id, transaction in transactions.items():
        transaction_postings = postings.get(transaction_id, [])
        if len(transaction_postings) < 2:
            raise BackfillMappingError(
                "transactions",
                _source_key("transactions", transaction),
                "unbalanced_transaction",
                "transaction has fewer than two postings",
            )
        facts = [
            _posting_fact(row, transaction=transaction, scales=scales)
            for row in transaction_postings
        ]
        sums: dict[str, dict[PostingSide, int]] = defaultdict(
            lambda: {PostingSide.DEBIT: 0, PostingSide.CREDIT: 0}
        )
        for _, _, asset_code, side, units in facts:
            sums[asset_code][side] += units
        if any(
            sides[PostingSide.DEBIT] != sides[PostingSide.CREDIT]
            for sides in sums.values()
        ):
            raise BackfillMappingError(
                "transactions",
                _source_key("transactions", transaction),
                "unbalanced_transaction",
                "debit and credit units differ by asset",
            )
        normalized_by_transaction[transaction_id] = facts

        allocated: dict[str, int] = defaultdict(int)
        for line in lines.get(transaction_id, []):
            key = _source_key("transaction_lines", line)
            for field, supported_value in (
                ("counterparty_id", None),
                ("project_id", None),
                ("necessity", "unknown"),
                ("reimbursement_status", "none"),
            ):
                if line.get(field) != supported_value:
                    raise BackfillMappingError(
                        "transaction_lines",
                        key,
                        "unsupported_reporting_metadata",
                        f"{field} has a meaningful V1 value with no V2 mapping",
                    )
            if (
                _line_category_source_id(line, category_versions) is None
                and _historical_reporting_line_kind(line) is None
            ):
                raise BackfillMappingError(
                    "transaction_lines",
                    key,
                    "unsupported_reporting_dimension",
                    "V2 backfill requires a category or category-version identity",
                )
            code = str(line.get("currency", ""))
            if code not in scales:
                raise BackfillMappingError(
                    "transaction_lines", key, "unknown_asset", "line asset is absent"
                )
            units = decimal_to_units(
                str(line.get("amount")),
                asset_code=code,
                ledger_scale=scales[code],
                backfill_mode=True,
            )
            if units <= 0 or len(str(units)) > 38:
                raise BackfillMappingError(
                    "transaction_lines", key, "invalid_amount", "line units"
                )
            allocated[code] += units
        if any(
            units > sums.get(code, {PostingSide.DEBIT: 0})[PostingSide.DEBIT]
            for code, units in allocated.items()
        ):
            first = lines[transaction_id][0]
            raise BackfillMappingError(
                "transaction_lines",
                _source_key("transaction_lines", first),
                "reporting_overallocated",
                "reporting lines exceed transaction units",
            )

    for transaction_id, transaction in transactions.items():
        original_id = transaction.get("reverses_transaction_id")
        if original_id is None:
            continue
        original = normalized_by_transaction.get(str(original_id))
        reversal = normalized_by_transaction[transaction_id]
        if original is None or len(original) != len(reversal):
            raise BackfillMappingError(
                "transactions",
                _source_key("transactions", transaction),
                "reversal_inconsistent",
                "reversal postings do not match their source",
            )
        original_row = transactions[str(original_id)]
        if _parse_time(
            transaction["occurred_at"],
            table="transactions",
            primary_key=_source_key("transactions", transaction),
        ) < _parse_time(
            original_row["occurred_at"],
            table="transactions",
            primary_key=_source_key("transactions", original_row),
        ):
            raise BackfillMappingError(
                "transactions",
                _source_key("transactions", transaction),
                "reversal_precedes_original",
                "reversal cannot precede its original transaction",
            )
        expected = sorted(
            (
                account_id,
                asset_code,
                PostingSide.CREDIT if side is PostingSide.DEBIT else PostingSide.DEBIT,
                units,
            )
            for _, account_id, asset_code, side, units in original
        )
        actual = sorted(
            (account_id, asset_code, side, units)
            for _, account_id, asset_code, side, units in reversal
        )
        if expected != actual:
            raise BackfillMappingError(
                "transactions",
                _source_key("transactions", transaction),
                "reversal_inconsistent",
                "reversal postings are not the exact inverse",
            )


def _aggregate_schedule(
    rows: Mapping[str, Sequence[Mapping[str, object]]],
) -> tuple[_ScheduledAggregate, ...]:
    NodeId = tuple[str, int, str]
    nodes: dict[NodeId, tuple[str, Mapping[str, object], datetime, bytes]] = {}
    dependencies: dict[NodeId, set[NodeId]] = defaultdict(set)
    transaction_nodes: dict[tuple[str, str], NodeId] = {}
    reclass_nodes_by_line: dict[tuple[str, str, str], list[NodeId]] = defaultdict(list)

    for row in rows["transactions"]:
        book = str(row["book_id"])
        source_id = str(row["transaction_id"])
        node_id = (book, 0, source_id)
        transaction_nodes[(book, source_id)] = node_id
        nodes[node_id] = (
            "transaction",
            row,
            _parse_time(
                row["occurred_at"],
                table="transactions",
                primary_key=_source_key("transactions", row),
            ),
            source_id.encode("utf-8"),
        )
    for row in rows["transactions"]:
        original = row.get("reverses_transaction_id")
        if original is not None:
            node_id = (str(row["book_id"]), 0, str(row["transaction_id"]))
            dependencies[node_id].add(
                transaction_nodes[(str(row["book_id"]), str(original))]
            )

    for row in rows["classification_events"]:
        book = str(row["book_id"])
        source_id = str(row["classification_event_id"])
        node_id = (book, 1, source_id)
        nodes[node_id] = (
            "classification",
            row,
            _parse_time(
                row["created_at"],
                table="classification_events",
                primary_key=_source_key("classification_events", row),
            ),
            source_id.encode("utf-8"),
        )
        if row.get("event_type") == HistoricalCategoryActivityKind.RECLASSIFY.value:
            after = row["after"]
            if isinstance(after, Mapping):
                transaction_id = str(after["transaction_id"])
                dependencies[node_id].add(transaction_nodes[(book, transaction_id)])
                reclass_nodes_by_line[
                    (book, transaction_id, str(after["line_id"]))
                ].append(node_id)
    for line_nodes in reclass_nodes_by_line.values():
        line_nodes.sort(key=lambda node_id: (nodes[node_id][2], nodes[node_id][3]))
        for previous, current in zip(line_nodes, line_nodes[1:], strict=False):
            dependencies[current].add(previous)

    for row in rows["investment_events"]:
        book = str(row["book_id"])
        source_id = str(row["event_id"])
        node_id = (book, 2, source_id)
        nodes[node_id] = (
            "investment",
            row,
            _parse_time(
                row["occurred_at"],
                table="investment_events",
                primary_key=_source_key("investment_events", row),
            ),
            source_id.encode("utf-8"),
        )

    remaining = set(nodes)
    emitted: set[NodeId] = set()
    ordered: list[_ScheduledAggregate] = []
    while remaining:
        ready = [node_id for node_id in remaining if dependencies[node_id] <= emitted]
        if not ready:
            node_id = min(remaining)
            kind, row, _, _ = nodes[node_id]
            table = {
                "transaction": "transactions",
                "classification": "classification_events",
                "investment": "investment_events",
            }[kind]
            raise BackfillMappingError(
                table,
                _source_key(table, row),
                "canonical_schedule_cycle",
                "source dependencies cannot be ordered",
            )
        selected = min(
            ready,
            key=lambda node_id: (
                _book_id(node_id[0]).bytes,
                nodes[node_id][2],
                nodes[node_id][3],
                node_id[1],
            ),
        )
        kind, row, _, _ = nodes[selected]
        ordered.append(_ScheduledAggregate(kind=kind, row=row, rank=len(ordered)))
        emitted.add(selected)
        remaining.remove(selected)
    return tuple(ordered)


def _item(
    table: str,
    row: Mapping[str, object],
    *,
    canonical_key: str | None = None,
    target_id: UUID | None = None,
) -> SourceLoadItem:
    source_book = row.get("book_id")
    return SourceLoadItem(
        source_table=table,
        source_primary_key=_source_key(table, row),
        canonical_source_key=canonical_key or _source_key(table, row),
        source_hash=_row_hash(row),
        book_id=None if source_book is None else _book_id(source_book),
        target_entity_id=target_id,
        payload=dict(row),
    )


def load_extracted_rows_to_target(
    *,
    target_url: str,
    manifest: FrozenSourceManifest,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> BackfillPipelineResult:
    rows = {table: tuple(rows_by_table.get(table, ())) for table in _TABLE_ORDER}
    manifest_hash_hex = manifest.content_sha256 or manifest.calculated_content_sha256()
    manifest_hash = bytes.fromhex(manifest_hash_hex)
    snapshot_id = manifest.snapshot_id or f"sha256:{manifest_hash_hex}"
    _assert_target_url_control_scope(
        target_url,
        snapshot_id=snapshot_id,
        manifest_hash=manifest_hash,
    )
    declared_counts = {table.table: table.row_count for table in manifest.tables}
    actual_counts = {table: len(values) for table, values in rows.items()}
    if declared_counts and declared_counts != actual_counts:
        raise ValueError("extracted source counts do not match the manifest")
    inventory = inventory_rows(rows)
    if not inventory.ok:
        issue = inventory.issues[0]
        raise BackfillMappingError(
            issue.source_table,
            issue.source_primary_key,
            issue.code,
            issue.relation,
        )
    _preflight_mapping(rows)

    actor = CommandActor(subject_id=f"backfill:{manifest_hash_hex[:32]}")
    engine = create_engine(target_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    _assert_target_control_scope(
        factory,
        snapshot_id=snapshot_id,
        manifest_hash=manifest_hash,
    )
    totals = {"applied": 0, "replayed": 0}

    def collect(result: LoadResult) -> None:
        totals["applied"] += result.applied
        totals["replayed"] += result.replayed

    def loader(apply_item: Callable[[Session, SourceLoadItem], None]):
        return ResumableBackfillLoader(
            factory,
            snapshot_id=snapshot_id,
            manifest_hash=manifest_hash,
            apply_item=apply_item,
        )

    try:

        def ensure_actor(session: Session) -> None:
            if session.get(UserRecord, actor.subject_id) is not None:
                return
            session.add(
                UserRecord(
                    user_id=actor.subject_id,
                    subject_type="machine",
                    current_display_name="V1 deterministic backfill",
                    status="active",
                )
            )
            session.flush()

        def apply_book(session: Session, item: SourceLoadItem) -> None:
            row = item.payload
            ensure_actor(session)
            create_book(
                CreateBook(
                    book_id=_book_id(row["book_id"]),
                    current_name=str(row["name"]),
                    base_asset_code=None,
                ),
                actor=actor,
                uow_factory=lambda: _BorrowedUnitOfWork(session),
            )

        collect(
            loader(apply_book).load(
                tuple(
                    _item("ledger_books", row, target_id=_book_id(row["book_id"]))
                    for row in rows["ledger_books"]
                )
            )
        )

        first_book = min(
            (_book_id(row["book_id"]) for row in rows["ledger_books"]),
            key=str,
            default=None,
        )

        def apply_asset(session: Session, item: SourceLoadItem) -> None:
            if first_book is None:
                raise BackfillRunBlocked("assets cannot be imported without a Book")
            row = item.payload
            scale = int(row["scale"])
            create_asset(
                CreateAsset(
                    book_id=first_book,
                    asset_code=str(row["asset_code"]),
                    kind=str(row["kind"]),
                    ledger_scale=scale,
                    input_scale=scale,
                    display_scale=int(row["display_scale"]),
                    current_name=str(row["name"]),
                ),
                actor=actor,
                uow_factory=lambda: _BorrowedUnitOfWork(session),
            )

        collect(
            loader(apply_asset).load(
                tuple(_item("assets", row) for row in rows["assets"])
            )
        )

        source_accounts = {
            (str(row["book_id"]), str(row["account_id"])): row
            for row in rows["accounts"]
        }
        source_postings_by_transaction: dict[str, list[Mapping[str, object]]] = (
            defaultdict(list)
        )
        source_lines_by_transaction: dict[str, list[Mapping[str, object]]] = (
            defaultdict(list)
        )
        for row in rows["postings"]:
            source_postings_by_transaction[str(row["transaction_id"])].append(row)
        for row in rows["transaction_lines"]:
            source_lines_by_transaction[str(row["transaction_id"])].append(row)

        pure_fx_transaction_ids: set[str] = set()
        fx_trading_source_accounts: set[tuple[str, str]] = set()
        trading_account_by_asset: dict[tuple[str, str], str] = {}
        for transaction in rows["transactions"]:
            source_transaction_id = str(transaction["transaction_id"])
            source_book_id = str(transaction["book_id"])
            transaction_postings = source_postings_by_transaction[source_transaction_id]
            if (
                transaction.get("reverses_transaction_id") is not None
                or len(transaction_postings) != 4
                or not any(
                    _historical_reporting_line_kind(line)
                    is HistoricalReportingLineKind.FX_EXCHANGE
                    for line in source_lines_by_transaction[source_transaction_id]
                )
            ):
                continue
            postings_by_asset: dict[str, list[Mapping[str, object]]] = defaultdict(list)
            for posting in transaction_postings:
                postings_by_asset[str(posting["currency"])].append(posting)
            if len(postings_by_asset) != 2 or any(
                len(asset_postings) != 2
                for asset_postings in postings_by_asset.values()
            ):
                continue
            trading_rows: list[Mapping[str, object]] = []
            valid_shape = True
            for asset_code, asset_postings in postings_by_asset.items():
                system_postings = [
                    posting
                    for posting in asset_postings
                    if str(
                        source_accounts[(source_book_id, str(posting["account_id"]))][
                            "type"
                        ]
                    )
                    == "system"
                ]
                if len(system_postings) != 1:
                    valid_shape = False
                    break
                system_posting = system_postings[0]
                user_posting = next(
                    posting
                    for posting in asset_postings
                    if posting is not system_posting
                )
                if system_posting["side"] == user_posting["side"]:
                    valid_shape = False
                    break
                source_system_account = source_accounts[
                    (source_book_id, str(system_posting["account_id"]))
                ]
                if str(source_system_account["currency"]) != asset_code:
                    valid_shape = False
                    break
                trading_rows.append(source_system_account)
            trading_ids = {str(row["account_id"]) for row in trading_rows}
            trading_sides = {
                str(posting["side"])
                for posting in transaction_postings
                if str(posting["account_id"]) in trading_ids
            }
            if not valid_shape or trading_sides != {"debit", "credit"}:
                continue
            pure_fx_transaction_ids.add(source_transaction_id)
            for account_row in trading_rows:
                source_account_id = str(account_row["account_id"])
                asset_key = (source_book_id, str(account_row["currency"]))
                existing = trading_account_by_asset.setdefault(
                    asset_key, source_account_id
                )
                if existing != source_account_id:
                    raise BackfillMappingError(
                        "accounts",
                        _source_key("accounts", account_row),
                        "unsupported_fx_semantics",
                        "multiple source FX trading accounts share a Book asset",
                    )
                fx_trading_source_accounts.add((source_book_id, source_account_id))

        def apply_account(session: Session, item: SourceLoadItem) -> None:
            row = item.payload
            create_account(
                CreateAccount(
                    book_id=_book_id(row["book_id"]),
                    account_id=_account_id(row["book_id"], row["account_id"]),
                    asset_code=str(row["currency"]),
                    account_type=str(row["type"]),
                    current_name=str(row["name"]),
                    system_role=(
                        "fx_trading"
                        if (str(row["book_id"]), str(row["account_id"]))
                        in fx_trading_source_accounts
                        else None
                    ),
                ),
                actor=actor,
                uow_factory=lambda: _BorrowedUnitOfWork(session),
            )

        collect(
            loader(apply_account).load(
                tuple(
                    _item(
                        "accounts",
                        row,
                        target_id=_account_id(row["book_id"], row["account_id"]),
                    )
                    for row in rows["accounts"]
                )
            )
        )

        versions_by_category: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for version in rows["category_versions"]:
            versions_by_category[str(version["category_id"])].append(version)

        def current_version(
            category: Mapping[str, object],
        ) -> Mapping[str, object] | None:
            versions = versions_by_category.get(str(category["category_id"]), [])
            if not versions:
                return None
            active = [
                version for version in versions if version.get("valid_to") is None
            ]
            candidates = active or versions
            return max(
                candidates,
                key=lambda value: (
                    str(value.get("valid_from", "")),
                    str(value["category_version_id"]),
                ),
            )

        def apply_category(session: Session, item: SourceLoadItem) -> None:
            row = item.payload
            version = current_version(row)
            source_version_id = (
                f"synthetic:{row['category_id']}"
                if version is None
                else str(version["category_version_id"])
            )
            parent = (
                row.get("parent_id") if version is None else version.get("parent_id")
            )
            name = row.get("name") if version is None else version.get("name")
            create_category(
                CreateCategory(
                    book_id=_book_id(row["book_id"]),
                    category_id=_category_id(row["book_id"], row["category_id"]),
                    category_version_id=_category_version_id(
                        row["book_id"], source_version_id
                    ),
                    name=str(name),
                    parent_category_id=(
                        None if parent is None else _category_id(row["book_id"], parent)
                    ),
                    change_reason_code=(
                        "backfill_current"
                        if version is None
                        else str(version.get("change_reason") or "backfill")[:64]
                    ),
                ),
                actor=actor,
                uow_factory=lambda: _BorrowedUnitOfWork(session),
            )

        categories = sorted(
            rows["categories"],
            key=lambda value: (
                int(value.get("level", 0)),
                str(value["book_id"]),
                str(value["category_id"]),
            ),
        )
        collect(
            loader(apply_category).load(
                tuple(
                    _item(
                        "categories",
                        row,
                        canonical_key=f"{int(row.get('level', 0)):04d}:{_source_key('categories', row)}",
                        target_id=_category_id(row["book_id"], row["category_id"]),
                    )
                    for row in categories
                )
            )
        )

        category_rows = {str(row["category_id"]): row for row in rows["categories"]}
        source_category_versions = {
            str(row["category_version_id"]): row for row in rows["category_versions"]
        }

        def apply_category_version(session: Session, item: SourceLoadItem) -> None:
            row = item.payload
            book_id = _book_id(row["book_id"])
            category_id = _category_id(row["book_id"], row["category_id"])
            version_id = _category_version_id(
                row["book_id"], row["category_version_id"]
            )
            existing = session.get(
                CategoryVersionRecord, (book_id, category_id, version_id)
            )
            parent = row.get("parent_id")
            source_category = category_rows[str(row["category_id"])]
            version_status = (
                "archived"
                if row.get("valid_to") is not None
                or str(source_category.get("status")) == "archived"
                else "active"
            )
            parent_category_id = (
                None if parent is None else _category_id(row["book_id"], parent)
            )
            change_reason = str(row.get("change_reason") or "backfill")[:64]
            if existing is None:
                session.add(
                    CategoryVersionRecord(
                        book_id=book_id,
                        category_id=category_id,
                        category_version_id=version_id,
                        parent_category_id=parent_category_id,
                        name=str(row["name"]),
                        status=version_status,
                        change_reason_code=change_reason,
                    )
                )
            else:
                existing.parent_category_id = parent_category_id
                existing.name = str(row["name"])
                existing.status = version_status
                existing.change_reason_code = change_reason
            category = session.get(CategoryRecord, (book_id, category_id))
            if category is None:
                raise BackfillRunBlocked("category disappeared during version import")
            chosen = current_version(source_category)
            if chosen is not None and str(chosen["category_version_id"]) == str(
                row["category_version_id"]
            ):
                category.current_version_id = version_id
                category.current_name = str(row["name"])
                category.parent_category_id = (
                    None
                    if row.get("parent_id") is None
                    else _category_id(row["book_id"], row["parent_id"])
                )
            session.flush()

        collect(
            loader(apply_category_version).load(
                tuple(
                    _item(
                        "category_versions",
                        row,
                        canonical_key=(
                            f"{str(row.get('valid_from', ''))}:"
                            f"{_source_key('category_versions', row)}"
                        ),
                        target_id=_category_version_id(
                            row["book_id"], row["category_version_id"]
                        ),
                    )
                    for row in rows["category_versions"]
                )
            )
        )

        scales = {str(row["asset_code"]): int(row["scale"]) for row in rows["assets"]}
        postings_by_transaction: dict[str, list[Mapping[str, object]]] = defaultdict(
            list
        )
        lines_by_transaction: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in rows["postings"]:
            postings_by_transaction[str(row["transaction_id"])].append(row)
        for row in rows["transaction_lines"]:
            lines_by_transaction[str(row["transaction_id"])].append(row)

        aggregate_schedule = _aggregate_schedule(rows)
        scheduled_transactions = tuple(
            aggregate
            for aggregate in aggregate_schedule
            if aggregate.kind == "transaction"
        )
        scheduled_classifications = tuple(
            aggregate
            for aggregate in aggregate_schedule
            if aggregate.kind == "classification"
        )
        scheduled_investments = tuple(
            aggregate
            for aggregate in aggregate_schedule
            if aggregate.kind == "investment"
        )
        ordered_classification_events = tuple(
            aggregate.row for aggregate in scheduled_classifications
        )
        initial_classification_by_line: dict[tuple[str, str], Mapping[str, object]] = {}
        for row in ordered_classification_events:
            if row["event_type"] != HistoricalCategoryActivityKind.RECLASSIFY.value:
                continue
            before = row["before"]
            if not isinstance(before, Mapping):
                raise BackfillRunBlocked(
                    "classification snapshot disappeared after preflight"
                )
            initial_classification_by_line.setdefault(
                (str(before["transaction_id"]), str(before["line_id"])),
                before,
            )

        def initial_reporting_line(
            line: Mapping[str, object],
        ) -> Mapping[str, object]:
            initial = initial_classification_by_line.get(
                (str(line["transaction_id"]), str(line["line_id"]))
            )
            if initial is None:
                return line
            restored = dict(line)
            restored["category_id"] = initial["category_id"]
            restored["category_version_id"] = initial["category_version_id"]
            restored["category_path_snapshot"] = initial.get("category_path_snapshot")
            return restored

        scheduled_loads: list[tuple[int, Callable[[], None]]] = []
        for scheduled_transaction in scheduled_transactions:
            sequence = scheduled_transaction.rank
            transaction = scheduled_transaction.row
            source_transaction_id = str(transaction["transaction_id"])
            transaction_postings = sorted(
                postings_by_transaction[source_transaction_id],
                key=lambda value: (int(value["position"]), str(value["id"])),
            )
            source_transaction_lines = sorted(
                lines_by_transaction[source_transaction_id],
                key=lambda value: (int(value["position"]), str(value["line_id"])),
            )
            transaction_lines = tuple(
                initial_reporting_line(line) for line in source_transaction_lines
            )
            category_lines = tuple(
                line
                for line in transaction_lines
                if _historical_reporting_line_kind(line) is None
            )
            historical_reporting_lines = tuple(
                line
                for line in transaction_lines
                if _historical_reporting_line_kind(line) is not None
            )
            prefix = f"{sequence:012d}"
            group = [
                _item(
                    "transactions",
                    transaction,
                    canonical_key=f"{prefix}:000000:transaction",
                    target_id=_transaction_id(
                        snapshot_id,
                        transaction["book_id"],
                        transaction["transaction_id"],
                    ),
                )
            ]
            group.extend(
                _item(
                    "postings",
                    row,
                    canonical_key=f"{prefix}:100000:{position:06d}",
                )
                for position, row in enumerate(transaction_postings)
            )
            group.extend(
                _item(
                    "transaction_lines",
                    row,
                    canonical_key=f"{prefix}:200000:{position:06d}",
                    target_id=deterministic_uuid(
                        "line",
                        str(transaction["book_id"]),
                        source_transaction_id,
                        str(row["line_id"]),
                    ),
                )
                for position, row in enumerate(source_transaction_lines)
            )

            def apply_transaction_group(
                session: Session,
                _items: tuple[SourceLoadItem, ...],
                *,
                transaction=transaction,
                transaction_postings=transaction_postings,
                category_lines=category_lines,
                historical_reporting_lines=historical_reporting_lines,
                source_transaction_id=source_transaction_id,
            ) -> None:
                book_id = _book_id(transaction["book_id"])
                target_transaction_id = _transaction_id(
                    snapshot_id,
                    transaction["book_id"],
                    transaction["transaction_id"],
                )
                effective_at = _parse_time(
                    transaction["occurred_at"],
                    table="transactions",
                    primary_key=_source_key("transactions", transaction),
                )

                def borrowed() -> _BorrowedUnitOfWork:
                    return _BorrowedUnitOfWork(session)

                if transaction.get("reverses_transaction_id") is None:
                    if source_transaction_id in pure_fx_transaction_ids:
                        posting_facts: list[JournalPostingFact] = []
                        for position, posting in enumerate(transaction_postings):
                            posting_id, account_id, asset_code, side, units = (
                                _posting_fact(
                                    posting,
                                    transaction=transaction,
                                    scales=scales,
                                )
                            )
                            posting_facts.append(
                                JournalPostingFact(
                                    posting_id=posting_id,
                                    position=position,
                                    account_id=account_id,
                                    asset_code=asset_code,
                                    side=side,
                                    units=str(units),
                                )
                            )
                        command_id = deterministic_uuid(
                            "command",
                            snapshot_id,
                            str(transaction["book_id"]),
                            source_transaction_id,
                            "journal.fx",
                        )
                        _append_historical_event(
                            session,
                            book_id=book_id,
                            pending=PendingEvent(
                                event_id=deterministic_uuid(
                                    "event",
                                    snapshot_id,
                                    str(transaction["book_id"]),
                                    source_transaction_id,
                                    "journal.fx",
                                ),
                                stream_type="journal_transaction",
                                stream_id=target_transaction_id,
                                payload=JournalTransactionPosted(
                                    transaction_id=target_transaction_id,
                                    kind=TransactionKind.FX,
                                    postings=tuple(posting_facts),
                                ),
                                command_id=command_id,
                                actor_subject_id=actor.subject_id,
                                correlation_id=command_id,
                                causation_event_id=None,
                                effective_at=effective_at,
                            ),
                        )
                    else:
                        posting_inputs: list[PostTransactionPosting] = []
                        for posting in transaction_postings:
                            posting_id, account_id, asset_code, side, units = (
                                _posting_fact(
                                    posting,
                                    transaction=transaction,
                                    scales=scales,
                                )
                            )
                            posting_inputs.append(
                                PostTransactionPosting(
                                    posting_id=posting_id,
                                    account_id=account_id,
                                    asset_code=asset_code,
                                    side=side,
                                    amount=_units_to_amount(units, scales[asset_code]),
                                )
                            )
                        command_id = deterministic_uuid(
                            "command",
                            snapshot_id,
                            str(transaction["book_id"]),
                            source_transaction_id,
                            "journal.post",
                        )
                        execute_post_transaction(
                            PostTransactionCommand(
                                book_id=book_id,
                                command_id=command_id,
                                transaction_id=target_transaction_id,
                                expected_stream_version=0,
                                kind=_transaction_kind(transaction.get("purpose")),
                                postings=tuple(posting_inputs),
                                effective_at=effective_at,
                            ),
                            raw_key=(
                                f"backfill:{snapshot_id}:journal:"
                                f"{source_transaction_id}"
                            ),
                            actor=actor,
                            uow_factory=borrowed,
                            max_attempts=1,
                        )
                else:
                    command_id = deterministic_uuid(
                        "command",
                        snapshot_id,
                        str(transaction["book_id"]),
                        source_transaction_id,
                        "journal.reverse",
                    )
                    execute_reverse_transaction(
                        ReverseTransactionCommand(
                            book_id=book_id,
                            command_id=command_id,
                            reversal_transaction_id=target_transaction_id,
                            reverses_transaction_id=_transaction_id(
                                snapshot_id,
                                transaction["book_id"],
                                transaction["reverses_transaction_id"],
                            ),
                            expected_stream_version=0,
                            reason_code=ReversalReasonCode.IMPORT_CORRECTION,
                            effective_at=effective_at,
                        ),
                        raw_key=f"backfill:{snapshot_id}:reverse:{source_transaction_id}",
                        actor=actor,
                        uow_factory=borrowed,
                        max_attempts=1,
                    )

                journal_record = session.get(
                    JournalTransactionRecord,
                    (book_id, target_transaction_id),
                )
                if journal_record is None:
                    raise BackfillRunBlocked(
                        "journal projection disappeared during historical import"
                    )
                parent_journal_event_id = journal_record.source_event_id

                if category_lines:
                    reporting_inputs: list[ReportingLineInput] = []
                    for line in category_lines:
                        asset_code = str(line["currency"])
                        units = decimal_to_units(
                            str(line["amount"]),
                            asset_code=asset_code,
                            ledger_scale=scales[asset_code],
                            backfill_mode=True,
                        )
                        source_line_id = str(line["line_id"])
                        source_category_id = _line_category_source_id(
                            line, source_category_versions
                        )
                        if source_category_id is None:
                            raise BackfillRunBlocked(
                                "reporting category disappeared after preflight"
                            )
                        catalog_id = _line_catalog_version_id(line)
                        if line.get("category_version_id") is None:
                            source_category = category_rows[source_category_id]
                            target_book_id = _book_id(line["book_id"])
                            target_category_id = _category_id(
                                line["book_id"], source_category_id
                            )
                            existing_snapshot = session.get(
                                CategoryVersionRecord,
                                (target_book_id, target_category_id, catalog_id),
                            )
                            if existing_snapshot is None:
                                snapshot = line.get("category_path_snapshot")
                                snapshot_name: object | None = None
                                if isinstance(snapshot, Mapping):
                                    snapshot_name = (
                                        snapshot.get("secondary")
                                        or snapshot.get("primary")
                                        or snapshot.get("name")
                                    )
                                snapshot_name = snapshot_name or source_category["name"]
                                source_parent = source_category.get("parent_id")
                                session.add(
                                    CategoryVersionRecord(
                                        book_id=target_book_id,
                                        category_id=target_category_id,
                                        category_version_id=catalog_id,
                                        parent_category_id=(
                                            None
                                            if source_parent is None
                                            else _category_id(
                                                line["book_id"], source_parent
                                            )
                                        ),
                                        name=str(snapshot_name),
                                        status="archived",
                                        change_reason_code="backfill_line_snapshot",
                                    )
                                )
                                session.flush()
                        reporting_inputs.append(
                            ReportingLineInput(
                                line_id=deterministic_uuid(
                                    "line",
                                    str(transaction["book_id"]),
                                    source_transaction_id,
                                    source_line_id,
                                ),
                                line_version_id=deterministic_uuid(
                                    "line_version",
                                    str(transaction["book_id"]),
                                    source_transaction_id,
                                    source_line_id,
                                    str(line.get("version", 1)),
                                ),
                                catalog_id=catalog_id,
                                asset_code=asset_code,
                                units=str(units),
                                line_kind=_line_kind(line.get("line_type")),
                                dimension=ReportingDimension.CATEGORY,
                                dimension_id=_category_id(
                                    transaction["book_id"], source_category_id
                                ),
                            )
                        )
                    reporting_command_id = deterministic_uuid(
                        "command",
                        snapshot_id,
                        str(transaction["book_id"]),
                        source_transaction_id,
                        "reporting.assign",
                    )
                    execute_assign_reporting_lines(
                        AssignReportingLinesCommand(
                            book_id=book_id,
                            command_id=reporting_command_id,
                            transaction_id=target_transaction_id,
                            expected_revision=0,
                            lines=tuple(reporting_inputs),
                            effective_at=effective_at,
                        ),
                        raw_key=f"backfill:{snapshot_id}:reporting:{source_transaction_id}",
                        actor=actor,
                        uow_factory=borrowed,
                        max_attempts=1,
                    )

                for line in historical_reporting_lines:
                    source_line_id = str(line["line_id"])
                    source_key = _source_key("transaction_lines", line)
                    asset_code = str(line["currency"])
                    source_amount = _source_decimal(
                        line["amount"],
                        table="transaction_lines",
                        primary_key=source_key,
                        field="amount",
                    )
                    line_kind = _historical_reporting_line_kind(line)
                    if line_kind is None:
                        raise BackfillRunBlocked(
                            "historical reporting line disappeared after preflight"
                        )
                    historical_event_id = deterministic_uuid(
                        "event",
                        snapshot_id,
                        str(transaction["book_id"]),
                        source_transaction_id,
                        source_line_id,
                        "historical-reporting-line",
                    )
                    historical_stream_id = deterministic_uuid(
                        "line",
                        str(transaction["book_id"]),
                        source_transaction_id,
                        source_line_id,
                    )
                    historical_command_id = deterministic_uuid(
                        "command",
                        snapshot_id,
                        str(transaction["book_id"]),
                        source_transaction_id,
                        source_line_id,
                        "historical-reporting-import",
                    )
                    _append_historical_event(
                        session,
                        book_id=book_id,
                        pending=PendingEvent(
                            event_id=historical_event_id,
                            stream_type="historical_reporting",
                            stream_id=historical_stream_id,
                            payload=HistoricalReportingLineImported(
                                source_line_id=source_line_id,
                                source_transaction_id=source_transaction_id,
                                transaction_id=target_transaction_id,
                                line_kind=line_kind,
                                position=int(line["position"]),
                                asset_code=asset_code,
                                amount=source_amount,
                                source_version=int(line.get("version", 1)),
                                source_row_hash=_row_hash(line).hex(),
                            ),
                            command_id=historical_command_id,
                            actor_subject_id=actor.subject_id,
                            correlation_id=historical_command_id,
                            causation_event_id=parent_journal_event_id,
                            effective_at=effective_at,
                        ),
                    )

            def run_transaction_load(
                *,
                group_items=tuple(group),
                apply_group=apply_transaction_group,
            ) -> None:
                group_loader = loader(lambda _session, _item: None)
                collect(
                    group_loader.load_atomic_group(
                        group_items,
                        apply_group=apply_group,
                    )
                )

            scheduled_loads.append(
                (
                    sequence,
                    run_transaction_load,
                )
            )

        def apply_classification_activity(
            session: Session, item: SourceLoadItem
        ) -> None:
            row = item.payload
            source_key = _source_key("classification_events", row)
            source_event_id = str(row["classification_event_id"])
            source_book_id = str(row["book_id"])
            book_id = _book_id(source_book_id)
            activity_kind = HistoricalCategoryActivityKind(str(row["event_type"]))
            effective_at = _parse_time(
                row["created_at"],
                table="classification_events",
                primary_key=source_key,
            )
            causation_event_id: UUID | None = None

            if activity_kind is HistoricalCategoryActivityKind.RECLASSIFY:
                after = row["after"]
                if not isinstance(after, Mapping):
                    raise BackfillRunBlocked(
                        "classification snapshot disappeared after preflight"
                    )
                source_transaction_id = str(after["transaction_id"])
                source_line_id = str(after["line_id"])
                target_transaction_id = _transaction_id(
                    snapshot_id,
                    source_book_id,
                    source_transaction_id,
                )
                latest_revision = session.scalar(
                    select(func.max(ReportingLineRecord.classification_revision)).where(
                        ReportingLineRecord.book_id == book_id,
                        ReportingLineRecord.transaction_id == target_transaction_id,
                    )
                )
                if latest_revision is None:
                    raise BackfillRunBlocked(
                        "reclassification target has no reporting projection"
                    )
                current_lines = tuple(
                    session.scalars(
                        select(ReportingLineRecord)
                        .where(
                            ReportingLineRecord.book_id == book_id,
                            ReportingLineRecord.transaction_id == target_transaction_id,
                            ReportingLineRecord.classification_revision
                            == latest_revision,
                        )
                        .order_by(ReportingLineRecord.line_position)
                    )
                )
                target_line_id = deterministic_uuid(
                    "line",
                    source_book_id,
                    source_transaction_id,
                    source_line_id,
                )
                reporting_inputs: list[ReportingLineInput] = []
                replaced = False
                for projected in current_lines:
                    if projected.line_id == target_line_id:
                        replaced = True
                        line_version_id = deterministic_uuid(
                            "line_version",
                            source_book_id,
                            source_transaction_id,
                            source_line_id,
                            source_event_id,
                            str(row["version"]),
                        )
                        catalog_id = _category_version_id(
                            source_book_id, after["category_version_id"]
                        )
                        dimension_id = _category_id(
                            source_book_id, after["category_id"]
                        )
                    else:
                        line_version_id = projected.line_version_id
                        catalog_id = projected.catalog_id
                        dimension_id = projected.dimension_id
                    reporting_inputs.append(
                        ReportingLineInput(
                            line_id=projected.line_id,
                            line_version_id=line_version_id,
                            catalog_id=catalog_id,
                            asset_code=projected.asset_code,
                            units=str(int(projected.units)),
                            line_kind=ReportingLineKind(projected.line_kind),
                            dimension=ReportingDimension(projected.dimension),
                            dimension_id=dimension_id,
                            description_ref=projected.description_ref,
                        )
                    )
                if not replaced:
                    raise BackfillRunBlocked(
                        "reclassification target line disappeared during replay"
                    )
                reporting_command_id = deterministic_uuid(
                    "command",
                    snapshot_id,
                    source_book_id,
                    source_event_id,
                    "reporting.reclassify",
                )
                execute_assign_reporting_lines(
                    AssignReportingLinesCommand(
                        book_id=book_id,
                        command_id=reporting_command_id,
                        transaction_id=target_transaction_id,
                        expected_revision=int(latest_revision),
                        lines=tuple(reporting_inputs),
                        effective_at=effective_at,
                    ),
                    raw_key=(
                        f"backfill:{snapshot_id}:classification:{source_event_id}"
                    ),
                    actor=actor,
                    uow_factory=lambda: _BorrowedUnitOfWork(session),
                    max_attempts=1,
                )
                causation_event_id = session.scalar(
                    select(ReportingLineRecord.source_event_id)
                    .where(
                        ReportingLineRecord.book_id == book_id,
                        ReportingLineRecord.transaction_id == target_transaction_id,
                        ReportingLineRecord.classification_revision
                        == int(latest_revision) + 1,
                    )
                    .limit(1)
                )
                if causation_event_id is None:
                    raise BackfillRunBlocked(
                        "reclassification event projection disappeared"
                    )

            before = row["before"]
            after = row["after"]
            rollback = row["rollback"]
            if not all(
                isinstance(value, Mapping) for value in (before, after, rollback)
            ):
                raise BackfillRunBlocked(
                    "classification audit snapshot disappeared after preflight"
                )
            historical_event_id = deterministic_uuid(
                "event",
                snapshot_id,
                source_book_id,
                source_event_id,
                "historical-category-activity",
            )
            historical_command_id = deterministic_uuid(
                "command",
                snapshot_id,
                source_book_id,
                source_event_id,
                "historical-category-import",
            )
            _append_historical_event(
                session,
                book_id=book_id,
                pending=PendingEvent(
                    event_id=historical_event_id,
                    stream_type="historical_category",
                    stream_id=historical_event_id,
                    payload=HistoricalCategoryActivityImported(
                        source_event_id=source_event_id,
                        activity_kind=activity_kind,
                        source_category_id=str(row["source_category_id"]),
                        target_category_id=(
                            None
                            if row.get("target_category_id") is None
                            else str(row["target_category_id"])
                        ),
                        affected_line_count=int(row["affected_line_count"]),
                        source_actor_hash=_source_actor_hash(
                            row["created_by"], primary_key=source_key
                        ),
                        source_version=int(row["version"]),
                        before_hash=_classification_value_hash(before),
                        after_hash=_classification_value_hash(after),
                        rollback_hash=_classification_value_hash(rollback),
                        source_row_hash=_row_hash(row).hex(),
                    ),
                    command_id=historical_command_id,
                    actor_subject_id=actor.subject_id,
                    correlation_id=historical_command_id,
                    causation_event_id=causation_event_id,
                    effective_at=effective_at,
                ),
            )

        for scheduled_classification in scheduled_classifications:
            sequence = scheduled_classification.rank
            row = scheduled_classification.row
            classification_item = _item(
                "classification_events",
                row,
                canonical_key=(f"{sequence:012d}:{row['classification_event_id']}"),
                target_id=deterministic_uuid(
                    "event",
                    snapshot_id,
                    str(row["book_id"]),
                    str(row["classification_event_id"]),
                    "historical-category-activity",
                ),
            )

            def run_classification_load(*, item=classification_item) -> None:
                collect(loader(apply_classification_activity).load((item,)))

            scheduled_loads.append(
                (
                    sequence,
                    run_classification_load,
                )
            )

        def apply_investment_activity(session: Session, item: SourceLoadItem) -> None:
            row = item.payload
            source_key = _source_key("investment_events", row)
            asset_code = str(row["currency"])
            cash_amount = _source_decimal(
                row["amount"],
                table="investment_events",
                primary_key=source_key,
                field="amount",
            )
            source_event_id = str(row["event_id"])
            source_book_id = str(row["book_id"])
            event_id = deterministic_uuid(
                "event",
                snapshot_id,
                source_book_id,
                source_event_id,
                "historical-investment-activity",
            )
            stream_id = deterministic_uuid(
                "event",
                snapshot_id,
                source_book_id,
                source_event_id,
                "historical-investment-stream",
            )
            command_id = deterministic_uuid(
                "command",
                snapshot_id,
                source_book_id,
                source_event_id,
                "historical-investment-import",
            )
            quantity = (
                None
                if row.get("units") is None
                else _source_decimal(
                    row["units"],
                    table="investment_events",
                    primary_key=source_key,
                    field="units",
                )
            )
            nav = (
                None
                if row.get("nav") is None
                else _source_decimal(
                    row["nav"],
                    table="investment_events",
                    primary_key=source_key,
                    field="nav",
                )
            )
            pending = PendingEvent(
                event_id=event_id,
                stream_type="historical_investment",
                stream_id=stream_id,
                payload=HistoricalInvestmentActivityImported(
                    source_event_id=source_event_id,
                    source_account_id=str(row["account_id"]),
                    activity_kind=HistoricalInvestmentActivityKind(
                        str(row["event_type"])
                    ),
                    settlement_asset_code=asset_code,
                    cash_amount=cash_amount,
                    quantity=quantity,
                    nav=nav,
                    source_version=int(row["version"]),
                    source_row_hash=_row_hash(row).hex(),
                ),
                command_id=command_id,
                actor_subject_id=actor.subject_id,
                correlation_id=command_id,
                causation_event_id=None,
                effective_at=_parse_time(
                    row["occurred_at"],
                    table="investment_events",
                    primary_key=source_key,
                ),
            )
            _append_historical_event(
                session,
                book_id=_book_id(row["book_id"]),
                pending=pending,
            )

        for scheduled_investment in scheduled_investments:
            sequence = scheduled_investment.rank
            row = scheduled_investment.row
            investment_item = _item(
                "investment_events",
                row,
                canonical_key=f"{sequence:012d}:{row['event_id']}",
                target_id=deterministic_uuid(
                    "event",
                    snapshot_id,
                    str(row["book_id"]),
                    str(row["event_id"]),
                    "historical-investment-activity",
                ),
            )

            def run_investment_load(*, item=investment_item) -> None:
                collect(loader(apply_investment_activity).load((item,)))

            scheduled_loads.append(
                (
                    sequence,
                    run_investment_load,
                )
            )

        for _schedule_key, run_scheduled_load in sorted(
            scheduled_loads,
            key=lambda scheduled: scheduled[0],
        ):
            run_scheduled_load()

        # Valuation rows cannot be represented without changing their meaning;
        # preflight blocks any non-empty source instead of receipting a no-op.
        if rows["investment_valuations"]:
            raise BackfillRunBlocked(
                "investment valuations disappeared after blocking preflight"
            )

        with factory() as session, session.begin():
            for source_book in rows["ledger_books"]:
                book = session.get(BookRecord, _book_id(source_book["book_id"]))
                if book is None:
                    raise BackfillRunBlocked("Book disappeared during finalization")
                book.base_asset_code = str(source_book["base_currency"])
            for source_asset in rows["assets"]:
                asset = session.get(AssetRecord, str(source_asset["asset_code"]))
                if asset is None:
                    raise BackfillRunBlocked("asset disappeared during finalization")
                asset.status = (
                    "active"
                    if str(source_asset.get("status", "active")) == "active"
                    else "disabled"
                )
                if asset.asset_code == "USDT":
                    asset.input_scale = min(6, asset.ledger_scale)
            for source_category in rows["categories"]:
                category = session.get(
                    CategoryRecord,
                    (
                        _book_id(source_category["book_id"]),
                        _category_id(
                            source_category["book_id"], source_category["category_id"]
                        ),
                    ),
                )
                if category is None:
                    raise BackfillRunBlocked("category disappeared during finalization")
                category.status = (
                    "archived"
                    if str(source_category.get("status")) == "archived"
                    else "active"
                )
            session.flush()

        with factory() as session:
            terminal_hashes = {
                str(row.book_id): row.last_hash.hex()
                for row in session.scalars(
                    select(BookEventHeadRecord).order_by(BookEventHeadRecord.book_id)
                )
            }
        seal = seal_backfill(
            factory,
            snapshot_id=snapshot_id,
            manifest_hash=manifest_hash,
            source_counts=actual_counts,
            terminal_book_hashes=terminal_hashes,
        )
        return BackfillPipelineResult(
            applied_receipts=totals["applied"],
            replayed_receipts=totals["replayed"],
            seal=seal,
        )
    finally:
        engine.dispose()


def _record_blocker(
    target_url: str,
    *,
    snapshot_id: str,
    manifest_hash: bytes,
    error: BackfillMappingError,
) -> None:
    _assert_target_url_control_scope(
        target_url,
        snapshot_id=snapshot_id,
        manifest_hash=manifest_hash,
    )
    engine = create_engine(target_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        record_quarantine(
            factory,
            snapshot_id=snapshot_id,
            source_table=error.source_table,
            source_primary_key=error.source_primary_key,
            reason_code=error.reason_code,
            details={"detail": error.detail},
        )
    finally:
        engine.dispose()


def run_backfill(
    *,
    source_url: str,
    target_url: str,
    dump_path: Path,
    manifest_path: Path,
    output_dir: Path,
    batch_size: int,
    workers: int,
    shuffle_seed: int,
) -> BackfillPipelineResult:
    output_dir = Path(output_dir)
    extraction_dir = output_dir / "extraction"
    verification_path = output_dir / "verification.json"
    frozen = read_manifest(manifest_path)
    config = BackfillConfig(
        source_url=source_url,
        target_url=target_url,
        dump_path=dump_path,
        source_revision=frozen.source_revision,
        output_dir=extraction_dir,
        batch_size=batch_size,
        workers=workers,
        shuffle_seed=shuffle_seed,
        frozen_manifest_path=manifest_path,
    )
    verify_frozen_source(
        dump_path=dump_path,
        manifest=frozen,
        actual_source_revision=frozen.source_revision,
    )

    if output_dir.exists():
        if verification_path.exists():
            raise FileExistsError(
                f"completed backfill output already exists: {output_dir}"
            )
        if not extraction_dir.is_dir():
            raise FileExistsError(
                f"backfill output exists without a resumable extraction: {output_dir}"
            )
        extracted_manifest, rows = load_extracted_rows(extraction_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        extraction = extract_database(config)
        extracted_manifest, rows = load_extracted_rows(extraction_dir)
        if not extraction.inventory.ok:
            issue = extraction.inventory.issues[0]
            error = BackfillMappingError(
                issue.source_table,
                issue.source_primary_key,
                issue.code,
                issue.relation,
            )
            _record_blocker(
                target_url,
                snapshot_id=extracted_manifest.snapshot_id,
                manifest_hash=bytes.fromhex(
                    extracted_manifest.content_sha256
                    or extracted_manifest.calculated_content_sha256()
                ),
                error=error,
            )
            raise BackfillRunBlocked(str(error))

    if (
        extracted_manifest.dump_sha256 != frozen.dump_sha256
        or extracted_manifest.source_revision != frozen.source_revision
    ):
        raise ValueError(
            "canonical extraction source identity does not match the frozen manifest"
        )
    if frozen.tables and extracted_manifest.to_dict() != frozen.to_dict():
        raise ValueError("canonical extraction does not match the frozen manifest")
    try:
        result = load_extracted_rows_to_target(
            target_url=target_url,
            manifest=extracted_manifest,
            rows_by_table=rows,
        )
    except BackfillMappingError as error:
        _record_blocker(
            target_url,
            snapshot_id=extracted_manifest.snapshot_id,
            manifest_hash=bytes.fromhex(
                extracted_manifest.content_sha256
                or extracted_manifest.calculated_content_sha256()
            ),
            error=error,
        )
        raise BackfillRunBlocked(str(error)) from error
    write_verification(verification_path, result.seal)
    return result


__all__ = [
    "BackfillMappingError",
    "BackfillPipelineResult",
    "BackfillRunBlocked",
    "load_extracted_rows_to_target",
    "run_backfill",
]
