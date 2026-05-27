from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .storage_json import to_jsonable


class ServiceRequestHashing:
    @staticmethod
    def _hash_request_payload(
        operation: str,
        payload: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Return a stable idempotency hash for the client supplied request.

        Mutation commands may contain server-side dynamic defaults such as
        ``occurred_at=datetime.now(...)``. Hashing the validated command would
        make two identical retries look different whenever the client omitted
        that field, so the idempotency boundary is the canonical raw request
        payload plus immutable route/context fields.
        """

        envelope: dict[str, Any] = {"operation": operation, "payload": payload or {}}
        if extra:
            envelope["extra"] = extra
        encoded = json.dumps(
            to_jsonable(envelope),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_command(command) -> str:
        return ServiceRequestHashing._hash_request_payload(
            command.__class__.__name__,
            command.model_dump(mode="python", exclude_unset=True),
        )

    @staticmethod
    def _hash_command_payload(command, extra: dict[str, Any]) -> str:
        return ServiceRequestHashing._hash_request_payload(
            command.__class__.__name__,
            command.model_dump(mode="python", exclude_unset=True),
            extra,
        )
