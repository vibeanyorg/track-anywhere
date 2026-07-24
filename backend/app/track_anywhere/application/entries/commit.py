from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hmac
import json
from typing import Callable
from uuid import UUID

from pydantic import ValidationError

from ...infrastructure.db.repositories.entries import (
    EverydayEntryDuplicateRepository,
    PreparedEntryIntentRepository,
    ProposedExternalReference,
    ProposedSourceFingerprint,
    hash_commit_token,
)
from ...infrastructure.db.repositories.privacy import ProtectedContentRepository
from ...serialization.canonical_json import JSONValue, canonical_json_bytes
from ..command_bus import execute_financial
from ..event_batch import AppendBatchResult
from ..idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyValidationError,
)
from ..journal.post_transaction import authorize_journal_write
from ..ledger_committer import (
    LedgerCommitter,
    LedgerWritePlan,
    LockedBookHead,
)
from ..privacy.service import ProtectedContentConflict, ProtectedContentService
from ..privacy.protected_content import TransactionNarrativeV2
from ..unit_of_work import UnitOfWork
from .compiler import compile_entry
from .contracts import (
    CommitEntryInput,
    CommittedEntry,
)
from .errors import EntryErrorCode, EntryGatewayError
from .prepare import (
    UnitOfWorkFactory,
    load_compilation_context,
    preview_and_resolved,
    restore_entry,
)


Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class CommitPreparedEntryCommand:
    book_id: UUID
    command_id: UUID
    intent_id: UUID
    commit_token_hash: bytes = field(repr=False)
    operation: str = field(default="everyday_entry.commit", init=False)

    def __post_init__(self) -> None:
        if any(
            type(value) is not UUID
            for value in (self.book_id, self.command_id, self.intent_id)
        ):
            raise IdempotencyValidationError("entry commit identifiers are invalid")
        if type(self.commit_token_hash) is not bytes or len(self.commit_token_hash) != 32:
            raise IdempotencyValidationError("entry commit token digest is invalid")

    def idempotency_payload(self) -> dict[str, JSONValue]:
        return {
            "intent_id": str(self.intent_id),
            "commit_token_hash": self.commit_token_hash.hex(),
        }


@dataclass(frozen=True, slots=True)
class EntryCommitRuntime:
    actor: CommandActor
    uow_factory: UnitOfWorkFactory
    ledger_committer: LedgerCommitter
    protected_content_service: ProtectedContentService | None
    clock: Clock = lambda: datetime.now(UTC)
    max_attempts: int = 3


def commit_entry(
    *,
    book_id: UUID,
    command: CommitEntryInput,
    runtime: EntryCommitRuntime,
) -> CommittedEntry:
    received = CommitPreparedEntryCommand(
        book_id=book_id,
        command_id=command.request_id,
        intent_id=command.intent_id,
        commit_token_hash=hash_commit_token(command.commit_token),
    )

    def handler(
        dispatched: object,
        uow: UnitOfWork,
        locked_head: LockedBookHead,
    ) -> LedgerWritePlan:
        if dispatched is not received:
            raise IdempotencyValidationError("unexpected entry commit command")
        return _build_commit_plan(
            received,
            uow,
            locked_head,
            actor=runtime.actor,
            protected_content_service=runtime.protected_content_service,
            committed_at=runtime.clock(),
        )

    outcome = execute_financial(
        received,
        raw_key=str(command.request_id),
        actor=runtime.actor,
        authorize=authorize_journal_write,
        handler=handler,
        uow_factory=runtime.uow_factory,
        ledger_committer=runtime.ledger_committer,
        max_attempts=runtime.max_attempts,
    )
    return _committed_entry(outcome)


