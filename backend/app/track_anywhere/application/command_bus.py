from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from .idempotency import (
    AuthorizationScope,
    CommandActor,
    CommandOutcome,
    CommandResult,
    IdempotencyCommand,
    IdempotencyValidationError,
    hash_command_request,
    hash_idempotency_key,
)
from .ledger_committer import LedgerCommitter, LedgerWritePlan, LockedBookHead
from .unit_of_work import UnitOfWork
from ..infrastructure.db.command_receipts import (
    CommandReceiptRepository,
    ReceiptScope,
)


Authorize = Callable[..., AuthorizationScope]
Handler = Callable[[IdempotencyCommand, UnitOfWork], CommandResult]
UnitOfWorkFactory = Callable[[], UnitOfWork]
FinancialHandler = Callable[
    [IdempotencyCommand, UnitOfWork, LockedBookHead], LedgerWritePlan
]

_RETRYABLE_TRANSACTION_STATES = frozenset({"40001", "40P01"})


def execute(
    command: IdempotencyCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    authorize: Authorize,
    handler: Handler,
    uow_factory: UnitOfWorkFactory,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(actor) is not CommandActor:
        raise IdempotencyValidationError("actor must be a CommandActor")
    if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
        raise IdempotencyValidationError("max_attempts is outside its bound")
    key_hash = hash_idempotency_key(raw_key)
    for attempt in range(max_attempts):
        try:
            return _execute_once(
                command,
                key_hash=key_hash,
                actor=actor,
                authorize=authorize,
                handler=handler,
                uow_factory=uow_factory,
            )
        except DBAPIError as error:
            if attempt + 1 >= max_attempts or not _is_retryable(error):
                raise
    raise AssertionError("unreachable command retry state")


def execute_financial(
    command: IdempotencyCommand,
    *,
    raw_key: str,
    actor: CommandActor,
    authorize: Authorize,
    handler: FinancialHandler,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
    max_attempts: int = 3,
) -> CommandOutcome:
    if type(actor) is not CommandActor:
        raise IdempotencyValidationError("actor must be a CommandActor")
    if not isinstance(ledger_committer, LedgerCommitter):
        raise IdempotencyValidationError("ledger_committer is required")
    if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
        raise IdempotencyValidationError("max_attempts is outside its bound")
    key_hash = hash_idempotency_key(raw_key)
    for attempt in range(max_attempts):
        try:
            return _execute_financial_once(
                command,
                key_hash=key_hash,
                actor=actor,
                authorize=authorize,
                handler=handler,
                uow_factory=uow_factory,
                ledger_committer=ledger_committer,
            )
        except DBAPIError as error:
            if attempt + 1 >= max_attempts or not _is_retryable(error):
                raise
    raise AssertionError("unreachable financial command retry state")


def _execute_once(
    command: IdempotencyCommand,
    *,
    key_hash: bytes,
    actor: CommandActor,
    authorize: Authorize,
    handler: Handler,
    uow_factory: UnitOfWorkFactory,
) -> CommandOutcome:
    book_id = getattr(command, "book_id", None)
    command_id = getattr(command, "command_id", None)
    operation = getattr(command, "operation", None)
    if type(book_id) is not UUID or type(command_id) is not UUID:
        raise IdempotencyValidationError("command identifiers must be UUIDs")
    if type(operation) is not str or not operation or len(operation) > 96:
        raise IdempotencyValidationError("command operation is outside its bound")

    with uow_factory() as uow:
        authorization_scope = authorize(
            uow.session,
            actor,
            book_id,
            lock_membership=True,
        )
        if type(authorization_scope) is not AuthorizationScope:
            raise IdempotencyValidationError(
                "authorize must return an AuthorizationScope"
            )
        if authorization_scope.actor_subject_id != actor.subject_id:
            raise IdempotencyValidationError("authorization actor does not match")
        request_hash = hash_command_request(command, authorization_scope)
        receipt_scope = ReceiptScope(
            actor_subject_id=actor.subject_id,
            book_id=book_id,
            operation=operation,
            idempotency_key_hash=key_hash,
        )
        receipts = CommandReceiptRepository(uow.session)
        reservation = receipts.reserve_or_lock(
            receipt_scope,
            request_hash=request_hash,
            command_id=command_id,
        )
        if not reservation.created:
            return CommandOutcome(
                result=reservation.replay_or_conflict(request_hash),
                replayed=True,
            )

        result: Any = handler(command, uow)
        if type(result) is not CommandResult:
            raise IdempotencyValidationError("handler must return a CommandResult")
        receipts.complete(receipt_scope, result)
        return CommandOutcome(result=result, replayed=False)


def _execute_financial_once(
    command: IdempotencyCommand,
    *,
    key_hash: bytes,
    actor: CommandActor,
    authorize: Authorize,
    handler: FinancialHandler,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
) -> CommandOutcome:
    book_id = getattr(command, "book_id", None)
    command_id = getattr(command, "command_id", None)
    operation = getattr(command, "operation", None)
    if type(book_id) is not UUID or type(command_id) is not UUID:
        raise IdempotencyValidationError("command identifiers must be UUIDs")
    if type(operation) is not str or not operation or len(operation) > 96:
        raise IdempotencyValidationError("command operation is outside its bound")

    with uow_factory() as uow:
        authorization_scope = authorize(
            uow.session,
            actor,
            book_id,
            lock_membership=True,
        )
        if type(authorization_scope) is not AuthorizationScope:
            raise IdempotencyValidationError(
                "authorize must return an AuthorizationScope"
            )
        if authorization_scope.actor_subject_id != actor.subject_id:
            raise IdempotencyValidationError("authorization actor does not match")
        request_hash = hash_command_request(command, authorization_scope)
        receipt_scope = ReceiptScope(
            actor_subject_id=actor.subject_id,
            book_id=book_id,
            operation=operation,
            idempotency_key_hash=key_hash,
        )
        receipts = CommandReceiptRepository(uow.session)
        reservation = receipts.reserve_or_lock(
            receipt_scope,
            request_hash=request_hash,
            command_id=command_id,
        )
        if not reservation.created:
            return CommandOutcome(
                result=reservation.replay_or_conflict(request_hash),
                replayed=True,
            )

        locked_head = ledger_committer.execute_under_book_lock(uow.session, book_id)
        plan = handler(command, uow, locked_head)
        if type(plan) is not LedgerWritePlan:
            raise IdempotencyValidationError(
                "financial handler must return a write plan"
            )
        if any(
            event.command_id != command_id or event.actor_subject_id != actor.subject_id
            for event in plan.events
        ):
            raise IdempotencyValidationError(
                "financial event identity does not match the command"
            )
        appended = ledger_committer.append_and_project(
            uow.session,
            locked_head=locked_head,
            expected_stream_versions=plan.expected_stream_versions,
            events=plan.events,
        )
        result = plan.to_result(appended)
        receipts.complete(receipt_scope, result)
        return CommandOutcome(result=result, replayed=False)


def _is_retryable(error: DBAPIError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    return error.connection_invalidated or sqlstate in _RETRYABLE_TRANSACTION_STATES


__all__ = ["execute", "execute_financial"]
