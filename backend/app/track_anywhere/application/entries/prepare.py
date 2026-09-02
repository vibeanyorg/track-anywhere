from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from collections.abc import Mapping
from typing import Callable
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from ...domain.credit_cards import CreditCardTransactionRecorded
from ...domain.journal import AccountSystemRole, AccountType
from ...domain.journal.events import JournalTransactionPosted
from ...domain.reporting.events import ReportingLinesAssigned
from ...infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from ...infrastructure.db.models.event_store import BookEventHeadRecord
from ...infrastructure.db.models.projections import AccountBalanceRecord
from ...infrastructure.db.repositories.entries import (
    EverydayEntryDuplicateRepository,
    PreparedEntryIntentRepository,
    ProposedPreparedIntent,
    hash_commit_token,
)
from ...infrastructure.crypto import DuplicateDetectionKeyProvider
from ...queries.everyday_entries import get_everyday_entry
from ...serialization.canonical_json import format_utc_microseconds
from ..idempotency import CommandActor
from ..journal.post_transaction import authorize_journal_write
from ..ledger_committer import LedgerWritePlan
from ..privacy.protected_content import (
    NarrativeAmountSource,
    NarrativeExternalReference,
    NarrativeMoney,
    TransactionNarrativeV2,
)
from ..privacy.service import ProtectedContentService
from ..payment_instruments.contracts import BindingRole
from ..payment_instruments.service import (
    PaymentInstrumentError,
    resolve_payment_instrument,
)
from ..unit_of_work import UnitOfWork
from .account_resolver import (
    AccountUse,
    EntryAccount,
    derive_account_last4,
    resolve_account,
)
from .amounts import EntryAsset
from .category_resolver import CategoryUsageKind, EntryCategory
from .compiler import (
    EntryCompilationContext,
    OriginalCategoryAllocation,
    OriginalEntry,
    compile_entry,
)
from .contracts import (
    AccountRef,
    AdjustmentEntryInput,
    BalanceInput,
    Clarification,
    ClarificationCode,
    ClarificationChoice,
    CreditCardPaymentEntryInput,
    EntryPreview,
    EntryWarning,
    EntryWarningCode,
    EverydayEntryInput,
    ExpenseEntryInput,
    IncomeEntryInput,
    MoneyInput,
    PreparedEntry,
    PreparedEntryStatus,
    PreviewAccount,
    PreviewMoney,
    RefundEntryInput,
    ResolvedEntryReferences,
    TransferEntryInput,
)
from .duplicate_detector import (
    DuplicateCandidate,
    DuplicateEvidenceKind,
    decide_duplicate,
)
from .errors import EntryClarificationRequired, EntryErrorCode, EntryGatewayError


UnitOfWorkFactory = Callable[[], UnitOfWork]
Clock = Callable[[], datetime]
TokenFactory = Callable[[], str]
IdFactory = Callable[[], UUID]

_TRANSACTION_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/everyday-entry/transaction",
)
_SIDECAR_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/everyday-entry/narrative",
)


@dataclass(frozen=True, slots=True)
class EntryPreparationRuntime:
    actor: CommandActor
    uow_factory: UnitOfWorkFactory
    protected_content_service: ProtectedContentService | None
    duplicate_key_provider: DuplicateDetectionKeyProvider | None
    intent_ttl: timedelta = timedelta(minutes=10)
    clock: Clock = lambda: datetime.now(UTC)
    token_factory: TokenFactory = lambda: secrets.token_urlsafe(32)
    id_factory: IdFactory = uuid4

    def __post_init__(self) -> None:
        if type(self.actor) is not CommandActor:
            raise TypeError("entry actor is invalid")
        if not callable(self.uow_factory):
            raise TypeError("entry unit-of-work factory is invalid")
        if self.duplicate_key_provider is not None and not isinstance(
            self.duplicate_key_provider,
            DuplicateDetectionKeyProvider,
        ):
            raise TypeError("entry duplicate key provider is invalid")
        if self.intent_ttl <= timedelta(0):
            raise ValueError("entry intent TTL must be positive")


