from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Mapping


_SENSITIVE_LABEL_PARTS = (
    "api_key",
    "attachment",
    "authorization",
    "credential",
    "ciphertext",
    "csrf",
    "description",
    "dsn",
    "hash",
    "memo",
    "line_memo",
    "nonce",
    "password",
    "plaintext",
    "purpose",
    "secret",
    "setup_key",
    "token",
)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    counters: Mapping[str, int]
    gauges: Mapping[str, int | float]


class LedgerMetrics:
    """Small in-process collector with bounded names and redacted labels.

    Production exporters can consume snapshots without giving the ledger core a
    dependency on a specific metrics backend.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, int | float] = {}

    def increment(
        self,
        name: str,
        value: int = 1,
        *,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        if type(value) is not int or value < 0:
            raise ValueError("counter increments must be nonnegative integers")
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def gauge(
        self,
        name: str,
        value: int | float,
        *,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        if type(value) not in (int, float):
            raise TypeError("gauge value must be an integer or float")
        key = _metric_key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                counters=MappingProxyType(dict(self._counters)),
                gauges=MappingProxyType(dict(self._gauges)),
            )


def _metric_key(name: str, labels: Mapping[str, object] | None) -> str:
    if type(name) is not str or not name or len(name) > 128:
        raise ValueError("metric name is outside its allowed bound")
    if labels is None:
        return name
    safe: list[tuple[str, str]] = []
    for raw_key, raw_value in labels.items():
        if type(raw_key) is not str or not raw_key or len(raw_key) > 64:
            raise ValueError("metric label name is outside its allowed bound")
        lowered = raw_key.lower()
        if any(part in lowered for part in _SENSITIVE_LABEL_PARTS):
            value = "[REDACTED]"
        else:
            value = str(raw_value)
            if len(value) > 96:
                value = value[:93] + "..."
        safe.append((raw_key, value))
    if not safe:
        return name
    rendered = ",".join(f"{key}={value}" for key, value in sorted(safe))
    return f"{name}{{{rendered}}}"


__all__ = ["LedgerMetrics", "MetricsSnapshot"]
