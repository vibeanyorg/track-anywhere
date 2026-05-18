from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from .errors import NotFound, ValidationError


@dataclass
class Recurrence:
    type: str
    day: int
    month: int | None = None


@dataclass
class RecurringItem:
    recurring_id: str
    name: str
    kind: str
    status: str
    recurrence: Recurrence
    reminder_days: list[int]
    anchor_date: date
    amount: Decimal | None = None
    currency: str | None = None
    provider: str | None = None
    reference: str | None = None
    source_account_id: str | None = None
    category_id: str | None = None
    last_draft_renewal_date: date | None = None
    last_draft_id: str | None = None
    version: int = 1


class RecurringBook:
    def __init__(self) -> None:
        self.items: dict[str, RecurringItem] = {}

    def create(
        self,
        *,
        name: str,
        kind: str,
        recurrence: Recurrence,
        reminder_days: list[int],
        anchor_date: date,
        amount: Decimal | None = None,
        currency: str | None = None,
        provider: str | None = None,
        reference: str | None = None,
        source_account_id: str | None = None,
        category_id: str | None = None,
    ) -> RecurringItem:
        item = RecurringItem(
            recurring_id=f"rec_{uuid4().hex}",
            name=_clean(name, "name"),
            kind=kind,
            status="active",
            amount=amount,
            currency=currency,
            provider=_clean_optional(provider),
            reference=_clean_optional(reference),
            recurrence=recurrence,
            reminder_days=validate_reminder_days(reminder_days),
            anchor_date=anchor_date,
            source_account_id=source_account_id,
            category_id=category_id,
        )
        validate_recurring_item(item)
        self.items[item.recurring_id] = item
        return item

    def get(self, recurring_id: str) -> RecurringItem:
        try:
            return self.items[recurring_id]
        except KeyError as exc:
            raise NotFound(f"recurring item not found: {recurring_id}") from exc

    def list(self, *, status: str | None = None, kind: str | None = None) -> list[RecurringItem]:
        items = list(self.items.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        return sorted(items, key=lambda item: (item.status, item.name, item.recurring_id))


def validate_recurring_item(item: RecurringItem) -> None:
    if item.kind not in {"paid", "reminder_only"}:
        raise ValidationError("recurring kind must be paid or reminder_only")
    if item.status not in {"active", "paused", "cancelled"}:
        raise ValidationError("recurring status must be active, paused, or cancelled")
    validate_recurrence(item.recurrence)
    if not _matches_recurrence(item.anchor_date, item.recurrence):
        raise ValidationError("anchor_date must match recurrence")
    if item.kind == "paid":
        missing = [
            field
            for field, value in {
                "amount": item.amount,
                "currency": item.currency,
                "source_account_id": item.source_account_id,
                "category_id": item.category_id,
            }.items()
            if value in (None, "")
        ]
        if missing:
            raise ValidationError(f"paid recurring item missing fields: {missing}")


def validate_recurrence(recurrence: Recurrence) -> None:
    if recurrence.type == "monthly_day":
        if recurrence.month is not None:
            raise ValidationError("monthly recurrence must not include month")
        if recurrence.day < 1 or recurrence.day > 28:
            raise ValidationError("monthly recurrence day must be between 1 and 28")
        return
    if recurrence.type == "yearly_date":
        if recurrence.month is None or recurrence.month < 1 or recurrence.month > 12:
            raise ValidationError("yearly recurrence month must be between 1 and 12")
        if recurrence.month == 2 and recurrence.day == 29:
            raise ValidationError("yearly recurrence does not support leap day")
        try:
            date(2025, recurrence.month, recurrence.day)
        except ValueError as exc:
            raise ValidationError("yearly recurrence date is invalid") from exc
        return
    raise ValidationError("recurrence type must be monthly_day or yearly_date")


def validate_reminder_days(days: list[int]) -> list[int]:
    if not days:
        raise ValidationError("at least one reminder day is required")
    unique = sorted(set(days), reverse=True)
    if len(unique) != len(days):
        raise ValidationError("reminder days must be unique")
    if any(day < 1 or day > 365 for day in unique):
        raise ValidationError("reminder days must be between 1 and 365")
    return unique


def next_renewal_date(item: RecurringItem, as_of: date) -> date:
    target = max(as_of, item.anchor_date)
    candidate = _candidate_for_year_or_month(item, target)
    if candidate < target:
        return _advance(candidate, item.recurrence)
    return candidate


def last_renewal_date(item: RecurringItem, as_of: date) -> date | None:
    if as_of < item.anchor_date:
        return None
    candidate = _candidate_for_year_or_month(item, as_of)
    if candidate > as_of:
        candidate = _retreat(candidate, item.recurrence)
    return candidate if candidate >= item.anchor_date else None


def due_reminders(item: RecurringItem, as_of: date, window_days: int) -> list[dict[str, object]]:
    if item.status != "active":
        return []
    through = as_of + timedelta(days=window_days)
    renewal = next_renewal_date(item, as_of)
    results = []
    for lead_days in item.reminder_days:
        reminder_date = renewal - timedelta(days=lead_days)
        if as_of <= reminder_date <= through:
            results.append({"renewal_date": renewal, "reminder_date": reminder_date, "lead_days": lead_days})
    return results


def _candidate_for_year_or_month(item: RecurringItem, as_of: date) -> date:
    recurrence = item.recurrence
    if recurrence.type == "monthly_day":
        return date(as_of.year, as_of.month, recurrence.day)
    month = recurrence.month
    if month is None:
        raise ValidationError("yearly recurrence lost month")
    return date(as_of.year, month, recurrence.day)


def _advance(value: date, recurrence: Recurrence) -> date:
    if recurrence.type == "monthly_day":
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        return date(year, month, recurrence.day)
    return date(value.year + 1, value.month, value.day)


def _retreat(value: date, recurrence: Recurrence) -> date:
    if recurrence.type == "monthly_day":
        year = value.year - (1 if value.month == 1 else 0)
        month = 12 if value.month == 1 else value.month - 1
        return date(year, month, recurrence.day)
    return date(value.year - 1, value.month, value.day)


def _matches_recurrence(value: date, recurrence: Recurrence) -> bool:
    if recurrence.type == "monthly_day":
        return value.day == recurrence.day
    return value.month == recurrence.month and value.day == recurrence.day


def _clean(value: str, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValidationError(f"{field_name} must not be blank")
    return normalized


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None