def prepare_entry(
    *,
    book_id: UUID,
    entry: EverydayEntryInput,
    runtime: EntryPreparationRuntime,
) -> PreparedEntry:
    if type(book_id) is not UUID:
        raise EntryGatewayError(EntryErrorCode.INVALID_INPUT, "book_id is invalid")
    if runtime.duplicate_key_provider is None:
        raise EntryGatewayError(
            EntryErrorCode.UNSUPPORTED,
            "duplicate detection is not configured",
            retryable=True,
        )
    now = runtime.clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("entry clock must return an aware datetime")
    intent_id = runtime.id_factory()
    transaction_id = uuid5(_TRANSACTION_NAMESPACE, str(intent_id))
    sidecar_id = uuid5(_SIDECAR_NAMESPACE, str(intent_id))
    expires_at = now + runtime.intent_ttl

    with runtime.uow_factory() as uow:
        authorize_journal_write(
            uow.session,
            runtime.actor,
            book_id,
            lock_membership=True,
        )
        entry, instrument_resolution = _resolve_payment_instrument_source(
            uow.session,
            book_id=book_id,
            entry=entry,
        )
        context = load_compilation_context(
            uow.session,
            book_id=book_id,
            command_id=intent_id,
            transaction_id=transaction_id,
            actor_subject_id=runtime.actor.subject_id,
            entry=entry,
        )
        try:
            plan = compile_entry(entry, context=context)
        except EntryClarificationRequired as error:
            prepared = _nonready_prepared(
                intent_id=intent_id,
                expires_at=expires_at,
                entry=entry,
                context=context,
                status=PreparedEntryStatus.NEEDS_CLARIFICATION,
                clarifications=error.clarifications,
            )
            payload = _nonready_payload(entry, prepared, transaction_id)
        except EntryGatewayError as error:
            if error.code is not EntryErrorCode.UNSUPPORTED:
                raise
            prepared = _nonready_prepared(
                intent_id=intent_id,
                expires_at=expires_at,
                entry=entry,
                context=context,
                status=PreparedEntryStatus.UNSUPPORTED,
                clarifications=(),
            )
            payload = _nonready_payload(entry, prepared, transaction_id)
        else:
            preview, resolved = preview_and_resolved(
                entry,
                context=context,
                plan=plan,
            )
            if instrument_resolution is not None:
                instrument_id, binding_id = instrument_resolution
                resolved = resolved.model_copy(
                    update={
                        "payment_instrument_id": instrument_id,
                        "payment_instrument_binding_id": binding_id,
                    }
                )
            fingerprint = _fingerprint(
                entry,
                preview=preview,
                resolved=resolved,
                provider=runtime.duplicate_key_provider,
            )
            candidates = _duplicate_candidates(
                uow.session,
                book_id=book_id,
                entry=entry,
                fingerprint=fingerprint,
                provider=runtime.duplicate_key_provider,
            )
            decision = decide_duplicate(candidates)
            token = (
                runtime.token_factory()
                if decision.status is PreparedEntryStatus.READY
                else None
            )
            if token is not None and not 32 <= len(token) <= 512:
                raise RuntimeError("entry token factory returned an invalid token")
            clarifications = (
                ()
                if not candidates
                else (
                    Clarification(
                        code=ClarificationCode.DUPLICATE_CONFIRMATION,
                        field="narrative.external_reference",
                        prompt="This entry may duplicate an existing transaction.",
                        choices=tuple(
                            ClarificationChoice(
                                choice_id=str(item.transaction_id),
                                label=item.summary,
                                resolved_id=item.transaction_id,
                            )
                            for item in candidates
                        ),
                    ),
                )
            )
            warnings = (
                ()
                if not candidates
                else (
                    EntryWarning(
                        code=EntryWarningCode.DUPLICATE_CANDIDATE,
                        message="Possible duplicate transaction detected.",
                    ),
                )
            )
            prepared = PreparedEntry(
                intent_id=intent_id,
                status=decision.status,
                commit_token=token,
                expires_at=expires_at,
                preview=preview,
                resolved=resolved,
                warnings=warnings,
                clarifications=clarifications,
            )
            payload = canonical_prepared_payload(
                entry,
                prepared=prepared,
                transaction_id=transaction_id,
                fingerprint=fingerprint,
                external_reference_digest=_external_reference_digest(
                    entry,
                    provider=runtime.duplicate_key_provider,
                ),
            )

        if runtime.protected_content_service is None:
            raise EntryGatewayError(
                EntryErrorCode.UNSUPPORTED,
                "protected narrative storage is unavailable",
                retryable=True,
            )
        runtime.protected_content_service.create_or_exact_verify(
            uow.session,
            book_id=book_id,
            sidecar_id=sidecar_id,
            kind="transaction_narrative_v2",
            canonical_plaintext=_narrative_plaintext(entry),
        )

        PreparedEntryIntentRepository(uow.session).insert_or_exact_get(
            ProposedPreparedIntent(
                book_id=book_id,
                actor_id=runtime.actor.subject_id,
                intent_id=intent_id,
                prepared_status=prepared.status.value,
                commit_token_hash=(
                    None
                    if prepared.commit_token is None
                    else hash_commit_token(prepared.commit_token)
                ),
                canonical_payload=payload,
                protected_content_ref=sidecar_id,
                expires_at=expires_at,
            )
        )
        return prepared


