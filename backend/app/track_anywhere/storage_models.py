from __future__ import annotations
from typing import Any
from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ASSET_CODE_LENGTH = 16
class Base(DeclarativeBase):
    pass


class AssetRecord(Base):
    __tablename__ = "assets"

    asset_code: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    scale: Mapped[int] = mapped_column(Integer)
    display_scale: Mapped[int] = mapped_column(Integer, default=2)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)


class AppStateRecord(Base):
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)

class AccountRecord(Base):
    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_book_type_currency", "book_id", "type", "currency"),)

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    institution_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class UserRecord(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)


class AuthIdentityRecord(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_auth_identity_provider_subject"),)

    identity_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(160))
    user_id: Mapped[str] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(240), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)


class PasswordAccountRecord(Base):
    __tablename__ = "password_accounts"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(260))
    role: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)


class TransactionRecord(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("reverses_transaction_id", name="uq_transactions_reverses_transaction_id"),
        Index("ix_transactions_book_occurred", "book_id", "occurred_at", "transaction_id"),
    )

    transaction_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    memo: Mapped[str] = mapped_column(String(256))
    occurred_at: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(256))
    reversed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reverses_transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class PostingRecord(Base):
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("transaction_id", "position", name="uq_postings_transaction_position"),
        Index("ix_postings_account_transaction", "account_id", "transaction_id"),
        CheckConstraint("amount_semantics in ('legacy_signed', 'debit_credit')", name="ck_postings_amount_semantics"),
        CheckConstraint("side is null or side in ('debit', 'credit')", name="ck_postings_side"),
        CheckConstraint(
            "amount_semantics != 'debit_credit' or (side in ('debit', 'credit') and cast(amount as numeric) > 0)",
            name="ck_postings_debit_credit_shape",
        ),
        CheckConstraint(
            "amount_semantics != 'legacy_signed' or cast(amount as numeric) != 0",
            name="ck_postings_legacy_nonzero",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"))
    book_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String(80))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    amount_semantics: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="debit_credit",
        server_default="debit_credit",
    )
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))


class DraftRecord(Base):
    __tablename__ = "drafts"

    draft_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    memo: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(40))
    missing_fields: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer)
    attachment_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class DraftPostingRecord(Base):
    __tablename__ = "draft_postings"
    __table_args__ = (
        CheckConstraint("amount_semantics in ('legacy_signed', 'debit_credit')", name="ck_draft_postings_amount_semantics"),
        CheckConstraint("side is null or side in ('debit', 'credit')", name="ck_draft_postings_side"),
        CheckConstraint(
            "amount_semantics != 'debit_credit' or (side in ('debit', 'credit') and cast(amount as numeric) > 0)",
            name="ck_draft_postings_debit_credit_shape",
        ),
        CheckConstraint(
            "amount_semantics != 'legacy_signed' or cast(amount as numeric) != 0",
            name="ck_draft_postings_legacy_nonzero",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.draft_id"))
    position: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String(80))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    amount_semantics: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="debit_credit",
        server_default="debit_credit",
    )
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))


class FundRecord(Base):
    __tablename__ = "funds"

    fund_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    account_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    allocated: Mapped[str] = mapped_column(String(80))
    spent: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    flow: Mapped[list[dict[str, str]]] = mapped_column(JSON)


class RecurringItemRecord(Base):
    __tablename__ = "recurring_items"

    recurring_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    amount: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(ASSET_CODE_LENGTH), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recurrence: Mapped[dict[str, Any]] = mapped_column(JSON)
    reminder_days: Mapped[list[int]] = mapped_column(JSON)
    anchor_date: Mapped[str] = mapped_column(String(20))
    source_account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_draft_renewal_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_draft_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class InvestmentEventRecord(Base):
    __tablename__ = "investment_events"
    __table_args__ = (Index("ix_investment_events_book_account_occurred", "book_id", "account_id", "occurred_at"),)

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    account_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    occurred_at: Mapped[str] = mapped_column(String(80))
    memo: Mapped[str] = mapped_column(String(256))
    units: Mapped[str | None] = mapped_column(String(80), nullable=True)
    nav: Mapped[str | None] = mapped_column(String(80), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class InvestmentValuationRecord(Base):
    __tablename__ = "investment_valuations"
    __table_args__ = (Index("ix_investment_valuations_book_account_observed", "book_id", "account_id", "observed_at"),)

    valuation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    account_id: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    observed_at: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(80))
    memo: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer)


class CategoryRecord(Base):
    __tablename__ = "categories"

    category_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    kind: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    normalized_name: Mapped[str] = mapped_column(String(80), default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    path_cache: Mapped[str] = mapped_column(String(180), default="")
    icon: Mapped[str | None] = mapped_column(String(80), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="active")
    version: Mapped[int] = mapped_column(Integer)


class TransactionCategoryMigrationAuditRecord(Base):
    __tablename__ = "transaction_category_migration_audit"

    audit_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(80))
    book_id: Mapped[str] = mapped_column(String(80))
    legacy_category_id: Mapped[str] = mapped_column(String(80))
    created_line_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(256))


class CreditCardProfileRecord(Base):
    __tablename__ = "credit_card_profiles"

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    credit_limit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    available_credit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    statement_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_fee: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class AttachmentRecord(Base):
    __tablename__ = "attachments"

    attachment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(180))
    content_hash: Mapped[str] = mapped_column(String(80))
    mime_type: Mapped[str] = mapped_column(String(80))
    original_filename: Mapped[str] = mapped_column(String(240))
    scanner_status: Mapped[str] = mapped_column(String(80))
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    operation: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(40))
    entity_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(80))


class IdempotencyReceiptRecord(Base):
    __tablename__ = "idempotency_receipts"

    key_hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    operation: Mapped[str] = mapped_column(String(120), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(80))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    replay_count: Mapped[int] = mapped_column(Integer)


class ReconciliationActionRecord(Base):
    __tablename__ = "reconciliation_actions"

    reconciliation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AdjustmentAccountRecord(Base):
    __tablename__ = "adjustment_accounts"

    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
