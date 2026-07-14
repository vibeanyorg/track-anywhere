from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from ..serialization.canonical_json import JSONValue, canonical_json_bytes


_REQUEST_HASH_DOMAIN = b"track-anywhere:v2:command-request:sha256:v1\0"


class IdempotencyCommand(Protocol):
    book_id: UUID
    command_id: UUID
    operation: str

    def idempotency_payload(self) -> dict[str, JSONValue]: ...


class IdempotencyValidationError(ValueError):
    pass


class IdempotencyConflict(RuntimeError):
    status_code = 409

    def __init__(self) -> None:
        super().__init__("idempotency key was already used for a different request")


@dataclass(frozen=True, slots=True)
class CommandActor:
    subject_id: str

    def __post_init__(self) -> None:
        if (
            type(self.subject_id) is not str
            or not self.subject_id
            or len(self.subject_id) > 128
        ):
            raise IdempotencyValidationError("actor subject is outside its bound")


@dataclass(frozen=True, slots=True)
class AuthorizationScope:
    book_id: UUID
    actor_subject_id: str
    role: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.book_id) is not UUID:
            raise IdempotencyValidationError("authorization Book must be a UUID")
        if (
            type(self.actor_subject_id) is not str
            or not self.actor_subject_id
            or len(self.actor_subject_id) > 128
        ):
            raise IdempotencyValidationError("authorization actor is invalid")
        if type(self.role) is not str or not self.role:
            raise IdempotencyValidationError("authorization role is invalid")
        if type(self.scopes) is not tuple or any(
            type(scope) is not str or not scope for scope in self.scopes
        ):
            raise IdempotencyValidationError("authorization scopes are invalid")

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "actor_subject_id": self.actor_subject_id,
            "book_id": str(self.book_id),
            "role": self.role,
            "scopes": sorted(set(self.scopes)),
        }


@dataclass(frozen=True, slots=True)
class CommandResult:
    response_schema_version: int
    status_code: int
    body: dict[str, JSONValue] | list[JSONValue]
    first_book_position: int | None = None
    last_book_position: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.response_schema_version) is not int
            or self.response_schema_version < 1
            or self.response_schema_version > 32767
        ):
            raise IdempotencyValidationError("response schema version is invalid")
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise IdempotencyValidationError("response status is invalid")
        if type(self.body) not in {dict, list}:
            raise IdempotencyValidationError("response body must be an object or array")
        canonical_json_bytes(self.body)
        if (self.first_book_position is None) != (self.last_book_position is None):
            raise IdempotencyValidationError("result position range is incomplete")
        if self.first_book_position is not None and (
            type(self.first_book_position) is not int
            or type(self.last_book_position) is not int
            or self.first_book_position < 1
            or self.last_book_position < self.first_book_position
        ):
            raise IdempotencyValidationError("result position range is invalid")


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    result: CommandResult
    replayed: bool


def hash_idempotency_key(raw_key: str) -> bytes:
    if type(raw_key) is not str or not raw_key:
        raise IdempotencyValidationError("idempotency key must be a nonempty string")
    try:
        encoded = raw_key.encode("utf-8")
    except UnicodeError:
        raise IdempotencyValidationError("idempotency key is not valid UTF-8") from None
    return sha256(encoded).digest()


def hash_command_request(
    command: IdempotencyCommand,
    authorization_scope: AuthorizationScope,
) -> bytes:
    book_id = getattr(command, "book_id", None)
    operation = getattr(command, "operation", None)
    if type(book_id) is not UUID or book_id != authorization_scope.book_id:
        raise IdempotencyValidationError("command Book does not match authorization")
    if type(operation) is not str or not operation or len(operation) > 96:
        raise IdempotencyValidationError("command operation is outside its bound")
    payload_factory = getattr(command, "idempotency_payload", None)
    if not callable(payload_factory):
        raise IdempotencyValidationError("command has no idempotency payload")
    payload = payload_factory()
    if type(payload) is not dict:
        raise IdempotencyValidationError("command payload must be an object")
    frozen_request: dict[str, JSONValue] = {
        "authorization": authorization_scope.canonical_value(),
        "book_id": str(book_id),
        "operation": operation,
        "payload": payload,
    }
    return sha256(_REQUEST_HASH_DOMAIN + canonical_json_bytes(frozen_request)).digest()


__all__ = [
    "AuthorizationScope",
    "CommandActor",
    "CommandOutcome",
    "CommandResult",
    "IdempotencyCommand",
    "IdempotencyConflict",
    "IdempotencyValidationError",
    "hash_command_request",
    "hash_idempotency_key",
]