def _resolve_payment_instrument_source(
    session: Session,
    *,
    book_id: UUID,
    entry: EverydayEntryInput,
) -> tuple[EverydayEntryInput, tuple[UUID, UUID] | None]:
    reference = (
        entry.payment_instrument
        if isinstance(entry, (ExpenseEntryInput, CreditCardPaymentEntryInput))
        else None
    )
    if reference is None:
        return entry, None
    try:
        instrument, binding = resolve_payment_instrument(
            session,
            book_id=book_id,
            reference=reference,
            asset_code=entry.amount.asset_code,
            occurred_at=entry.occurred_at,
        )
    except PaymentInstrumentError as error:
        raise EntryGatewayError(
            EntryErrorCode.INVALID_INPUT,
            str(error),
            field="payment_instrument",
        ) from error
    if isinstance(entry, CreditCardPaymentEntryInput):
        if binding.binding_role != BindingRole.CARD_LIABILITY.value:
            raise EntryGatewayError(
                EntryErrorCode.INVALID_INPUT,
                "credit-card payments require a statement payment instrument",
                field="payment_instrument",
            )
        normalized = entry.model_copy(
            update={
                "card_account": AccountRef(account_id=binding.account_id),
                "payment_instrument": None,
            }
        )
    else:
        normalized = entry.model_copy(
            update={
                "source_account": AccountRef(account_id=binding.account_id),
                "payment_instrument": None,
            }
        )
    return normalized, (instrument.instrument_id, binding.binding_id)


def load_compilation_context(
    session: Session,
    *,
    book_id: UUID,
    command_id: UUID,
    transaction_id: UUID,
    actor_subject_id: str,
    entry: EverydayEntryInput,
    locked_last_position: int | None = None,
) -> EntryCompilationContext:
    head = session.scalar(
        select(BookEventHeadRecord.last_position).where(
            BookEventHeadRecord.book_id == book_id
        )
    )
    if head is None:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_NOT_FOUND,
            "entry resource was not found",
        )
    if locked_last_position is not None and head != locked_last_position:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "the prepared entry is stale",
        )
    assets = tuple(
        EntryAsset(
            asset_code=row.asset_code,
            kind=row.kind,
            ledger_scale=row.ledger_scale,
            input_scale=row.input_scale,
            minor_unit_scale=row.ledger_scale if row.kind == "fiat" else None,
            status=row.status,
        )
        for row in session.scalars(select(AssetRecord).order_by(AssetRecord.asset_code))
    )
    accounts = tuple(
        EntryAccount(
            account_id=row.account_id,
            book_id=row.book_id,
            display_name=row.current_name,
            asset_code=row.asset_code,
            account_type=AccountType(row.account_type),
            account_subtype=row.account_subtype,
            system_role=AccountSystemRole(row.system_role or "standard"),
            status=row.status,
            last4=derive_account_last4(row.current_name),
        )
        for row in session.scalars(
            select(AccountRecord)
            .where(AccountRecord.book_id == book_id)
            .order_by(AccountRecord.account_id)
        )
    )
    category_rows = tuple(
        session.execute(
            select(CategoryRecord, CategoryVersionRecord)
            .join(
                CategoryVersionRecord,
                (CategoryVersionRecord.book_id == CategoryRecord.book_id)
                & (
                    CategoryVersionRecord.category_id
                    == CategoryRecord.category_id
                )
                & (
                    CategoryVersionRecord.category_version_id
                    == CategoryRecord.current_version_id
                ),
            )
            .where(CategoryRecord.book_id == book_id)
            .order_by(CategoryRecord.category_id)
        )
    )
    names = {record.category_id: record.current_name for record, _ in category_rows}
    parents = {
        record.category_id: record.parent_category_id for record, _ in category_rows
    }
    categories = tuple(
        EntryCategory(
            category_id=record.category_id,
            category_version_id=version.category_version_id,
            book_id=book_id,
            path=_category_path(record.category_id, names=names, parents=parents),
            usage_kind=CategoryUsageKind(version.usage_kind),
            status=record.status,
        )
        for record, version in category_rows
    )
    original = (
        _load_original_entry(session, book_id, entry, categories)
        if isinstance(entry, RefundEntryInput)
        else None
    )
    current_balance = None
    if isinstance(entry, AdjustmentEntryInput):
        asset_code = entry.actual_balance.asset_code
        resolution = resolve_account(
            entry.account,
            accounts=accounts,
            book_id=book_id,
            asset_code=asset_code,
            use=AccountUse.ADJUSTED,
        )
        if resolution.account is not None:
            current_balance = session.scalar(
                select(AccountBalanceRecord.balance_units).where(
                    AccountBalanceRecord.book_id == book_id,
                    AccountBalanceRecord.account_id == resolution.account.account_id,
                    AccountBalanceRecord.asset_code == asset_code,
                )
            )
            current_balance = 0 if current_balance is None else int(current_balance)
    return EntryCompilationContext(
        book_id=book_id,
        command_id=command_id,
        transaction_id=transaction_id,
        actor_subject_id=actor_subject_id,
        locked_last_position=int(head),
        assets=assets,
        accounts=accounts,
        categories=categories,
        current_balance_units=current_balance,
        original_entry=original,
    )


