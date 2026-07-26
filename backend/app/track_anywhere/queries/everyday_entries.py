from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from ..infrastructure.db.models.event_store import BookEventHeadRecord
from ..infrastructure.db.models.event_store import LedgerEventRecord
from ..infrastructure.db.models.projections import JournalTransactionRecord
from ..domain.journal.events import JournalTransactionPosted
from ..serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from .journal import (
    CreditCardRelation,
    JournalItem,
    JournalPosting,
    get_journal_transaction,
    list_journal,
)
from .reporting import ReportingLine, list_current_reporting_lines


class EverydayEntryKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    CREDIT_CARD_PAYMENT = "credit_card_payment"
    REFUND = "refund"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"
    UNAVAILABLE = "unavailable"


class FieldAvailability(StrEnum):
    AVAILABLE = "available"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


class NarrativeAccess(StrEnum):
    REDACTED = "redacted"
    OWNER_AUTHORIZED = "owner_authorized"


class NarrativeStatus(StrEnum):
    ABSENT = "absent"
    REDACTED = "redacted"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERASED = "erased"


@dataclass(frozen=True, slots=True)
class AssetUnitAmount:
    """An exact asset-main-unit value rendered at the asset ledger scale."""

    value: str
    asset_code: str
    scale: int


@dataclass(frozen=True, slots=True)
class AccountDisplay:
    account_id: UUID
    display_name: str


@dataclass(frozen=True, slots=True)
class CategoryAllocationView:
    category_id: UUID
    category_version_id: UUID
    path: tuple[str, ...]
    amount: AssetUnitAmount


@dataclass(frozen=True, slots=True)
class NarrativeView:
    status: NarrativeStatus
    merchant: str | None = field(default=None, repr=False)
    channel: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class RawJournalReference:
    transaction_id: UUID
    book_position: int
    transaction_kind: str


@dataclass(frozen=True, slots=True)
class EverydayEntryView:
    entry_id: UUID
    transaction_id: UUID
    kind: EverydayEntryKind
    amount: AssetUnitAmount | None
    amount_availability: FieldAvailability
    source_account: AccountDisplay | None
    target_account: AccountDisplay | None
    payment_account: AccountDisplay | None
    account_display_availability: FieldAvailability
    category_allocations: tuple[CategoryAllocationView, ...]
    category_availability: FieldAvailability
    occurred_at: datetime
    original_transaction_id: UUID | None
    reversed_by_transaction_id: UUID | None
    reverses_transaction_id: UUID | None
    relationship_availability: FieldAvailability
    narrative: NarrativeView
    raw_journal: RawJournalReference


@dataclass(frozen=True, slots=True)
class EverydayEntryPage:
    items: tuple[EverydayEntryView, ...]
    next_cursor: str | None
    as_of_book_position: int


@dataclass(frozen=True, slots=True)
class DecodedTransactionNarrative:
    """Safe output of a version-aware, authenticated narrative decoder."""

    book_id: UUID
    sidecar_id: UUID
    status: NarrativeStatus
    merchant: str | None = field(default=None, repr=False)
    channel: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status is NarrativeStatus.AVAILABLE:
            if self.merchant is None and self.channel is None:
                raise ValueError(
                    "an available narrative must expose merchant or channel"
                )
        elif self.merchant is not None or self.channel is not None:
            raise ValueError(
                "an unavailable narrative cannot expose protected fields"
            )


