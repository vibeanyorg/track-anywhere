from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, JSON, String, create_engine, delete, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .attachments import Attachment
from .audit import AuditEvent
from .budgets import BudgetFund
from .categories import Category
from .drafts import DraftTransaction
from .idempotency import CommandReceipt
from .investments import InvestmentEvent
from .ledger import Account, Posting, Transaction
from .security import Actor, Credential
from .users import AppUser


DEFAULT_DATABASE_URL = "sqlite:///./.local/track-anywhere.sqlite3"
ASSET_CODE_LENGTH = 16


class Base(DeclarativeBase):
    pass


class AppStateRecord(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class AccountRecord(Base):
    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
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


class TransactionRecord(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    memo: Mapped[str] = mapped_column(String(256))
    occurred_at: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(256))
    category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reversed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class PostingRecord(Base):
    __tablename__ = "postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"))
    position: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String(80))
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))


class DraftRecord(Base):
    __tablename__ = "drafts"

    draft_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    memo: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(40))
    missing_fields: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer)
    attachment_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class DraftPostingRecord(Base):
    __tablename__ = "draft_postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.draft_id"))
    position: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String(80))
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))


class FundRecord(Base):
    __tablename__ = "funds"

    fund_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    allocated: Mapped[str] = mapped_column(String(80))
    spent: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    flow: Mapped[list[dict[str, str]]] = mapped_column(JSON)


class InvestmentEventRecord(Base):
    __tablename__ = "investment_events"

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    occurred_at: Mapped[str] = mapped_column(String(80))
    memo: Mapped[str] = mapped_column(String(256))
    units: Mapped[str | None] = mapped_column(String(80), nullable=True)
    nav: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class CategoryRecord(Base):
    __tablename__ = "categories"

    category_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    primary: Mapped[str] = mapped_column(String(80))
    secondary: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class AttachmentRecord(Base):
    __tablename__ = "attachments"

    attachment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(180))
    content_hash: Mapped[str] = mapped_column(String(80))
    mime_type: Mapped[str] = mapped_column(String(80))
    original_filename: Mapped[str] = mapped_column(String(240))
    scanner_status: Mapped[str] = mapped_column(String(80))


class CredentialRecord(Base):
    __tablename__ = "credentials"

    token_hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(40))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    issued_at: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[str] = mapped_column(String(80))
    jti: Mapped[str] = mapped_column(String(80))
    revoked_at: Mapped[str | None] = mapped_column(String(80), nullable=True)


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


def database_url_from_env() -> str:
    return os.getenv("TRACK_ANYWHERE_DATABASE_URL", DEFAULT_DATABASE_URL)


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername.split("+", 1)[0] != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_database_engine(database_url: str) -> Engine:
    _ensure_sqlite_parent(database_url)
    url = make_url(database_url)
    is_sqlite = url.drivername.split("+", 1)[0] == "sqlite"
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    kwargs: dict[str, Any] = {"connect_args": connect_args, "future": True}
    if is_sqlite and url.database == ":memory:":
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