def preview_and_resolved(
    entry: EverydayEntryInput,
    *,
    context: EntryCompilationContext,
    plan: LedgerWritePlan,
) -> tuple[EntryPreview, ResolvedEntryReferences]:
    financial = plan.events[0].payload
    if not isinstance(
        financial,
        (JournalTransactionPosted, CreditCardTransactionRecorded),
    ):
        raise RuntimeError("entry compiler returned an invalid financial event")
    units = int(financial.postings[0].units)
    asset_code = financial.postings[0].asset_code
    asset = next(item for item in context.assets if item.asset_code == asset_code)
    value = _format_units(units, asset.ledger_scale)
    account_by_id = {item.account_id: item for item in context.accounts}
    category_by_id = {item.category_id: item for item in context.categories}
    category_ids: tuple[UUID, ...] = ()
    category_versions: tuple[UUID, ...] = ()
    if len(plan.events) > 1:
        reporting = plan.events[1].payload
        if not isinstance(reporting, ReportingLinesAssigned):
            raise RuntimeError("entry compiler returned an invalid reporting event")
        category_ids = tuple(
            line.dimension_id for line in reporting.lines if line.dimension_id is not None
        )
        category_versions = tuple(line.catalog_id for line in reporting.lines)

    source = destination = funding = card = adjusted = None
    preview_accounts: list[PreviewAccount] = []
    if isinstance(entry, ExpenseEntryInput):
        source = (
            financial.card_account_id
            if isinstance(financial, CreditCardTransactionRecorded)
            else financial.postings[1].account_id
        )
        preview_accounts.append(
            PreviewAccount(
                role="source",
                display_name=account_by_id[source].display_name,
            )
        )
    elif isinstance(entry, IncomeEntryInput):
        destination = financial.postings[0].account_id
        preview_accounts.append(
            PreviewAccount(
                role="destination",
                display_name=account_by_id[destination].display_name,
            )
        )
    elif isinstance(entry, TransferEntryInput):
        destination = financial.postings[0].account_id
        source = financial.postings[1].account_id
        preview_accounts.extend(
            (
                PreviewAccount(
                    role="source",
                    display_name=account_by_id[source].display_name,
                ),
                PreviewAccount(
                    role="destination",
                    display_name=account_by_id[destination].display_name,
                ),
            )
        )
    elif isinstance(entry, CreditCardPaymentEntryInput):
        if entry.source_amount is None:
            assert isinstance(financial, CreditCardTransactionRecorded)
            funding = financial.counter_account_id
            card = financial.card_account_id
        else:
            assert isinstance(financial, JournalTransactionPosted)
            card = financial.postings[0].account_id
            funding = financial.postings[4].account_id
        preview_accounts.extend(
            (
                PreviewAccount(
                    role="funding",
                    display_name=account_by_id[funding].display_name,
                ),
                PreviewAccount(
                    role="card",
                    display_name=account_by_id[card].display_name,
                ),
            )
        )
    elif isinstance(entry, AdjustmentEntryInput):
        adjusted = next(
            posting.account_id
            for posting in financial.postings
            if account_by_id[posting.account_id].system_role
            is AccountSystemRole.STANDARD
        )
        preview_accounts.append(
            PreviewAccount(
                role="adjusted",
                display_name=account_by_id[adjusted].display_name,
            )
        )
    resolved = ResolvedEntryReferences(
        source_account_id=source,
        destination_account_id=destination,
        funding_account_id=funding,
        card_account_id=card,
        adjusted_account_id=adjusted,
        source_trading_account_id=(
            financial.postings[2].account_id
            if isinstance(entry, CreditCardPaymentEntryInput)
            and entry.source_amount is not None
            else None
        ),
        target_trading_account_id=(
            financial.postings[1].account_id
            if isinstance(entry, CreditCardPaymentEntryInput)
            and entry.source_amount is not None
            else None
        ),
        category_ids=category_ids,
        category_version_ids=category_versions,
        original_transaction_id=(
            entry.original_transaction_id
            if isinstance(entry, RefundEntryInput)
            else None
        ),
    )
    source_preview = fee_preview = None
    summary = f"{entry.kind.replace('_', ' ').title()} {value} {asset_code}"
    if (
        isinstance(entry, CreditCardPaymentEntryInput)
        and entry.source_amount is not None
    ):
        source_posting = financial.postings[2]
        fee_posting = financial.postings[3]
        source_asset = next(
            item
            for item in context.assets
            if item.asset_code == source_posting.asset_code
        )
        source_value = _format_units(
            int(source_posting.units), source_asset.ledger_scale
        )
        fee_value = _format_units(int(fee_posting.units), source_asset.ledger_scale)
        gross_value = _format_units(
            int(financial.postings[4].units), source_asset.ledger_scale
        )
        source_preview = PreviewMoney(
            value=source_value,
            asset_code=source_asset.asset_code,
            display=f"{source_value} {source_asset.asset_code}",
        )
        fee_preview = PreviewMoney(
            value=fee_value,
            asset_code=source_asset.asset_code,
            display=f"{fee_value} {source_asset.asset_code}",
        )
        summary = (
            f"Cross-asset card payment {value} {asset_code}; funding total "
            f"{gross_value} {source_asset.asset_code} (principal {source_value}, "
            f"fee {fee_value})"
        )
    preview = EntryPreview(
        kind=entry.kind,
        summary=summary,
        amount=PreviewMoney(
            value=value,
            asset_code=asset_code,
            display=f"{value} {asset_code}",
        ),
        occurred_at=entry.occurred_at,
        source_amount=source_preview,
        fee_amount=fee_preview,
        accounts=tuple(preview_accounts),
        category_paths=tuple(category_by_id[item].path for item in category_ids),
    )
    return preview, resolved


