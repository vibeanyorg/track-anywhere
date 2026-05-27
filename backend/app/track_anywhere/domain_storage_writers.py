from __future__ import annotations

from sqlalchemy.orm import Session

from .domain_storage_models import (
    BookMemberRecord,
    BudgetRecord,
    BudgetTargetRecord,
    CategoryAliasRecord,
    CategoryVersionRecord,
    ClassificationEventRecord,
    LedgerBookRecord,
)
from .storage_json import to_jsonable


class DomainStorageWriters:
    def _save_books(self, session: Session, books, members) -> None:
        for book in books:
            session.merge(
                LedgerBookRecord(
                    book_id=book.book_id,
                    name=book.name,
                    kind=book.kind,
                    base_currency=book.base_currency,
                    timezone=book.timezone,
                    status=book.status,
                    template_key=book.template_key,
                    settings=to_jsonable(book.settings),
                    created_by=book.created_by,
                    version=book.version,
                )
            )
        for member in members:
            session.merge(
                BookMemberRecord(
                    book_id=member.book_id,
                    user_id=member.user_id,
                    role=member.role,
                    status=member.status,
                    scopes=list(member.scopes),
                    version=member.version,
                )
            )

    def _save_category_history(
        self,
        session: Session,
        *,
        aliases,
        versions,
        events,
    ) -> None:
        for alias in aliases:
            self._upsert_record(
                session,
                CategoryAliasRecord,
                {
                    "alias_id": alias.alias_id,
                    "book_id": alias.book_id,
                    "category_id": alias.category_id,
                    "alias": alias.alias,
                    "normalized_alias": alias.normalized_alias,
                    "locale": alias.locale,
                    "source": alias.source,
                    "confidence": alias.confidence,
                    "status": alias.status,
                    "version": alias.version,
                },
                ["alias_id"],
            )
        for version in versions:
            self._upsert_record(
                session,
                CategoryVersionRecord,
                {
                    "category_version_id": version.category_version_id,
                    "category_id": version.category_id,
                    "book_id": version.book_id,
                    "name": version.name,
                    "parent_id": version.parent_id,
                    "path": version.path,
                    "icon": version.icon,
                    "color": version.color,
                    "valid_from": version.valid_from.isoformat(),
                    "valid_to": version.valid_to.isoformat() if version.valid_to else None,
                    "change_reason": version.change_reason,
                    "version": version.version,
                },
                ["category_version_id"],
            )
        for event in events:
            self._upsert_record(
                session,
                ClassificationEventRecord,
                {
                    "classification_event_id": event.classification_event_id,
                    "book_id": event.book_id,
                    "event_type": event.event_type,
                    "source_category_id": event.source_category_id,
                    "target_category_id": event.target_category_id,
                    "affected_line_count": event.affected_line_count,
                    "before": to_jsonable(event.before),
                    "after": to_jsonable(event.after),
                    "rollback": to_jsonable(event.rollback),
                    "created_by": event.created_by,
                    "created_at": event.created_at.isoformat(),
                    "version": event.version,
                },
                ["classification_event_id"],
            )

    def _save_budgets(self, session: Session, budgets, targets) -> None:
        for budget in budgets:
            session.merge(
                BudgetRecord(
                    budget_id=budget.budget_id,
                    book_id=budget.book_id,
                    name=budget.name,
                    period=budget.period,
                    starts_on=budget.starts_on.isoformat() if budget.starts_on else None,
                    ends_on=budget.ends_on.isoformat() if budget.ends_on else None,
                    currency=budget.currency,
                    total_amount=str(budget.total_amount),
                    rollover_policy=budget.rollover_policy,
                    alert_thresholds=list(budget.alert_thresholds),
                    status=budget.status,
                    version=budget.version,
                )
            )
        for target in targets:
            session.merge(
                BudgetTargetRecord(
                    budget_target_id=target.budget_target_id,
                    budget_id=target.budget_id,
                    target_type=target.target_type,
                    target_id=target.target_id,
                    mode=target.mode,
                    amount=str(target.amount) if target.amount is not None else None,
                    metadata_json=to_jsonable(target.metadata),
                    version=target.version,
                )
            )