def _build_commit_plan(
    command: CommitPreparedEntryCommand,
    uow: UnitOfWork,
    locked_head: LockedBookHead,
    *,
    actor: CommandActor,
    protected_content_service: ProtectedContentService | None,
    committed_at: datetime,
) -> LedgerWritePlan:
    repository = PreparedEntryIntentRepository(uow.session)
    snapshot = repository.get(
        book_id=command.book_id,
        actor_id=actor.subject_id,
        intent_id=command.intent_id,
    )
    if (
        snapshot is None
        or snapshot.commit_token_hash is None
        or not hmac.compare_digest(
            snapshot.commit_token_hash,
            command.commit_token_hash,
        )
    ):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_NOT_FOUND,
            "entry intent was not found",
        )
    now = committed_at
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("entry clock must return an aware datetime")
    if snapshot.expires_at <= now:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_EXPIRED,
            "entry intent has expired",
        )
    if snapshot.prepared_status != "ready":
        raise EntryGatewayError(
            EntryErrorCode.INTENT_NOT_READY,
            "entry intent is not ready to commit",
        )
    if snapshot.lifecycle_status != "created":
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "entry intent is no longer available",
        )
    payload = snapshot.canonical_payload
    transaction_id = _payload_uuid(payload, "transaction_id")
    sidecar, amount_sources = _prepared_narrative(
        uow,
        book_id=command.book_id,
        protected_content_ref=snapshot.protected_content_ref,
        service=protected_content_service,
    )
    entry = restore_entry(
        payload.get("entry"),
        amount_sources=amount_sources,
    )
    try:
        context = load_compilation_context(
            uow.session,
            book_id=command.book_id,
            command_id=command.command_id,
            transaction_id=transaction_id,
            actor_subject_id=actor.subject_id,
            entry=entry,
            locked_last_position=locked_head.last_position,
        )
        plan = compile_entry(entry, context=context)
        preview, resolved = preview_and_resolved(entry, context=context, plan=plan)
    except EntryGatewayError as error:
        if error.code is EntryErrorCode.INTENT_STALE:
            raise
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "entry accounts or categories changed after prepare",
        ) from error
    if (
        preview.model_dump(mode="json") != _thaw_json(payload.get("preview"))
        or resolved.model_dump(mode="json") != _thaw_json(payload.get("resolved"))
    ):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "entry accounts or categories changed after prepare",
        )
    _recheck_duplicates(
        uow.session,
        book_id=command.book_id,
        transaction_id=transaction_id,
        payload=payload,
        created_since=entry.occurred_at - timedelta(days=7),
    )
    claimed = repository.claim_ready(
        book_id=command.book_id,
        actor_id=actor.subject_id,
        intent_id=command.intent_id,
        commit_token_hash=command.commit_token_hash,
        request_id=command.command_id,
        transaction_id=transaction_id,
    )
    if claimed is None:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "entry intent could not be claimed",
        )

    plan = _attach_narrative(plan, sidecar.sidecar_id)

    finalizer = _entry_finalizer(
        book_id=command.book_id,
        transaction_id=transaction_id,
        intent_id=command.intent_id,
        payload=payload,
        prior=plan.post_projection_finalizer,
    )
    result = CommittedEntry(
        intent_id=command.intent_id,
        request_id=command.command_id,
        transaction_id=transaction_id,
        committed_at=committed_at,
        preview=preview,
    )
    return replace(
        plan,
        response_schema_version=1,
        status_code=201,
        body=result.model_dump(mode="json"),
        post_projection_finalizer=finalizer,
    )


def _attach_narrative(
    plan: LedgerWritePlan,
    sidecar_id: UUID,
) -> LedgerWritePlan:
    first, *rest = plan.events
    if not hasattr(first.payload, "description_ref"):
        raise RuntimeError("entry financial event cannot reference narrative")
    payload = first.payload.model_copy(update={"description_ref": sidecar_id})
    return replace(
        plan,
        events=(replace(first, payload=payload), *rest),
    )


def _prepared_narrative(
    uow: UnitOfWork,
    *,
    book_id: UUID,
    protected_content_ref: UUID | None,
    service: ProtectedContentService | None,
):
    if protected_content_ref is None:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared narrative is unavailable",
        )
    if service is None:
        raise EntryGatewayError(
            EntryErrorCode.UNSUPPORTED,
            "protected narrative storage is unavailable",
            retryable=True,
        )
    sidecar = ProtectedContentRepository().get(
        uow.session,
        book_id=book_id,
        sidecar_id=protected_content_ref,
    )
    if (
        sidecar is None
        or sidecar.status != "active"
        or sidecar.kind != "transaction_narrative_v2"
    ):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared narrative is unavailable",
        )
    try:
        plaintext = service.decrypt_active(
            sidecar,
            expected_kind="transaction_narrative_v2",
        )
        parsed = json.loads(plaintext.decode("utf-8"))
        narrative = TransactionNarrativeV2.model_validate(parsed)
        if not hmac.compare_digest(
            plaintext,
            canonical_json_bytes(narrative.model_dump(mode="json")),
        ):
            raise ValueError
    except (
        json.JSONDecodeError,
        ProtectedContentConflict,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared narrative is unavailable",
        ) from None
    return sidecar, narrative.amount_sources