def canonical_prepared_payload(
    entry: EverydayEntryInput,
    *,
    prepared: PreparedEntry,
    transaction_id: UUID,
    fingerprint: bytes,
    external_reference_digest: tuple[str, str, bytes] | None,
) -> dict[str, object]:
    resolved_entry = entry.model_dump(mode="json")
    _retain_safe_narrative_amounts(resolved_entry)
    _remove_source_text(resolved_entry)
    resolved = prepared.resolved
    replacements = {
        "source_account": resolved.source_account_id,
        "destination_account": resolved.destination_account_id,
        "funding_account": resolved.funding_account_id,
        "card_account": resolved.card_account_id,
        "account": resolved.adjusted_account_id,
    }
    for field, identifier in replacements.items():
        if field in resolved_entry and identifier is not None:
            resolved_entry[field] = {"account_id": str(identifier)}
    category_ids = iter(resolved.category_ids)
    if "category" in resolved_entry and resolved_entry.get("category") is not None:
        resolved_entry["category"] = {"category_id": str(next(category_ids))}
    if (
        "fee_category" in resolved_entry
        and resolved_entry.get("fee_category") is not None
    ):
        resolved_entry["fee_category"] = {"category_id": str(next(category_ids))}
    allocations = resolved_entry.get("category_allocations")
    if isinstance(allocations, list):
        for allocation in allocations:
            if isinstance(allocation, dict):
                allocation["category"] = {"category_id": str(next(category_ids))}
    payload: dict[str, object] = {
        "entry": resolved_entry,
        "fingerprint_hmac": fingerprint.hex(),
        "preview": prepared.preview.model_dump(mode="json"),
        "resolved": prepared.resolved.model_dump(mode="json"),
        "transaction_id": str(transaction_id),
    }
    if external_reference_digest is not None:
        provider, kind, digest = external_reference_digest
        payload["source_reference_digest"] = {
            "provider_code": provider,
            "reference_kind": kind,
            "reference_hmac": digest.hex(),
        }
    return payload


