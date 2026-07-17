from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.postgres.test_frozen_import_catalog import seed_target_baseline
from backend.tests.v2.postgres.test_import_frozen_financial_history import (
    _cipher,
    _fixed_synthetic_plan,
)
import track_anywhere.application.imports.import_frozen_financial_history as frozen_import
from track_anywhere.application.imports.contracts import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    plan_sha256,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def test_all_five_card_balances_are_verified_and_alias_closes_at_zero(
    pg_engine,
) -> None:
    plan = _fixed_synthetic_plan()
    seed_target_baseline(pg_engine, plan, seed_receipt=False)
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)

    frozen_import.import_frozen_financial_history(
        plan,
        expected_plan_hash=plan_sha256(plan),
        raw_key="frozen-import-receipt",
        actor=CommandActor(subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID),
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        protected_content_cipher=_cipher(),
    )

    cards = tuple(
        account for account in plan.accounts if account.account_subtype == "credit_card"
    )
    alias = next(account for account in cards if account.close_after_import)
    assert len(cards) == 5
    with Session(pg_engine) as session:
        for card in cards:
            reference = int(
                session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        JournalPostingRecord.side == "debit",
                                        JournalPostingRecord.units,
                                    ),
                                    else_=-JournalPostingRecord.units,
                                )
                            ),
                            0,
                        )
                    ).where(
                        JournalPostingRecord.book_id == plan.target_book_id,
                        JournalPostingRecord.account_id == card.account_id,
                        JournalPostingRecord.asset_code == card.asset_code,
                    )
                )
                or 0
            )
            projection = session.get(
                AccountBalanceRecord,
                (plan.target_book_id, card.account_id, card.asset_code),
            )
            projected = 0 if projection is None else int(projection.balance_units)
            assert reference == projected == -card.expected_natural_units

        alias_row = session.get(
            AccountRecord,
            (plan.target_book_id, alias.account_id),
        )
        alias_posting_count = session.scalar(
            select(func.count())
            .select_from(JournalPostingRecord)
            .where(
                JournalPostingRecord.book_id == plan.target_book_id,
                JournalPostingRecord.account_id == alias.account_id,
            )
        )
        assert alias_posting_count and alias_posting_count > 0
        assert alias.expected_natural_units == 0
        assert alias_row is not None and alias_row.status == "closed"