def _entry_finalizer(
    *,
    book_id: UUID,
    transaction_id: UUID,
    intent_id: UUID,
    payload: Mapping[str, object],
    prior,
):
    fingerprint = _payload_digest(payload, "fingerprint_hmac")
    external = payload.get("source_reference_digest")

    def finalize(session, appended: AppendBatchResult) -> None:
        if prior is not None:
            prior(session, appended)
        repository = EverydayEntryDuplicateRepository(session)
        if external is not None:
            if not isinstance(external, Mapping):
                raise EntryGatewayError(
                    EntryErrorCode.INTENT_STALE,
                    "prepared duplicate evidence is invalid",
                )
            proposed = ProposedExternalReference(
                book_id=book_id,
                transaction_id=transaction_id,
                source_intent_id=intent_id,
                provider_code=_mapping_text(external, "provider_code"),
                reference_kind=_mapping_text(external, "reference_kind"),
                reference_hmac=_mapping_digest(external, "reference_hmac"),
            )
            evidence, inserted = repository.insert_external_reference_or_get(proposed)
            if not inserted and evidence.transaction_id != transaction_id:
                raise EntryGatewayError(
                    EntryErrorCode.DUPLICATE_SUSPECTED,
                    "entry source reference already exists",
                )
        repository.insert_source_fingerprint(
            ProposedSourceFingerprint(
                book_id=book_id,
                transaction_id=transaction_id,
                source_intent_id=intent_id,
                fingerprint_hmac=fingerprint,
            )
        )

    return finalize


def _recheck_duplicates(
    session,
    *,
    book_id: UUID,
    transaction_id: UUID,
    payload: Mapping[str, object],
    created_since: datetime,
) -> None:
    external = payload.get("source_reference_digest")
    repository = EverydayEntryDuplicateRepository(session)
    if external is not None:
        if not isinstance(external, Mapping):
            raise EntryGatewayError(
                EntryErrorCode.INTENT_STALE,
                "prepared duplicate evidence is invalid",
            )
        evidence = repository.get_external_reference(
            book_id=book_id,
            provider_code=_mapping_text(external, "provider_code"),
            reference_kind=_mapping_text(external, "reference_kind"),
            reference_hmac=_mapping_digest(external, "reference_hmac"),
        )
        if evidence is not None and evidence.transaction_id != transaction_id:
            raise EntryGatewayError(
                EntryErrorCode.DUPLICATE_SUSPECTED,
                "entry source reference already exists",
            )
    fingerprint = _payload_digest(payload, "fingerprint_hmac")
    recent = repository.find_source_fingerprints(
        book_id=book_id,
        fingerprint_hmac=fingerprint,
        created_since=created_since,
    )
    if any(item.transaction_id != transaction_id for item in recent):
        raise EntryGatewayError(
            EntryErrorCode.DUPLICATE_SUSPECTED,
            "a matching recent entry was committed after prepare",
        )


def _committed_entry(outcome: CommandOutcome) -> CommittedEntry:
    try:
        result = CommittedEntry.model_validate(outcome.result.body)
    except ValidationError:
        raise EntryGatewayError(
            EntryErrorCode.COMMIT_OUTCOME_UNKNOWN,
            "entry commit outcome could not be decoded",
            retryable=True,
        ) from None
    return result.model_copy(update={"replayed": outcome.replayed})


def _payload_uuid(payload: Mapping[str, object], key: str) -> UUID:
    try:
        value = UUID(str(payload[key]))
    except (KeyError, TypeError, ValueError):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared entry coordinates are invalid",
        ) from None
    return value


def _payload_digest(payload: Mapping[str, object], key: str) -> bytes:
    try:
        value = bytes.fromhex(str(payload[key]))
    except (KeyError, TypeError, ValueError):
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared duplicate evidence is invalid",
        ) from None
    if len(value) != 32:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared duplicate evidence is invalid",
        )
    return value


def _mapping_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if type(value) is not str or not value:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared duplicate evidence is invalid",
        )
    return value


def _mapping_digest(payload: Mapping[str, object], key: str) -> bytes:
    try:
        value = bytes.fromhex(_mapping_text(payload, key))
    except ValueError:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared duplicate evidence is invalid",
        ) from None
    if len(value) != 32:
        raise EntryGatewayError(
            EntryErrorCode.INTENT_STALE,
            "prepared duplicate evidence is invalid",
        )
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


__all__ = [
    "CommitPreparedEntryCommand",
    "EntryCommitRuntime",
    "commit_entry",
]