def restore_entry(
    payload: object,
    *,
    amount_sources: tuple[NarrativeAmountSource, ...],
) -> EverydayEntryInput:
    if not isinstance(payload, Mapping):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "the prepared entry payload is invalid",
        )
    value = _copy_json(payload)
    _restore_amount_sources(value, amount_sources=amount_sources)
    from pydantic import TypeAdapter

    try:
        return TypeAdapter(EverydayEntryInput).validate_python(value)
    except ValidationError:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "the prepared entry payload is invalid",
        ) from None


def _nonready_prepared(
    *,
    intent_id: UUID,
    expires_at: datetime,
    entry: EverydayEntryInput,
    context: EntryCompilationContext,
    status: PreparedEntryStatus,
    clarifications: tuple[Clarification, ...],
) -> PreparedEntry:
    asset_code = (
        entry.actual_balance.asset_code
        if isinstance(entry, AdjustmentEntryInput)
        else context.original_entry.asset_code
        if isinstance(entry, RefundEntryInput) and entry.amount is None
        and context.original_entry is not None
        else entry.amount.asset_code  # type: ignore[union-attr]
    )
    raw_value = (
        entry.actual_balance.value
        if isinstance(entry, AdjustmentEntryInput)
        else _format_units(
            context.original_entry.units,
            next(
                item.ledger_scale
                for item in context.assets
                if item.asset_code == asset_code
            ),
        )
        if isinstance(entry, RefundEntryInput) and entry.amount is None
        and context.original_entry is not None
        else entry.amount.value  # type: ignore[union-attr]
    )
    preview = EntryPreview(
        kind=entry.kind,
        summary=f"{entry.kind.replace('_', ' ').title()} {raw_value} {asset_code}",
        amount=PreviewMoney(
            value=raw_value,
            asset_code=asset_code,
            display=f"{raw_value} {asset_code}",
        ),
        occurred_at=entry.occurred_at,
    )
    return PreparedEntry(
        intent_id=intent_id,
        status=status,
        expires_at=expires_at,
        preview=preview,
        resolved=ResolvedEntryReferences(
            original_transaction_id=(
                entry.original_transaction_id
                if isinstance(entry, RefundEntryInput)
                else None
            )
        ),
        clarifications=clarifications,
    )


def _nonready_payload(
    entry: EverydayEntryInput,
    prepared: PreparedEntry,
    transaction_id: UUID,
) -> dict[str, object]:
    return {
        "preview": prepared.preview.model_dump(mode="json"),
        "resolved": prepared.resolved.model_dump(mode="json"),
        "transaction_id": str(transaction_id),
    }


def _duplicate_candidates(
    session: Session,
    *,
    book_id: UUID,
    entry: EverydayEntryInput,
    fingerprint: bytes,
    provider: DuplicateDetectionKeyProvider,
) -> tuple[DuplicateCandidate, ...]:
    repository = EverydayEntryDuplicateRepository(session)
    candidates: list[DuplicateCandidate] = []
    external = _external_reference_digest(entry, provider=provider)
    if external is not None:
        provider, kind, digest = external
        evidence = repository.get_external_reference(
            book_id=book_id,
            provider_code=provider,
            reference_kind=kind,
            reference_hmac=digest,
        )
        if evidence is not None:
            candidates.append(
                DuplicateCandidate(
                    transaction_id=evidence.transaction_id,
                    evidence_kind=DuplicateEvidenceKind.EXTERNAL_REFERENCE,
                    summary="An entry with the same protected source reference exists.",
                )
            )
    cutoff = entry.occurred_at - timedelta(days=7)
    candidates.extend(
        DuplicateCandidate(
            transaction_id=item.transaction_id,
            evidence_kind=DuplicateEvidenceKind.SOURCE_FINGERPRINT,
            summary="A recent entry has the same protected source fingerprint.",
        )
        for item in repository.find_source_fingerprints(
            book_id=book_id,
            fingerprint_hmac=fingerprint,
            created_since=cutoff,
        )
    )
    return tuple(
        {item.transaction_id: item for item in candidates}.values()
    )


