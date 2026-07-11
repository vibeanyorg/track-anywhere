from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, RLock
from typing import Any

from .errors import IdempotencyConflict
from .security import Actor, hash_secret


@dataclass
class CommandReceipt:
    key_hash: str
    actor_id: str
    operation: str
    request_hash: str
    result: Any
    stored_result: Any | None = None
    replay_count: int = 0
    committed: bool = True

    def __post_init__(self) -> None:
        if self.stored_result is None:
            self.stored_result = self.result


class IdempotencyStore:
    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str, str], CommandReceipt] = {}
        self._dirty_keys: set[tuple[str, str, str]] = set()
        self._active_write = False
        self._condition = Condition(RLock())

    def run(
        self,
        *,
        key: str,
        actor: Actor,
        operation: str,
        request_hash: str,
        fn: Callable[[], Any],
        stored_result_factory: Callable[[Any], Any] | None = None,
    ):
        key_hash = hash_secret(key)
        receipt_key = (key_hash, actor.actor_id, operation)
        with self._condition:
            while self._active_write:
                self._condition.wait()
            self._active_write = True
            receipt = self._receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_hash != request_hash:
                    self._active_write = False
                    self._condition.notify_all()
                    raise IdempotencyConflict("idempotency key reused with different payload")
                receipt.replay_count += 1
                self._dirty_keys.add(receipt_key)
                return receipt.result, True

        try:
            result = fn()
            stored_result = stored_result_factory(result) if stored_result_factory is not None else result
        except BaseException:
            with self._condition:
                self._active_write = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._receipts[receipt_key] = CommandReceipt(
                key_hash=key_hash,
                actor_id=actor.actor_id,
                operation=operation,
                request_hash=request_hash,
                result=result,
                stored_result=stored_result,
                committed=False,
            )
            self._dirty_keys.add(receipt_key)
            self._condition.notify_all()
        return result, False

    def dirty_receipts(self) -> list[CommandReceipt]:
        with self._condition:
            return [self._receipts[key] for key in self._dirty_keys if key in self._receipts]

    def mark_clean(self) -> None:
        with self._condition:
            for key in self._dirty_keys:
                receipt = self._receipts.get(key)
                if receipt is not None:
                    receipt.committed = True
            self._dirty_keys.clear()
            self._active_write = False
            self._condition.notify_all()

    def abort_pending(self) -> None:
        with self._condition:
            for key in tuple(self._dirty_keys):
                receipt = self._receipts.get(key)
                if receipt is not None and not receipt.committed:
                    self._receipts.pop(key, None)
            self._dirty_keys.clear()
            self._active_write = False
            self._condition.notify_all()