@runtime_checkable
class TransactionNarrativeDecoder(Protocol):
    """Narrow seam for the storage-owned transaction_narrative_v2 decoder."""

    def decode(
        self,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> Mapping[UUID, DecodedTransactionNarrative]: ...


@dataclass(frozen=True, slots=True)
class _AccountFact:
    account_id: UUID
    account_type: str
    system_role: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class _AssetFact:
    asset_code: str
    ledger_scale: int


@dataclass(frozen=True, slots=True)
class _ReportingFact:
    category_id: UUID
    category_version_id: UUID
    path: tuple[str, ...] | None
    asset_code: str
    units: int
    line_kind: str


@dataclass(frozen=True, slots=True)
class _EntryFacts:
    book_id: UUID
    journal: JournalItem
    accounts: Mapping[UUID, _AccountFact]
    assets: Mapping[str, _AssetFact]
    reporting: tuple[_ReportingFact, ...]
    inherited_reporting: tuple[_ReportingFact, ...]
    semantic_original_transaction_id: UUID | None = None


@runtime_checkable
class EverydayEntrySource(Protocol):
    def get(
        self,
        book_id: UUID,
        transaction_id: UUID,
        *,
        as_of_book_position: int | None = None,
    ) -> _EntryFacts: ...

    def list(
        self,
        book_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        as_of_book_position: int | None = None,
    ) -> tuple[tuple[_EntryFacts, ...], str | None, int]: ...


class SqlAlchemyEverydayEntrySource:
    """Compose the existing synchronous journal/reporting/catalog projections."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        book_id: UUID,
        transaction_id: UUID,
        *,
        as_of_book_position: int | None = None,
    ) -> _EntryFacts:
        item = get_journal_transaction(
            self._session,
            book_id,
            transaction_id,
            as_of_book_position=as_of_book_position,
        )
        as_of = as_of_book_position
        if as_of is None:
            as_of = self._session.scalar(
                select(BookEventHeadRecord.last_position).where(
                    BookEventHeadRecord.book_id == book_id
                )
            )
        if type(as_of) is not int:
            raise LookupError("Book not found")
        return self._load_facts(book_id, (item,), as_of_book_position=as_of)[0]

    def list(
        self,
        book_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        as_of_book_position: int | None = None,
    ) -> tuple[tuple[_EntryFacts, ...], str | None, int]:
        page = list_journal(
            self._session,
            book_id,
            limit=limit,
            cursor=cursor,
            as_of_book_position=as_of_book_position,
        )
        return (
            self._load_facts(
                book_id,
                page.items,
                as_of_book_position=page.as_of_book_position,
            ),
            page.next_cursor,
            page.as_of_book_position,
        )

    def _load_facts(
        self,
        book_id: UUID,
        items: tuple[JournalItem, ...],
        *,
        as_of_book_position: int,
    ) -> tuple[_EntryFacts, ...]:
        account_ids = {
            posting.account_id for item in items for posting in item.postings
        }
        accounts = {
            row.account_id: _AccountFact(
                account_id=row.account_id,
                account_type=row.account_type,
                system_role=row.system_role,
                display_name=row.current_name,
            )
            for row in self._session.scalars(
                select(AccountRecord).where(
                    AccountRecord.book_id == book_id,
                    AccountRecord.account_id.in_(account_ids),
                )
            )
        }
        asset_codes = {
            posting.asset_code for item in items for posting in item.postings
        }
        assets = {
            row.asset_code: _AssetFact(
                asset_code=row.asset_code,
                ledger_scale=row.ledger_scale,
            )
            for row in self._session.scalars(
                select(AssetRecord).where(AssetRecord.asset_code.in_(asset_codes))
            )
        }

        current_reporting = list_current_reporting_lines(
            self._session,
            book_id,
            as_of_book_position=as_of_book_position,
        )
        semantic_original_ids = self._semantic_original_transaction_ids(
            book_id,
            items,
        )
        relevant_transaction_ids = {
            value
            for item in items
            for value in (
                item.transaction_id,
                item.reverses_transaction_id,
                semantic_original_ids.get(item.transaction_id),
                (
                    item.credit_card_relation.original_transaction_id
                    if item.credit_card_relation is not None
                    and item.credit_card_relation.intent == "refund"
                    else None
                ),
            )
            if value is not None
        }
        lines_by_transaction: dict[UUID, list[ReportingLine]] = defaultdict(list)
        relevant_lines: list[ReportingLine] = []
        for line in current_reporting:
            if line.transaction_id in relevant_transaction_ids:
                lines_by_transaction[line.transaction_id].append(line)
                relevant_lines.append(line)

        path_by_identity = self._category_paths(book_id, relevant_lines)
        facts_by_transaction = {
            transaction_id: tuple(
                _ReportingFact(
                    category_id=line.dimension_id,
                    category_version_id=line.catalog_id,
                    path=path_by_identity.get(
                        (line.dimension_id, line.catalog_id)
                    ),
                    asset_code=line.asset_code,
                    units=line.units,
                    line_kind=line.line_kind,
                )
                for line in sorted(lines, key=lambda value: value.line_position)
                if line.dimension == "category" and line.dimension_id is not None
            )
            for transaction_id, lines in lines_by_transaction.items()
        }

        return tuple(
            _EntryFacts(
                book_id=book_id,
                journal=item,
                accounts=accounts,
                assets=assets,
                reporting=facts_by_transaction.get(item.transaction_id, ()),
                inherited_reporting=facts_by_transaction.get(
                    _inherited_reporting_transaction_id(
                        item,
                        semantic_original_transaction_id=semantic_original_ids.get(
                            item.transaction_id
                        ),
                    ),
                    (),
                ),
                semantic_original_transaction_id=semantic_original_ids.get(
                    item.transaction_id
                ),
            )
            for item in items
        )

    def _semantic_original_transaction_ids(
        self,
        book_id: UUID,
        items: tuple[JournalItem, ...],
    ) -> dict[UUID, UUID]:
        refund_ids = tuple(
            item.transaction_id
            for item in items
            if item.transaction_kind == "refund"
            and item.credit_card_relation is None
        )
        if not refund_ids:
            return {}
        rows = self._session.execute(
            select(JournalTransactionRecord, LedgerEventRecord)
            .join(
                LedgerEventRecord,
                (
                    LedgerEventRecord.book_id
                    == JournalTransactionRecord.book_id
                )
                & (
                    LedgerEventRecord.event_id
                    == JournalTransactionRecord.source_event_id
                ),
            )
            .where(
                JournalTransactionRecord.book_id == book_id,
                JournalTransactionRecord.transaction_id.in_(refund_ids),
            )
        )
        result: dict[UUID, UUID] = {}
        for transaction, stored in rows:
            payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
                stored.event_type,
                stored.event_schema_version,
                stored.payload,
            )
            if (
                type(payload) is not JournalTransactionPosted
                or payload.transaction_id != transaction.transaction_id
                or payload.kind.value != "refund"
            ):
                continue
            original_transaction_id = getattr(
                payload,
                "original_transaction_id",
                None,
            )
            if type(original_transaction_id) is UUID:
                result[transaction.transaction_id] = original_transaction_id
        return result

    def _category_paths(
        self,
        book_id: UUID,
        lines: list[ReportingLine],
    ) -> dict[tuple[UUID, UUID], tuple[str, ...] | None]:
        identities = {
            (line.dimension_id, line.catalog_id)
            for line in lines
            if line.dimension == "category" and line.dimension_id is not None
        }
        if not identities:
            return {}
        category_records = tuple(
            self._session.scalars(
                select(CategoryRecord).where(CategoryRecord.book_id == book_id)
            )
        )
        categories = {row.category_id: row for row in category_records}
        versions = {
            (row.category_id, row.category_version_id): row
            for row in self._session.scalars(
                select(CategoryVersionRecord).where(
                    CategoryVersionRecord.book_id == book_id,
                    CategoryVersionRecord.category_id.in_(
                        tuple(category_id for category_id, _ in identities)
                    ),
                )
            )
        }
        return {
            identity: _build_category_path(
                identity,
                categories=categories,
                versions=versions,
            )
            for identity in identities
        }


class EverydayEntryQueryService:
    def __init__(
        self,
        source: EverydayEntrySource,
        *,
        narrative_decoder: TransactionNarrativeDecoder | None = None,
    ) -> None:
        self._source = source
        self._narrative_decoder = narrative_decoder

    def get(
        self,
        book_id: UUID,
        transaction_id: UUID,
        *,
        as_of_book_position: int | None = None,
        narrative_access: NarrativeAccess = NarrativeAccess.REDACTED,
    ) -> EverydayEntryView:
        _validate_query_ids(book_id, transaction_id)
        facts = self._source.get(
            book_id,
            transaction_id,
            as_of_book_position=as_of_book_position,
        )
        _require_source_scope(facts, book_id)
        narratives = self._decode_narratives(
            book_id,
            (facts,),
            narrative_access=narrative_access,
        )
        return _build_view(facts, narratives=narratives)

    def list(
        self,
        book_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        as_of_book_position: int | None = None,
        narrative_access: NarrativeAccess = NarrativeAccess.REDACTED,
    ) -> EverydayEntryPage:
        if type(book_id) is not UUID:
            raise ValueError("book_id must be a UUID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        facts, next_cursor, as_of = self._source.list(
            book_id,
            limit=limit,
            cursor=cursor,
            as_of_book_position=as_of_book_position,
        )
        for item in facts:
            _require_source_scope(item, book_id)
        narratives = self._decode_narratives(
            book_id,
            facts,
            narrative_access=narrative_access,
        )
        return EverydayEntryPage(
            items=tuple(_build_view(item, narratives=narratives) for item in facts),
            next_cursor=next_cursor,
            as_of_book_position=as_of,
        )

    def _decode_narratives(
        self,
        book_id: UUID,
        facts: tuple[_EntryFacts, ...],
        *,
        narrative_access: NarrativeAccess,
    ) -> Mapping[UUID, DecodedTransactionNarrative]:
        if type(narrative_access) is not NarrativeAccess:
            raise ValueError("narrative_access is invalid")
        if narrative_access is not NarrativeAccess.OWNER_AUTHORIZED:
            return {}
        sidecar_ids = tuple(
            dict.fromkeys(
                item.journal.description_ref
                for item in facts
                if item.journal.description_ref is not None
            )
        )
        if not sidecar_ids:
            return {}
        if self._narrative_decoder is None:
            return {
                sidecar_id: DecodedTransactionNarrative(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    status=NarrativeStatus.UNAVAILABLE,
                )
                for sidecar_id in sidecar_ids
            }
        decoded = self._narrative_decoder.decode(
            book_id=book_id,
            sidecar_ids=sidecar_ids,
        )
        allowed_ids = set(sidecar_ids)
        for sidecar_id, value in decoded.items():
            if (
                type(sidecar_id) is not UUID
                or sidecar_id not in allowed_ids
                or type(value) is not DecodedTransactionNarrative
                or value.book_id != book_id
                or value.sidecar_id != sidecar_id
            ):
                raise RuntimeError(
                    "transaction narrative decoder crossed its authorized scope"
                )
        return {
            sidecar_id: decoded.get(
                sidecar_id,
                DecodedTransactionNarrative(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    status=NarrativeStatus.UNAVAILABLE,
                ),
            )
            for sidecar_id in sidecar_ids
        }


def get_everyday_entry(
    session: Session,
    book_id: UUID,
    transaction_id: UUID,
    *,
    as_of_book_position: int | None = None,
    narrative_access: NarrativeAccess = NarrativeAccess.REDACTED,
    narrative_decoder: TransactionNarrativeDecoder | None = None,
) -> EverydayEntryView:
    return EverydayEntryQueryService(
        SqlAlchemyEverydayEntrySource(session),
        narrative_decoder=narrative_decoder,
    ).get(
        book_id,
        transaction_id,
        as_of_book_position=as_of_book_position,
        narrative_access=narrative_access,
    )


def list_everyday_entries(
    session: Session,
    book_id: UUID,
    *,
    limit: int,
    cursor: str | None = None,
    as_of_book_position: int | None = None,
    narrative_access: NarrativeAccess = NarrativeAccess.REDACTED,
    narrative_decoder: TransactionNarrativeDecoder | None = None,
) -> EverydayEntryPage:
    return EverydayEntryQueryService(
        SqlAlchemyEverydayEntrySource(session),
        narrative_decoder=narrative_decoder,
    ).list(
        book_id,
        limit=limit,
        cursor=cursor,
        as_of_book_position=as_of_book_position,
        narrative_access=narrative_access,
    )


def _build_view(
    facts: _EntryFacts,
    *,
    narratives: Mapping[UUID, DecodedTransactionNarrative],
) -> EverydayEntryView:
    item = facts.journal
    kind = _entry_kind(item, facts.reporting or facts.inherited_reporting)
    amount = _transaction_amount(item.postings, facts.assets)
    source, target, payment, account_status = _account_displays(
        item,
        kind=kind,
        accounts=facts.accounts,
    )
    category_allocations, category_status = _category_allocations(
        facts,
        kind=kind,
        transaction_amount=amount,
    )
    narrative = _narrative_view(
        item.description_ref,
        narratives=narratives,
    )
    original_transaction_id = (
        item.credit_card_relation.original_transaction_id
        if item.credit_card_relation is not None
        and item.credit_card_relation.intent == "refund"
        else facts.semantic_original_transaction_id or item.reverses_transaction_id
    )
    return EverydayEntryView(
        entry_id=item.transaction_id,
        transaction_id=item.transaction_id,
        kind=kind,
        amount=amount,
        amount_availability=(
            FieldAvailability.AVAILABLE
            if amount is not None
            else FieldAvailability.UNAVAILABLE
        ),
        source_account=source,
        target_account=target,
        payment_account=payment,
        account_display_availability=account_status,
        category_allocations=category_allocations,
        category_availability=category_status,
        occurred_at=item.effective_at,
        original_transaction_id=original_transaction_id,
        reversed_by_transaction_id=item.reversed_by_transaction_id,
        reverses_transaction_id=item.reverses_transaction_id,
        relationship_availability=_relationship_availability(
            kind=kind,
            original_transaction_id=original_transaction_id,
            reversed_by_transaction_id=item.reversed_by_transaction_id,
            reverses_transaction_id=item.reverses_transaction_id,
        ),
        narrative=narrative,
        raw_journal=RawJournalReference(
            transaction_id=item.transaction_id,
            book_position=item.book_position,
            transaction_kind=item.transaction_kind,
        ),
    )


def _entry_kind(
    item: JournalItem,
    reporting: tuple[_ReportingFact, ...],
) -> EverydayEntryKind:
    if item.reverses_transaction_id is not None:
        return EverydayEntryKind.REVERSAL
    relation = item.credit_card_relation
    if relation is not None:
        return {
            "charge": EverydayEntryKind.EXPENSE,
            "fee": EverydayEntryKind.EXPENSE,
            "payment": EverydayEntryKind.CREDIT_CARD_PAYMENT,
            "refund": EverydayEntryKind.REFUND,
        }.get(relation.intent, EverydayEntryKind.UNAVAILABLE)
    direct_kind = {
        "transfer": EverydayEntryKind.TRANSFER,
        "adjustment": EverydayEntryKind.ADJUSTMENT,
        "refund": EverydayEntryKind.REFUND,
        "credit_card_charge": EverydayEntryKind.EXPENSE,
        "credit_card_fee": EverydayEntryKind.EXPENSE,
        "credit_card_payment": EverydayEntryKind.CREDIT_CARD_PAYMENT,
        "credit_card_refund": EverydayEntryKind.REFUND,
    }.get(item.transaction_kind)
    if direct_kind is not None:
        return direct_kind
    line_kinds = {line.line_kind for line in reporting}
    if line_kinds == {"expense"}:
        return EverydayEntryKind.EXPENSE
    if line_kinds == {"income"}:
        return EverydayEntryKind.INCOME
    return EverydayEntryKind.UNAVAILABLE


def _transaction_amount(
    postings: tuple[JournalPosting, ...],
    assets: Mapping[str, _AssetFact],
) -> AssetUnitAmount | None:
    debit_by_asset: dict[str, int] = defaultdict(int)
    credit_by_asset: dict[str, int] = defaultdict(int)
    for posting in postings:
        target = debit_by_asset if posting.side == "debit" else credit_by_asset
        target[posting.asset_code] += posting.units
    if (
        len(debit_by_asset) != 1
        or debit_by_asset != credit_by_asset
    ):
        return None
    asset_code, units = next(iter(debit_by_asset.items()))
    asset = assets.get(asset_code)
    if asset is None:
        return None
    return AssetUnitAmount(
        value=_format_units(units, asset.ledger_scale),
        asset_code=asset_code,
        scale=asset.ledger_scale,
    )


def _account_displays(
    item: JournalItem,
    *,
    kind: EverydayEntryKind,
    accounts: Mapping[UUID, _AccountFact],
) -> tuple[
    AccountDisplay | None,
    AccountDisplay | None,
    AccountDisplay | None,
    FieldAvailability,
]:
    relation = item.credit_card_relation
    if relation is not None:
        card = _display(accounts.get(relation.card_account_id))
        counter = _display(accounts.get(relation.counter_account_id))
        if relation.intent in {"charge", "fee"}:
            values = (None, None, card)
        elif relation.intent == "payment":
            values = (counter, card, None)
        elif relation.intent == "refund":
            values = (None, card, None)
        else:
            values = (None, None, None)
        expected = 1 if relation.intent in {"charge", "fee", "refund"} else 2
        available = sum(value is not None for value in values) == expected
        return (*values, _availability(available))

    debit = _single_external_account(item.postings, accounts, side="debit")
    credit = _single_external_account(item.postings, accounts, side="credit")
    if kind is EverydayEntryKind.EXPENSE:
        values = (None, None, credit)
        available = credit is not None
    elif kind is EverydayEntryKind.INCOME:
        values = (None, debit, None)
        available = debit is not None
    elif kind is EverydayEntryKind.TRANSFER:
        values = (credit, debit, None)
        available = credit is not None and debit is not None
    elif kind in {EverydayEntryKind.ADJUSTMENT, EverydayEntryKind.REVERSAL}:
        values = (credit, debit, None)
        available = credit is not None or debit is not None
    else:
        values = (None, None, None)
        available = False
    return (*values, _availability(available))


def _single_external_account(
    postings: tuple[JournalPosting, ...],
    accounts: Mapping[UUID, _AccountFact],
    *,
    side: str,
) -> AccountDisplay | None:
    candidates = {
        posting.account_id
        for posting in postings
        if posting.side == side
        and (account := accounts.get(posting.account_id)) is not None
        and account.system_role is None
        and account.account_type in {"asset", "liability"}
    }
    if len(candidates) != 1:
        return None
    return _display(accounts[next(iter(candidates))])


def _category_allocations(
    facts: _EntryFacts,
    *,
    kind: EverydayEntryKind,
    transaction_amount: AssetUnitAmount | None,
) -> tuple[tuple[CategoryAllocationView, ...], FieldAvailability]:
    if kind in {
        EverydayEntryKind.TRANSFER,
        EverydayEntryKind.CREDIT_CARD_PAYMENT,
        EverydayEntryKind.ADJUSTMENT,
    }:
        return (), FieldAvailability.NOT_APPLICABLE
    lines = facts.reporting
    if not lines and kind in {EverydayEntryKind.REFUND, EverydayEntryKind.REVERSAL}:
        lines = facts.inherited_reporting
        if kind is EverydayEntryKind.REFUND:
            lines = _safe_refund_allocations(lines, transaction_amount)
    if not lines or any(line.path is None for line in lines):
        return (), FieldAvailability.UNAVAILABLE
    allocations: list[CategoryAllocationView] = []
    for line in lines:
        asset = facts.assets.get(line.asset_code)
        if asset is None or line.path is None:
            return (), FieldAvailability.UNAVAILABLE
        allocations.append(
            CategoryAllocationView(
                category_id=line.category_id,
                category_version_id=line.category_version_id,
                path=line.path,
                amount=AssetUnitAmount(
                    value=_format_units(line.units, asset.ledger_scale),
                    asset_code=line.asset_code,
                    scale=asset.ledger_scale,
                ),
            )
        )
    return tuple(allocations), FieldAvailability.AVAILABLE


def _safe_refund_allocations(
    inherited: tuple[_ReportingFact, ...],
    amount: AssetUnitAmount | None,
) -> tuple[_ReportingFact, ...]:
    if amount is None or not inherited:
        return ()
    same_asset = tuple(
        line for line in inherited if line.asset_code == amount.asset_code
    )
    if len(same_asset) != len(inherited):
        return ()
    units = _parse_fixed_units(amount.value, amount.scale)
    if len(inherited) == 1:
        line = inherited[0]
        return (
            _ReportingFact(
                category_id=line.category_id,
                category_version_id=line.category_version_id,
                path=line.path,
                asset_code=line.asset_code,
                units=units,
                line_kind=line.line_kind,
            ),
        )
    if sum(line.units for line in inherited) == units:
        return inherited
    return ()


def _narrative_view(
    description_ref: UUID | None,
    *,
    narratives: Mapping[UUID, DecodedTransactionNarrative],
) -> NarrativeView:
    if description_ref is None:
        return NarrativeView(status=NarrativeStatus.ABSENT)
    decoded = narratives.get(description_ref)
    if decoded is None:
        return NarrativeView(status=NarrativeStatus.REDACTED)
    return NarrativeView(
        status=decoded.status,
        merchant=decoded.merchant,
        channel=decoded.channel,
    )


def _relationship_availability(
    *,
    kind: EverydayEntryKind,
    original_transaction_id: UUID | None,
    reversed_by_transaction_id: UUID | None,
    reverses_transaction_id: UUID | None,
) -> FieldAvailability:
    if kind is EverydayEntryKind.REFUND:
        return _availability(original_transaction_id is not None)
    if kind is EverydayEntryKind.REVERSAL:
        return _availability(reverses_transaction_id is not None)
    if reversed_by_transaction_id is not None:
        return FieldAvailability.AVAILABLE
    return FieldAvailability.NOT_APPLICABLE


def _inherited_reporting_transaction_id(
    item: JournalItem,
    *,
    semantic_original_transaction_id: UUID | None = None,
) -> UUID:
    if item.reverses_transaction_id is not None:
        return item.reverses_transaction_id
    if (
        item.credit_card_relation is not None
        and item.credit_card_relation.intent == "refund"
        and item.credit_card_relation.original_transaction_id is not None
    ):
        return item.credit_card_relation.original_transaction_id
    if item.transaction_kind == "refund" and semantic_original_transaction_id is not None:
        return semantic_original_transaction_id
    return item.transaction_id


def _build_category_path(
    identity: tuple[UUID, UUID],
    *,
    categories: Mapping[UUID, CategoryRecord],
    versions: Mapping[tuple[UUID, UUID], CategoryVersionRecord],
) -> tuple[str, ...] | None:
    leaf = versions.get(identity)
    if leaf is None:
        return None
    parts = [leaf.name]
    parent_id = leaf.parent_category_id
    seen = {leaf.category_id}
    while parent_id is not None:
        if parent_id in seen:
            return None
        seen.add(parent_id)
        parent = categories.get(parent_id)
        if parent is None:
            return None
        parts.append(parent.current_name)
        parent_id = parent.parent_category_id
    return tuple(reversed(parts))


def _format_units(units: int, scale: int) -> str:
    if type(units) is not int or units <= 0:
        raise ValueError("units must be a positive integer")
    if type(scale) is not int or not 0 <= scale <= 30:
        raise ValueError("scale must be between 0 and 30")
    if scale == 0:
        return str(units)
    digits = str(units).zfill(scale + 1)
    return f"{digits[:-scale]}.{digits[-scale:]}"


def _parse_fixed_units(value: str, scale: int) -> int:
    whole, separator, fraction = value.partition(".")
    if scale == 0:
        if separator:
            raise ValueError("zero-scale amount cannot contain a fraction")
        return int(whole)
    if not separator or len(fraction) != scale:
        raise ValueError("amount does not match its exact scale")
    return int(whole + fraction)


def _display(account: _AccountFact | None) -> AccountDisplay | None:
    if account is None:
        return None
    return AccountDisplay(
        account_id=account.account_id,
        display_name=account.display_name,
    )


def _availability(available: bool) -> FieldAvailability:
    return (
        FieldAvailability.AVAILABLE
        if available
        else FieldAvailability.UNAVAILABLE
    )


def _validate_query_ids(book_id: UUID, transaction_id: UUID) -> None:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if type(transaction_id) is not UUID:
        raise ValueError("transaction_id must be a UUID")


def _require_source_scope(facts: _EntryFacts, book_id: UUID) -> None:
    if facts.book_id != book_id:
        raise RuntimeError("everyday entry source crossed its Book scope")


__all__ = [
    "AccountDisplay",
    "AssetUnitAmount",
    "CategoryAllocationView",
    "DecodedTransactionNarrative",
    "EverydayEntryKind",
    "EverydayEntryPage",
    "EverydayEntryQueryService",
    "EverydayEntrySource",
    "EverydayEntryView",
    "FieldAvailability",
    "NarrativeAccess",
    "NarrativeStatus",
    "NarrativeView",
    "RawJournalReference",
    "SqlAlchemyEverydayEntrySource",
    "TransactionNarrativeDecoder",
    "get_everyday_entry",
    "list_everyday_entries",
]