def _fingerprint(
    entry: EverydayEntryInput,
    *,
    preview: EntryPreview,
    resolved: ResolvedEntryReferences,
    provider: DuplicateDetectionKeyProvider,
) -> bytes:
    narrative = entry.narrative
    return provider.source_fingerprint_digest(
        normalized_parts=(
            entry.kind,
            preview.amount.value,
            preview.amount.asset_code,
            "-" if preview.source_amount is None else preview.source_amount.value,
            "-" if preview.fee_amount is None else preview.fee_amount.value,
            format_utc_microseconds(entry.occurred_at),
            str(
                resolved.source_account_id
                or resolved.destination_account_id
                or resolved.funding_account_id
                or resolved.card_account_id
                or resolved.adjusted_account_id
                or resolved.original_transaction_id
            ),
            str(resolved.payment_instrument_id or "-"),
            "-" if narrative is None or narrative.merchant is None else narrative.merchant,
        ),
    )


def _external_reference_digest(
    entry: EverydayEntryInput,
    *,
    provider: DuplicateDetectionKeyProvider,
) -> tuple[str, str, bytes] | None:
    narrative = entry.narrative
    external = None if narrative is None else narrative.external_reference
    if external is None:
        return None
    return (
        external.provider_code,
        external.kind.value,
        provider.external_reference_digest(
            provider_code=external.provider_code,
            reference_kind=external.kind.value,
            reference=external.reference,
        ),
    )


def _narrative_plaintext(entry: EverydayEntryInput) -> bytes:
    from ...serialization.canonical_json import canonical_json_bytes

    narrative = entry.narrative
    net = (
        entry.actual_balance
        if isinstance(entry, AdjustmentEntryInput)
        else entry.amount
    )
    external = None if narrative is None else narrative.external_reference
    contract = TransactionNarrativeV2(
        amount_sources=_collect_amount_sources(entry),
        merchant=None if narrative is None else narrative.merchant,
        channel=None if narrative is None else narrative.channel,
        note=None if narrative is None else narrative.note,
        external_reference=(
            None
            if external is None
            else NarrativeExternalReference(
                provider_code=external.provider_code,
                kind=external.kind.value,
                reference=external.reference,
            )
        ),
        gross_amount=_narrative_money(
            None if narrative is None else narrative.gross_amount
        ),
        discount_amount=_narrative_money(
            None if narrative is None else narrative.discount_amount
        ),
        net_amount=_narrative_money(net),
    )
    return canonical_json_bytes(contract.model_dump(mode="json"))


def _narrative_money(value) -> NarrativeMoney | None:
    if value is None:
        return None
    return NarrativeMoney(value=value.value, asset_code=value.asset_code)


def _load_original_entry(
    session: Session,
    book_id: UUID,
    entry: RefundEntryInput,
    categories: tuple[EntryCategory, ...],
) -> OriginalEntry:
    try:
        view = get_everyday_entry(
            session,
            book_id,
            entry.original_transaction_id,
        )
    except LookupError:
        raise EntryGatewayError(
            EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
            "original transaction was not found",
            field="original_transaction_id",
        ) from None
    if view.amount is None:
        raise EntryGatewayError(
            EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
            "original transaction amount is unavailable",
            field="original_transaction_id",
        )
    by_id = {item.category_id: item for item in categories}
    allocations = tuple(
        OriginalCategoryAllocation(
            category=by_id[item.category_id],
            units=_parse_units(item.amount.value, item.amount.scale),
        )
        for item in view.category_allocations
        if item.category_id in by_id
    )
    is_card = view.raw_journal.transaction_kind == "credit_card_charge"
    return OriginalEntry(
        transaction_id=view.transaction_id,
        kind="credit_card_charge" if is_card else view.kind.value,
        asset_code=view.amount.asset_code,
        units=_parse_units(view.amount.value, view.amount.scale),
        source_account_id=(
            None if view.payment_account is None else view.payment_account.account_id
        ),
        card_account_id=(
            view.payment_account.account_id
            if is_card and view.payment_account is not None
            else None
        ),
        category_allocations=allocations,
    )


def _category_path(
    category_id: UUID,
    *,
    names: dict[UUID, str],
    parents: dict[UUID, UUID | None],
) -> tuple[str, ...]:
    path: list[str] = []
    seen: set[UUID] = set()
    current: UUID | None = category_id
    while current is not None:
        if current in seen or current not in names:
            raise RuntimeError("category hierarchy is invalid")
        seen.add(current)
        path.append(names[current])
        current = parents[current]
    return tuple(reversed(path))


