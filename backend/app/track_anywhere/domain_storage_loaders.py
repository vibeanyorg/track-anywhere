from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from .books import BookMember, LedgerBook
from .budgets import Budget, BudgetTarget
from .category_models import CategoryAlias, CategoryVersion, ClassificationEvent
from .domain_storage_models import (
    BookMemberRecord,
    BudgetRecord,
    BudgetTargetRecord,
    CategoryAliasRecord,
    CategoryVersionRecord,
    ClassificationEventRecord,
    LedgerBookRecord,
)


class DomainStorageLoaders:
    def _load_books(self, session: Session) -> tuple[dict[str, LedgerBook], dict[tuple[str, str], BookMember]]:
        books = {
            row.book_id: LedgerBook(
                book_id=row.book_id,
                name=row.name,
                kind=row.kind,
                base_currency=row.base_currency,
                timezone=row.timezone,
                status=row.status,
                template_key=row.template_key,
                settings=dict(row.settings or {}),
                created_by=row.created_by,
                version=row.version,
            )
            for row in session.query(LedgerBookRecord).all()
        }
        members = {
            (row.book_id, row.user_id): BookMember(
                book_id=row.book_id,
                user_id=row.user_id,
                role=row.role,
                status=row.status,
                scopes=list(row.scopes),
                version=row.version,
            )
            for row in session.query(BookMemberRecord).all()
        }
        return books, members

    def _load_category_history(self, session: Session):
        aliases = {
            row.alias_id: CategoryAlias(
                alias_id=row.alias_id,
                book_id=row.book_id,
                category_id=row.category_id,
                alias=row.alias,
                normalized_alias=row.normalized_alias,
                locale=row.locale,
                source=row.source,
                confidence=row.confidence,
                status=row.status,
                version=row.version,
            )
            for row in session.query(CategoryAliasRecord).all()
        }
        versions = {
            row.category_version_id: CategoryVersion(
                category_version_id=row.category_version_id,
                category_id=row.category_id,
                book_id=row.book_id,
                name=row.name,
                parent_id=row.parent_id,
                path=row.path,
                icon=row.icon,
                color=row.color,
                valid_from=datetime.fromisoformat(row.valid_from),
                valid_to=datetime.fromisoformat(row.valid_to) if row.valid_to else None,
                change_reason=row.change_reason,
                version=row.version,
            )
            for row in session.query(CategoryVersionRecord).all()
        }
        events = {
            row.classification_event_id: ClassificationEvent(
                classification_event_id=row.classification_event_id,
                book_id=row.book_id,
                event_type=row.event_type,
                source_category_id=row.source_category_id,
                target_category_id=row.target_category_id,
                affected_line_count=row.affected_line_count,
                before=dict(row.before or {}),
                after=dict(row.after or {}),
                rollback=dict(row.rollback or {}),
                created_by=row.created_by,
                created_at=datetime.fromisoformat(row.created_at),
                version=row.version,
            )
            for row in session.query(ClassificationEventRecord).all()
        }
        return aliases, versions, events

    def _load_budgets(self, session: Session) -> tuple[dict[str, Budget], dict[str, BudgetTarget]]:
        budgets = {
            row.budget_id: Budget(
                budget_id=row.budget_id,
                book_id=row.book_id,
                name=row.name,
                period=row.period,
                starts_on=date.fromisoformat(row.starts_on) if row.starts_on else None,
                ends_on=date.fromisoformat(row.ends_on) if row.ends_on else None,
                currency=row.currency,
                total_amount=Decimal(row.total_amount),
                rollover_policy=row.rollover_policy,
                alert_thresholds=list(row.alert_thresholds or []),
                status=row.status,
                version=row.version,
            )
            for row in session.query(BudgetRecord).all()
        }
        targets = {
            row.budget_target_id: BudgetTarget(
                budget_target_id=row.budget_target_id,
                budget_id=row.budget_id,
                target_type=row.target_type,
                target_id=row.target_id,
                mode=row.mode,
                amount=Decimal(row.amount) if row.amount is not None else None,
                metadata=dict(row.metadata_json or {}),
                version=row.version,
            )
            for row in session.query(BudgetTargetRecord).all()
        }
        return budgets, targets
