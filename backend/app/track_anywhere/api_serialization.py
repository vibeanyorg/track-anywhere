from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .ledger import Transaction
from .posting_semantics_views import transaction_posting_semantics


def serialize(value: Any) -> Any:
    if isinstance(value, Transaction):
        return _serialize_transaction(value)
    if is_dataclass(value):
        return {key: serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _serialize_transaction(transaction: Transaction) -> dict[str, Any]:
    payload = {key: serialize(item) for key, item in asdict(transaction).items()}
    payload["posting_semantics"] = transaction_posting_semantics(transaction)
    return payload