def _remove_source_text(value: object) -> None:
    if isinstance(value, dict):
        value.pop("source_text", None)
        for child in value.values():
            _remove_source_text(child)
    elif isinstance(value, list):
        for child in value:
            _remove_source_text(child)


def _retain_safe_narrative_amounts(value: dict[str, object]) -> None:
    narrative = value.get("narrative")
    if not isinstance(narrative, dict):
        value["narrative"] = None
        return
    safe_narrative = {
        field: narrative[field]
        for field in ("gross_amount", "discount_amount")
        if narrative.get(field) is not None
    }
    value["narrative"] = safe_narrative or None


def _collect_amount_sources(
    entry: EverydayEntryInput,
) -> tuple[NarrativeAmountSource, ...]:
    sources: list[NarrativeAmountSource] = []

    def append(
        field_path: str,
        amount: MoneyInput | BalanceInput | None,
    ) -> None:
        if amount is not None:
            sources.append(
                NarrativeAmountSource(
                    field_path=field_path,
                    source_text=amount.source_text,
                )
            )

    if isinstance(entry, AdjustmentEntryInput):
        append("actual_balance", entry.actual_balance)
    else:
        append("amount", entry.amount)
        append("source_amount", getattr(entry, "source_amount", None))
        append("fee_amount", getattr(entry, "fee_amount", None))
    for index, allocation in enumerate(
        getattr(entry, "category_allocations", ())
    ):
        append(f"category_allocations.{index}.amount", allocation.amount)
    if entry.narrative is not None:
        append("narrative.gross_amount", entry.narrative.gross_amount)
        append("narrative.discount_amount", entry.narrative.discount_amount)
    return tuple(sources)


def _restore_amount_sources(
    value: object,
    *,
    amount_sources: tuple[NarrativeAmountSource, ...],
) -> None:
    if not isinstance(value, dict):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "the prepared entry payload is invalid",
        )
    targets = _amount_source_targets(value)
    paths = tuple(source.field_path for source in amount_sources)
    if len(paths) != len(set(paths)) or set(paths) != set(targets):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared amount sources do not match the entry shape",
        )
    for source in amount_sources:
        target = targets[source.field_path]
        if "source_text" in target:
            raise EntryGatewayError(
                EntryErrorCode.INTENT_STALE,
                "the prepared entry payload is invalid",
            )
        target["source_text"] = source.source_text


def _amount_source_targets(
    value: dict[str, object],
) -> dict[str, dict[str, object]]:
    targets: dict[str, dict[str, object]] = {}

    def add(field_path: str, candidate: object) -> None:
        if not isinstance(candidate, dict):
            raise EntryGatewayError(
                EntryErrorCode.INTENT_STALE,
                "the prepared entry payload is invalid",
            )
        targets[field_path] = candidate

    for field in ("amount", "actual_balance", "source_amount", "fee_amount"):
        if field in value and value[field] is not None:
            add(field, value[field])
    allocations = value.get("category_allocations", [])
    if not isinstance(allocations, list):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "the prepared entry payload is invalid",
        )
    for index, allocation in enumerate(allocations):
        if not isinstance(allocation, dict) or "amount" not in allocation:
            raise EntryGatewayError(
                EntryErrorCode.INTENT_STALE,
                "the prepared entry payload is invalid",
            )
        add(f"category_allocations.{index}.amount", allocation["amount"])
    narrative = value.get("narrative")
    if narrative is not None:
        if not isinstance(narrative, dict):
            raise EntryGatewayError(
                EntryErrorCode.INTENT_STALE,
                "the prepared entry payload is invalid",
            )
        for field in ("gross_amount", "discount_amount"):
            if field in narrative and narrative[field] is not None:
                add(f"narrative.{field}", narrative[field])
    return targets


def _copy_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json(child) for child in value]
    return value


def _format_units(units: int, scale: int) -> str:
    digits = str(units).rjust(scale + 1, "0")
    if scale == 0:
        return digits
    return f"{digits[:-scale]}.{digits[-scale:]}"


def _parse_units(value: str, scale: int) -> int:
    whole, separator, fraction = value.partition(".")
    if len(fraction) > scale:
        raise RuntimeError("read-model amount exceeds its scale")
    return int(whole + fraction.ljust(scale, "0"))


__all__ = [
    "EntryPreparationRuntime",
    "canonical_prepared_payload",
    "load_compilation_context",
    "prepare_entry",
    "preview_and_resolved",
    "restore_entry",
]
