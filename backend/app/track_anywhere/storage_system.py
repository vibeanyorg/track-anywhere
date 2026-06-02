from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import ValidationError
from sqlalchemy import text


STATUS_COUNT_TABLES = (
    "ledger_books",
    "book_members",
    "accounts",
    "assets",
    "categories",
    "category_versions",
    "transaction_lines",
    "transactions",
    "postings",
    "audit_events",
    "idempotency_receipts",
)


class SystemStatusStorageMixin:
    def database_readiness(self) -> dict[str, str | None]:
        with self.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                database = self.engine.url.database or "sqlite"
                schema = None
            else:
                database = connection.execute(text("select current_database()")).scalar_one_or_none()
                schema = connection.execute(text("select current_schema()")).scalar_one_or_none()
            revision = connection.execute(text("select version_num from alembic_version")).scalar_one_or_none()
            return {"database": database, "schema": schema, "alembic_revision": revision}

    def status_table_counts(self, table_names: tuple[str, ...] = STATUS_COUNT_TABLES) -> dict[str, int]:
        with self.engine.connect() as connection:
            return {
                table_name: int(connection.execute(text(f'select count(*) from "{table_name}"')).scalar_one())
                for table_name in table_names
            }

    def rewrite_legacy_postings_to_debit_credit(self, *, book_id: str, candidates: list[dict[str, object]]) -> dict[str, int]:
        confirmed_rewritten = 0
        draft_rewritten = 0
        with self.engine.begin() as connection:
            for candidate in candidates:
                record_ref = _candidate_field(candidate, "transaction_id")
                params = {
                    "book_id": book_id,
                    "record_id": record_ref.removeprefix("draft:"),
                    "position": _candidate_position(candidate),
                    "account_id": _candidate_field(candidate, "account_id"),
                    "currency": _candidate_field(candidate, "currency"),
                    "legacy_amount": _candidate_field(candidate, "legacy_amount"),
                    "target_side": _debit_credit_side(candidate, field="target_side", source="rewrite candidate"),
                    "target_amount": _positive_decimal_string(candidate, field="target_amount", source="rewrite candidate"),
                }
                if record_ref.startswith("draft:"):
                    updated = connection.execute(
                        text(
                            """
                            update draft_postings
                            set
                                side = :target_side,
                                amount = :target_amount,
                                amount_semantics = 'debit_credit'
                            where amount_semantics = 'legacy_signed'
                              and draft_id = :record_id
                              and position = :position
                              and account_id = :account_id
                              and currency = :currency
                              and cast(amount as numeric) = cast(:legacy_amount as numeric)
                              and draft_id in (
                                  select draft_id
                                  from drafts
                                  where book_id = :book_id
                              )
                              and account_id in (
                                  select account_id
                                  from accounts
                                  where book_id = :book_id
                                    and type != 'liability'
                              )
                            """
                        ),
                        params,
                    )
                    draft_rewritten += _single_row_count(record_ref, updated.rowcount)
                else:
                    updated = connection.execute(
                        text(
                            """
                            update postings
                            set
                                side = :target_side,
                                amount = :target_amount,
                                amount_semantics = 'debit_credit'
                            where amount_semantics = 'legacy_signed'
                              and transaction_id = :record_id
                              and position = :position
                              and account_id = :account_id
                              and currency = :currency
                              and cast(amount as numeric) = cast(:legacy_amount as numeric)
                              and transaction_id in (
                                  select transaction_id
                                  from transactions
                                  where book_id = :book_id
                              )
                              and account_id in (
                                  select account_id
                                  from accounts
                                  where book_id = :book_id
                                    and type != 'liability'
                              )
                            """
                        ),
                        params,
                    )
                    confirmed_rewritten += _single_row_count(record_ref, updated.rowcount)
        self.refresh_read_cache_from_storage()
        return {
            "confirmed_postings_rewritten": confirmed_rewritten,
            "draft_postings_rewritten": draft_rewritten,
        }

    def resolve_legacy_liability_posting_reviews(self, *, book_id: str, decisions: list[dict[str, str]]) -> dict[str, int]:
        confirmed_rewritten = 0
        draft_rewritten = 0
        with self.engine.begin() as connection:
            for decision in decisions:
                record_ref = decision["record_ref"]
                params = {
                    "book_id": book_id,
                    "record_id": record_ref.removeprefix("draft:"),
                    "position": _decision_position(decision),
                    "account_id": decision["account_id"],
                    "currency": decision["currency"],
                    "legacy_amount": decision["legacy_amount"],
                    "target_side": _debit_credit_side(decision, field="target_side", source="review decision"),
                    "target_amount": _positive_decimal_string(decision, field="target_amount", source="review decision"),
                }
                if record_ref.startswith("draft:"):
                    updated = connection.execute(
                        text(
                            """
                            update draft_postings
                            set
                                side = :target_side,
                                amount = :target_amount,
                                amount_semantics = 'debit_credit'
                            where amount_semantics = 'legacy_signed'
                              and draft_id = :record_id
                              and position = :position
                              and account_id = :account_id
                              and currency = :currency
                              and cast(amount as numeric) = cast(:legacy_amount as numeric)
                              and draft_id in (
                                  select draft_id
                                  from drafts
                                  where book_id = :book_id
                              )
                              and account_id in (
                                  select account_id
                                  from accounts
                                  where book_id = :book_id
                                    and type = 'liability'
                              )
                            """
                        ),
                        params,
                    )
                    draft_rewritten += _single_row_count(record_ref, updated.rowcount)
                else:
                    updated = connection.execute(
                        text(
                            """
                            update postings
                            set
                                side = :target_side,
                                amount = :target_amount,
                                amount_semantics = 'debit_credit'
                            where amount_semantics = 'legacy_signed'
                              and transaction_id = :record_id
                              and position = :position
                              and account_id = :account_id
                              and currency = :currency
                              and cast(amount as numeric) = cast(:legacy_amount as numeric)
                              and transaction_id in (
                                  select transaction_id
                                  from transactions
                                  where book_id = :book_id
                              )
                              and account_id in (
                                  select account_id
                                  from accounts
                                  where book_id = :book_id
                                    and type = 'liability'
                              )
                            """
                        ),
                        params,
                    )
                    confirmed_rewritten += _single_row_count(record_ref, updated.rowcount)
        self.refresh_read_cache_from_storage()
        return {
            "confirmed_liability_postings_rewritten": confirmed_rewritten,
            "draft_liability_postings_rewritten": draft_rewritten,
        }