class OrmStorage:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or database_url_from_env()
        self.engine = create_database_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def _migrate_schema(self) -> None:
        inspector = inspect(self.engine)
        account_columns = {column["name"] for column in inspector.get_columns("accounts")}
        account_missing_columns = {
            "institution_type": "VARCHAR(40)",
            "subtype": "VARCHAR(64)",
            "institution": "VARCHAR(120)",
        }.items()
        transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
        transaction_missing_columns = {"category_id": "VARCHAR(80)"}.items()
        with self.engine.begin() as connection:
            for column_name, column_type in account_missing_columns:
                if column_name not in account_columns:
                    connection.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {column_type}"))
            for column_name, column_type in transaction_missing_columns:
                if column_name not in transaction_columns:
                    connection.execute(text(f"ALTER TABLE transactions ADD COLUMN {column_name} {column_type}"))

    def load_into(self, service: Any) -> None:
        with self.session_factory() as session:
            service.ledger.accounts = {
                row.account_id: Account(
                    account_id=row.account_id,
                    name=row.name,
                    type=row.type,
                    currency=row.currency,
                    institution_type=row.institution_type,
                    subtype=row.subtype,
                    institution=row.institution,
                    version=row.version,
                )
                for row in session.query(AccountRecord).all()
            }
            service.users.users = {
                row.user_id: AppUser(
                    user_id=row.user_id,
                    username=row.username,
                    display_name=row.display_name,
                    version=row.version,
                )
                for row in session.query(UserRecord).all()
            }
            service.ledger.transactions = self._load_transactions(session)
            service.drafts.drafts = self._load_drafts(session)
            service.budgets.funds = self._load_funds(session)
            service.investments.events = self._load_investment_events(session)
            service.categories.categories = {
                row.category_id: Category(
                    category_id=row.category_id,
                    kind=row.kind,
                    primary=row.primary,
                    secondary=row.secondary,
                    version=row.version,
                )
                for row in session.query(CategoryRecord).all()
            }
            service.attachments.attachments = {
                row.attachment_id: Attachment(
                    attachment_id=row.attachment_id,
                    storage_key=row.storage_key,
                    content_hash=row.content_hash,
                    mime_type=row.mime_type,
                    original_filename=row.original_filename,
                    scanner_status=row.scanner_status,
                )
                for row in session.query(AttachmentRecord).all()
            }
            service.credentials._credentials = self._load_credentials(session)
            service.audit.events = self._load_audit_events(session)
            service.idempotency._receipts = self._load_idempotency_receipts(session)
            service.reconciliation_actions = [row.payload for row in session.query(ReconciliationActionRecord).all()]
            service.adjustment_account_ids = {
                row.currency: row.account_id for row in session.query(AdjustmentAccountRecord).all()
            }
            owner_state = session.get(AppStateRecord, "owner_token")
            if owner_state is not None:
                service.owner_token = str(owner_state.value["token"])

    def save(self, service: Any) -> None:
        with self.session_factory.begin() as session:
            self._clear_snapshot(session)
            session.add(AppStateRecord(key="owner_token", value={"token": service.owner_token}))
            session.add_all(
                AccountRecord(
                    account_id=account.account_id,
                    name=account.name,
                    type=account.type,
                    currency=account.currency,
                    institution_type=account.institution_type,
                    subtype=account.subtype,
                    institution=account.institution,
                    version=account.version,
                )
                for account in service.ledger.accounts.values()
            )
            session.add_all(
                UserRecord(
                    user_id=user.user_id,
                    username=user.username,
                    display_name=user.display_name,
                    version=user.version,
                )
                for user in service.users.users.values()
            )
            self._save_transactions(session, service.ledger.transactions.values())
            self._save_drafts(session, service.drafts.drafts.values())
            self._save_funds(session, service.budgets.funds.values())
            self._save_investment_events(session, service.investments.events.values())
            session.add_all(
                CategoryRecord(
                    category_id=category.category_id,
                    kind=category.kind,
                    primary=category.primary,
                    secondary=category.secondary,
                    version=category.version,
                )
                for category in service.categories.categories.values()
            )
            session.add_all(
                AttachmentRecord(
                    attachment_id=attachment.attachment_id,
                    storage_key=attachment.storage_key,
                    content_hash=attachment.content_hash,
                    mime_type=attachment.mime_type,
                    original_filename=attachment.original_filename,
                    scanner_status=attachment.scanner_status,
                )
                for attachment in service.attachments.attachments.values()
            )
            self._save_credentials(session, service.credentials._credentials.values())
            self._save_audit_events(session, service.audit.events)
            self._save_idempotency_receipts(session, service.idempotency._receipts.values())
            session.add_all(
                ReconciliationActionRecord(
                    reconciliation_id=str(action["reconciliation_id"]),
                    payload=to_jsonable(action),
                )
                for action in service.reconciliation_actions
            )
            session.add_all(
                AdjustmentAccountRecord(currency=currency, account_id=account_id)
                for currency, account_id in service.adjustment_account_ids.items()
            )

    def _clear_snapshot(self, session: Session) -> None:
        for model in (
            PostingRecord,
            DraftPostingRecord,
            ReconciliationActionRecord,
            AdjustmentAccountRecord,
            IdempotencyReceiptRecord,
            AuditEventRecord,
            CredentialRecord,
            AttachmentRecord,
            CategoryRecord,
            InvestmentEventRecord,
            FundRecord,
            DraftRecord,
            TransactionRecord,
            UserRecord,
            AccountRecord,
            AppStateRecord,
        ):
            session.execute(delete(model))

    def _load_transactions(self, session: Session) -> dict[str, Transaction]:
        postings_by_transaction: dict[str, list[Posting]] = {}
        for posting in session.query(PostingRecord).order_by(PostingRecord.position).all():
            postings_by_transaction.setdefault(posting.transaction_id, []).append(
                Posting(posting.account_id, Decimal(posting.amount), posting.currency)
            )
        return {
            row.transaction_id: Transaction(
                transaction_id=row.transaction_id,
                memo=row.memo,
                occurred_at=datetime.fromisoformat(row.occurred_at),
                purpose=row.purpose,
                postings=postings_by_transaction.get(row.transaction_id, []),
                category_id=row.category_id,
                reversed_by=row.reversed_by,
                version=row.version,
            )
            for row in session.query(TransactionRecord).all()
        }

    def _save_transactions(self, session: Session, transactions) -> None:
        for transaction in transactions:
            session.add(
                TransactionRecord(
                    transaction_id=transaction.transaction_id,
                    memo=transaction.memo,
                    occurred_at=transaction.occurred_at.isoformat(),
                    purpose=transaction.purpose,
                    category_id=transaction.category_id,
                    reversed_by=transaction.reversed_by,
                    version=transaction.version,
                )
            )
            for index, posting in enumerate(transaction.postings):
                session.add(
                    PostingRecord(
                        transaction_id=transaction.transaction_id,
                        position=index,
                        account_id=posting.account_id,
                        amount=str(posting.amount),
                        currency=posting.currency,
                    )
                )

    def _load_drafts(self, session: Session) -> dict[str, DraftTransaction]:
        postings_by_draft: dict[str, list[Posting]] = {}
        for posting in session.query(DraftPostingRecord).order_by(DraftPostingRecord.position).all():
            postings_by_draft.setdefault(posting.draft_id, []).append(
                Posting(posting.account_id, Decimal(posting.amount), posting.currency)
            )
        return {
            row.draft_id: DraftTransaction(
                draft_id=row.draft_id,
                memo=row.memo,
                state=row.state,
                proposed_postings=postings_by_draft.get(row.draft_id, []),
                missing_fields=list(row.missing_fields),
                source=row.source,
                confidence=row.confidence,
                version=row.version,
                attachment_id=row.attachment_id,
            )
            for row in session.query(DraftRecord).all()
        }

    def _save_drafts(self, session: Session, drafts) -> None:
        for draft in drafts:
            session.add(
                DraftRecord(
                    draft_id=draft.draft_id,
                    memo=draft.memo,
                    state=draft.state,
                    missing_fields=list(draft.missing_fields),
                    source=draft.source,
                    confidence=draft.confidence,
                    version=draft.version,
                    attachment_id=draft.attachment_id,
                )
            )
            for index, posting in enumerate(draft.proposed_postings):
                session.add(
                    DraftPostingRecord(
                        draft_id=draft.draft_id,
                        position=index,
                        account_id=posting.account_id,
                        amount=str(posting.amount),
                        currency=posting.currency,
                    )
                )

    def _load_funds(self, session: Session) -> dict[str, BudgetFund]:
        return {
            row.fund_id: BudgetFund(
                fund_id=row.fund_id,
                account_id=row.account_id,
                name=row.name,
                currency=row.currency,
                allocated=Decimal(row.allocated),
                spent=Decimal(row.spent),
                version=row.version,
                flow=list(row.flow),
            )
            for row in session.query(FundRecord).all()
        }

    def _save_funds(self, session: Session, funds) -> None:
        session.add_all(
            FundRecord(
                fund_id=fund.fund_id,
                account_id=fund.account_id,
                name=fund.name,
                currency=fund.currency,
                allocated=str(fund.allocated),
                spent=str(fund.spent),
                version=fund.version,
                flow=to_jsonable(fund.flow),
            )
            for fund in funds
        )

    def _load_investment_events(self, session: Session) -> dict[str, InvestmentEvent]:
        return {
            row.event_id: InvestmentEvent(
                event_id=row.event_id,
                account_id=row.account_id,
                event_type=row.event_type,
                amount=Decimal(row.amount),
                currency=row.currency,
                occurred_at=datetime.fromisoformat(row.occurred_at),
                memo=row.memo,
                units=Decimal(row.units) if row.units is not None else None,
                nav=Decimal(row.nav) if row.nav is not None else None,
                version=row.version,
            )
            for row in session.query(InvestmentEventRecord).all()
        }

    def _save_investment_events(self, session: Session, events) -> None:
        session.add_all(
            InvestmentEventRecord(
                event_id=event.event_id,
                account_id=event.account_id,
                event_type=event.event_type,
                amount=str(event.amount),
                currency=event.currency,
                occurred_at=event.occurred_at.isoformat(),
                memo=event.memo,
                units=str(event.units) if event.units is not None else None,
                nav=str(event.nav) if event.nav is not None else None,
                version=event.version,
            )
            for event in events
        )

    def _load_credentials(self, session: Session) -> dict[str, Credential]:
        return {
            row.token_hash: Credential(
                token_hash=row.token_hash,
                actor=Actor(row.actor_id, row.actor_type, frozenset(row.scopes)),
                issued_at=datetime.fromisoformat(row.issued_at),
                expires_at=datetime.fromisoformat(row.expires_at),
                jti=row.jti,
                revoked_at=datetime.fromisoformat(row.revoked_at) if row.revoked_at else None,
            )
            for row in session.query(CredentialRecord).all()
        }

    def _save_credentials(self, session: Session, credentials) -> None:
        session.add_all(
            CredentialRecord(
                token_hash=credential.token_hash,
                actor_id=credential.actor.actor_id,
                actor_type=credential.actor.actor_type,
                scopes=sorted(credential.actor.scopes),
                issued_at=credential.issued_at.isoformat(),
                expires_at=credential.expires_at.isoformat(),
                jti=credential.jti,
                revoked_at=credential.revoked_at.isoformat() if credential.revoked_at else None,
            )
            for credential in credentials
        )

    def _load_audit_events(self, session: Session) -> list[AuditEvent]:
        return [
            AuditEvent(
                event_id=row.event_id,
                operation=row.operation,
                actor_id=row.actor_id,
                actor_type=row.actor_type,
                entity_ref=row.entity_ref,
                details=row.details,
                created_at=row.created_at,
            )
            for row in session.query(AuditEventRecord).all()
        ]

    def _save_audit_events(self, session: Session, events: list[AuditEvent]) -> None:
        session.add_all(
            AuditEventRecord(
                event_id=event.event_id,
                operation=event.operation,
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                entity_ref=event.entity_ref,
                details=to_jsonable(event.details),
                created_at=event.created_at,
            )
            for event in events
        )

    def _load_idempotency_receipts(self, session: Session) -> dict[tuple[str, str, str], CommandReceipt]:
        receipts: dict[tuple[str, str, str], CommandReceipt] = {}
        for row in session.query(IdempotencyReceiptRecord).all():
            receipts[(row.key_hash, row.actor_id, row.operation)] = CommandReceipt(
                key_hash=row.key_hash,
                actor_id=row.actor_id,
                operation=row.operation,
                request_hash=row.request_hash,
                result=row.result,
                replay_count=row.replay_count,
            )
        return receipts

    def _save_idempotency_receipts(self, session: Session, receipts) -> None:
        session.add_all(
            IdempotencyReceiptRecord(
                key_hash=receipt.key_hash,
                actor_id=receipt.actor_id,
                operation=receipt.operation,
                request_hash=receipt.request_hash,
                result=to_jsonable(receipt.result),
                replay_count=receipt.replay_count,
            )
            for receipt in receipts
        )


def new_owner_token() -> str:
    return f"ta_{uuid4().hex}"


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value
