from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from typing import Protocol


class AuthThrottle(Protocol):
    def check(self, client: str, subject: str) -> int | None: ...

    def reset(self, subject: str) -> None: ...


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class InMemoryAuthThrottle:
    """Bound expensive auth work for the supported single-app deployment."""

    def __init__(
        self,
        *,
        client_capacity: int = 30,
        client_refill_per_second: float = 0.5,
        subject_capacity: int = 8,
        subject_refill_per_second: float = 1 / 30,
        max_clients: int = 1024,
        max_subjects: int = 1024,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            client_capacity < 1
            or client_refill_per_second <= 0
            or subject_capacity < 1
            or subject_refill_per_second <= 0
            or max_clients < 1
            or max_subjects < 1
        ):
            raise ValueError("authentication throttle limits must be positive")
        self._client_capacity = client_capacity
        self._client_refill = client_refill_per_second
        self._subject_capacity = subject_capacity
        self._subject_refill = subject_refill_per_second
        self._max_clients = max_clients
        self._max_subjects = max_subjects
        self._clock = clock
        self._clients: OrderedDict[str, _Bucket] = OrderedDict()
        self._subjects: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = Lock()

    def check(self, client: str, subject: str) -> int | None:
        self._validate_key(client, label="client", max_length=64)
        self._validate_key(subject, label="subject", max_length=320)
        now = self._clock()
        with self._lock:
            client_bucket = self._bucket(
                self._clients,
                client,
                capacity=self._client_capacity,
                max_entries=self._max_clients,
                now=now,
            )
            subject_bucket = self._bucket(
                self._subjects,
                subject,
                capacity=self._subject_capacity,
                max_entries=self._max_subjects,
                now=now,
            )
            client_retry = self._retry_after(
                client_bucket,
                capacity=self._client_capacity,
                refill_per_second=self._client_refill,
                now=now,
            )
            subject_retry = self._retry_after(
                subject_bucket,
                capacity=self._subject_capacity,
                refill_per_second=self._subject_refill,
                now=now,
            )
            if client_retry is not None or subject_retry is not None:
                return max(client_retry or 0, subject_retry or 0)
            client_bucket.tokens -= 1
            subject_bucket.tokens -= 1
            return None

    def reset(self, subject: str) -> None:
        self._validate_key(subject, label="subject", max_length=320)
        with self._lock:
            self._subjects.pop(subject, None)

    @staticmethod
    def _validate_key(value: str, *, label: str, max_length: int) -> None:
        if type(value) is not str or not value or len(value) > max_length:
            raise ValueError(f"authentication throttle {label} is invalid")

    @staticmethod
    def _bucket(
        buckets: OrderedDict[str, _Bucket],
        key: str,
        *,
        capacity: int,
        max_entries: int,
        now: float,
    ) -> _Bucket:
        bucket = buckets.get(key)
        if bucket is not None:
            buckets.move_to_end(key)
            return bucket
        if len(buckets) >= max_entries:
            buckets.popitem(last=False)
        bucket = _Bucket(float(capacity), now)
        buckets[key] = bucket
        return bucket

    @staticmethod
    def _retry_after(
        bucket: _Bucket,
        *,
        capacity: int,
        refill_per_second: float,
        now: float,
    ) -> int | None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(
            float(capacity),
            bucket.tokens + elapsed * refill_per_second,
        )
        bucket.updated_at = now
        if bucket.tokens >= 1:
            return None
        return max(1, ceil((1 - bucket.tokens) / refill_per_second))


__all__ = ["AuthThrottle", "InMemoryAuthThrottle"]