def _single_row_count(record_ref: str, rowcount: int | None) -> int:
    count = int(rowcount or 0)
    if count != 1:
        raise ValidationError(f"posting semantics rewrite target must match exactly one posting: {record_ref}")
    return count


def _candidate_field(candidate: dict[str, object], field: str) -> str:
    value = candidate.get(field)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"posting semantics rewrite candidate requires {field}")
    return value


def _candidate_position(candidate: dict[str, object]) -> int:
    value = candidate.get("position")
    if type(value) is int:
        position = value
    elif isinstance(value, str) and value:
        try:
            position = int(value)
        except ValueError as exc:
            raise ValidationError("posting semantics rewrite candidate position must be an integer") from exc
    else:
        raise ValidationError("posting semantics rewrite candidate requires position")
    if position < 0:
        raise ValidationError("posting semantics rewrite candidate position must be non-negative")
    return position


def _decision_position(decision: dict[str, str]) -> int:
    value = decision.get("position")
    if type(value) is int:
        position = value
    elif isinstance(value, str) and value:
        try:
            position = int(value)
        except ValueError as exc:
            raise ValidationError("posting semantics review decision requires position") from exc
    else:
        raise ValidationError("posting semantics review decision requires position")
    if position < 0:
        raise ValidationError("posting semantics review decision position must be non-negative")
    return position


def _debit_credit_side(item: dict[str, object], *, field: str, source: str) -> str:
    value = _candidate_field(item, field)
    if value not in {"debit", "credit"}:
        raise ValidationError(f"posting semantics {source} {field} must be debit or credit")
    return value


def _positive_decimal_string(item: dict[str, object], *, field: str, source: str) -> str:
    value = _candidate_field(item, field)
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"posting semantics {source} {field} must be a decimal string") from exc
    if amount <= Decimal("0"):
        raise ValidationError(f"posting semantics {source} {field} must be positive")
    return str(amount)
