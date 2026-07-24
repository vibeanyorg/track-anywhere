from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.entries.commit import (
    EntryCommitRuntime,
    commit_entry,
)
from track_anywhere.application.entries.contracts import (
    AccountRef,
    CategoryAllocationInput,
    CategoryRef,
    CommitEntryInput,
    ExpenseEntryInput,
    MoneyInput,
    PreparedEntryStatus,
    RefundEntryInput,
)
from track_anywhere.application.entries.prepare import (
    EntryPreparationRuntime,
    prepare_entry,
)
from track_anywhere.application.entries.errors import (
    EntryErrorCode,
    EntryGatewayError,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.application.privacy.protected_content import (
    TransactionNarrativeV2,
)
from track_anywhere.application.privacy.service import ProtectedContentService
from track_anywhere.infrastructure.crypto import (
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.entries import PreparedEntryIntentRecord
from track_anywhere.infrastructure.db.models.event_store import (
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.repositories.entries import (
    EverydayEntryDuplicateRepository,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


OCCURRED_AT = datetime(2026, 7, 24, 12, 30, tzinfo=UTC)
HMAC_KEY = bytes(range(32))


def _seed(pg_engine) -> tuple[JournalScenario, UUID]:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id = uuid4()
    category_version_id = uuid4()
    clearing_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, system_role, "
                "current_name, status) values ("
                ":book_id, :account_id, 'USD', 'expense', 'expense_clearing', "
                "'Expense clearing', 'active')"
            ),
            {"book_id": scenario.book_id, "account_id": clearing_id},
        )
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, parent_category_id, current_name, "
                "current_version_id, status) values ("
                ":book_id, :category_id, null, 'Dining', null, 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, parent_category_id, "
                "name, status, usage_kind, change_reason_code) values ("
                ":book_id, :category_id, :version_id, null, 'Dining', "
                "'active', 'expense', 'created')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
        connection.execute(
            text(
                "update categories set current_version_id = :version_id "
                "where book_id = :book_id and category_id = :category_id"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
    return scenario, category_id


def _runtime(pg_engine, scenario: JournalScenario):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(scenario.actor_subject_id)
    protected_service = ProtectedContentService(
        cipher=ProtectedContentCipher(
            ProtectedContentKeyring.from_mapping(
                active_key_ref="test-v1",
                keys={"test-v1": b"p" * 32},
            )
        ),
        repository=ProtectedContentRepository(),
    )
    return (
        EntryPreparationRuntime(
            actor=actor,
            uow_factory=uow_factory,
            protected_content_service=protected_service,
            duplicate_key_provider=DuplicateDetectionKeyProvider(HMAC_KEY),
        ),
        EntryCommitRuntime(
            actor=actor,
            uow_factory=uow_factory,
            ledger_committer=LedgerCommitter(),
            protected_content_service=protected_service,
        ),
    )


def _add_expense_category(
    pg_engine,
    scenario: JournalScenario,
    *,
    name: str,
) -> UUID:
    category_id = uuid4()
    version_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, parent_category_id, current_name, "
                "current_version_id, status) values ("
                ":book_id, :category_id, null, :name, null, 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "name": name,
            },
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, parent_category_id, "
                "name, status, usage_kind, change_reason_code) values ("
                ":book_id, :category_id, :version_id, null, :name, "
                "'active', 'expense', 'created')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
                "name": name,
            },
        )
        connection.execute(
            text(
                "update categories set current_version_id = :version_id "
                "where book_id = :book_id and category_id = :category_id"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
    return category_id


def _entry(scenario: JournalScenario, category_id: UUID) -> ExpenseEntryInput:
    return ExpenseEntryInput(
        amount=MoneyInput(
            value="12.34",
            denomination="asset_unit",
            asset_code="USD",
            source_text="12.34 USD",
        ),
        source_account=AccountRef(account_id=scenario.credit_account_id),
        category=CategoryRef(category_id=category_id),
        occurred_at=OCCURRED_AT,
    )


def test_prepare_commit_is_atomic_and_idempotent(pg_engine) -> None:
    scenario, category_id = _seed(pg_engine)
    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)
    prepared = prepare_entry(
        book_id=scenario.book_id,
        entry=_entry(scenario, category_id),
        runtime=prepare_runtime,
    )
    assert prepared.status is PreparedEntryStatus.READY
    assert prepared.commit_token is not None
    request_id = uuid4()
    command = CommitEntryInput(
        intent_id=prepared.intent_id,
        commit_token=prepared.commit_token,
        request_id=request_id,
    )
    committed = commit_entry(
        book_id=scenario.book_id,
        command=command,
        runtime=commit_runtime,
    )
    replayed = commit_entry(
        book_id=scenario.book_id,
        command=command,
        runtime=commit_runtime,
    )
    assert committed.transaction_id == replayed.transaction_id
    assert committed.replayed is False
    assert replayed.replayed is True

    with Session(pg_engine) as session:
        assert session.get(
            JournalTransactionRecord,
            (scenario.book_id, committed.transaction_id),
        ) is not None
        intent = session.get(
            PreparedEntryIntentRecord,
            (scenario.book_id, prepared.intent_id),
        )
        assert intent is not None
        assert intent.lifecycle_status == "consumed"
        assert session.scalar(
            select(func.count())
            .select_from(CommandReceiptRecord)
            .where(CommandReceiptRecord.command_id == request_id)
        ) == 1


def test_split_amount_sources_round_trip_by_exact_path_through_commit(
    pg_engine,
) -> None:
    scenario, first_category_id = _seed(pg_engine)
    second_category_id = _add_expense_category(
        pg_engine,
        scenario,
        name="Transport",
    )
    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)
    entry = ExpenseEntryInput(
        amount=MoneyInput(
            value="12.34",
            denomination="asset_unit",
            asset_code="USD",
            source_text="receipt total 12.34 USD",
        ),
        source_account=AccountRef(account_id=scenario.credit_account_id),
        category_allocations=(
            CategoryAllocationInput(
                category=CategoryRef(category_id=first_category_id),
                amount=MoneyInput(
                    value="10.00",
                    denomination="asset_unit",
                    asset_code="USD",
                    source_text="dining ten dollars",
                ),
            ),
            CategoryAllocationInput(
                category=CategoryRef(category_id=second_category_id),
                amount=MoneyInput(
                    value="234",
                    denomination="minor_unit",
                    asset_code="USD",
                    source_text="transport two hundred thirty-four cents",
                ),
            ),
        ),
        occurred_at=OCCURRED_AT,
    )
    prepared = prepare_entry(
        book_id=scenario.book_id,
        entry=entry,
        runtime=prepare_runtime,
    )
    assert prepared.status is PreparedEntryStatus.READY
    assert prepared.commit_token is not None
    committed = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=prepared.intent_id,
            commit_token=prepared.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )

    with Session(pg_engine) as session:
        transaction = session.get(
            JournalTransactionRecord,
            (scenario.book_id, committed.transaction_id),
        )
        assert transaction is not None
        assert transaction.description_ref is not None
        sidecar = ProtectedContentRepository().get(
            session,
            book_id=scenario.book_id,
            sidecar_id=transaction.description_ref,
        )
        assert sidecar is not None
        protected_service = commit_runtime.protected_content_service
        assert protected_service is not None
        plaintext = protected_service.decrypt_active(
            sidecar,
            expected_kind="transaction_narrative_v2",
        )
    narrative = TransactionNarrativeV2.model_validate_json(plaintext)
    assert tuple(
        (source.field_path, source.source_text)
        for source in narrative.amount_sources
    ) == (
        ("amount", "receipt total 12.34 USD"),
        ("category_allocations.0.amount", "dining ten dollars"),
        (
            "category_allocations.1.amount",
            "transport two hundred thirty-four cents",
        ),
    )


def test_full_refund_prepare_commit_has_no_invented_amount_source(
    pg_engine,
) -> None:
    scenario, category_id = _seed(pg_engine)
    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)
    original = prepare_entry(
        book_id=scenario.book_id,
        entry=_entry(scenario, category_id),
        runtime=prepare_runtime,
    )
    assert original.commit_token is not None
    original_receipt = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=original.intent_id,
            commit_token=original.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )

    refund = prepare_entry(
        book_id=scenario.book_id,
        entry=RefundEntryInput(
            original_transaction_id=original_receipt.transaction_id,
            amount=None,
            occurred_at=OCCURRED_AT + timedelta(hours=1),
        ),
        runtime=prepare_runtime,
    )
    assert refund.status is PreparedEntryStatus.READY
    assert refund.commit_token is not None
    refunded = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=refund.intent_id,
            commit_token=refund.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )

    with Session(pg_engine) as session:
        transaction = session.get(
            JournalTransactionRecord,
            (scenario.book_id, refunded.transaction_id),
        )
        assert transaction is not None
        assert transaction.description_ref is not None
        sidecar = ProtectedContentRepository().get(
            session,
            book_id=scenario.book_id,
            sidecar_id=transaction.description_ref,
        )
        assert sidecar is not None
        protected_service = commit_runtime.protected_content_service
        assert protected_service is not None
        plaintext = protected_service.decrypt_active(
            sidecar,
            expected_kind="transaction_narrative_v2",
        )
    narrative = TransactionNarrativeV2.model_validate_json(plaintext)
    assert narrative.amount_sources == ()


def test_finalizer_failure_rolls_back_claim_events_and_receipt(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, category_id = _seed(pg_engine)
    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)
    prepared = prepare_entry(
        book_id=scenario.book_id,
        entry=_entry(scenario, category_id),
        runtime=prepare_runtime,
    )
    assert prepared.commit_token is not None
    before_events: int
    with Session(pg_engine) as session:
        before_events = int(
            session.scalar(select(func.count()).select_from(LedgerEventRecord)) or 0
        )

    def fail(*_args, **_kwargs):
        raise RuntimeError("forced finalizer rollback")

    monkeypatch.setattr(
        EverydayEntryDuplicateRepository,
        "insert_source_fingerprint",
        fail,
    )
    request_id = uuid4()
    with pytest.raises(RuntimeError, match="forced finalizer rollback"):
        commit_entry(
            book_id=scenario.book_id,
            command=CommitEntryInput(
                intent_id=prepared.intent_id,
                commit_token=prepared.commit_token,
                request_id=request_id,
            ),
            runtime=commit_runtime,
        )

    with Session(pg_engine) as session:
        intent = session.get(
            PreparedEntryIntentRecord,
            (scenario.book_id, prepared.intent_id),
        )
        assert intent is not None
        assert intent.lifecycle_status == "created"
        assert session.scalar(
            select(func.count()).select_from(LedgerEventRecord)
        ) == before_events
        assert session.scalar(
            select(func.count())
            .select_from(CommandReceiptRecord)
            .where(CommandReceiptRecord.command_id == request_id)
        ) == 0


def test_expired_and_stale_intents_never_claim_or_append(pg_engine) -> None:
    scenario, category_id = _seed(pg_engine)
    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)
    now = datetime.now(UTC)
    expiring = prepare_entry(
        book_id=scenario.book_id,
        entry=_entry(scenario, category_id),
        runtime=replace(
            prepare_runtime,
            clock=lambda: now,
            intent_ttl=timedelta(minutes=1),
        ),
    )
    assert expiring.commit_token is not None
    with pytest.raises(EntryGatewayError) as expired:
        commit_entry(
            book_id=scenario.book_id,
            command=CommitEntryInput(
                intent_id=expiring.intent_id,
                commit_token=expiring.commit_token,
                request_id=uuid4(),
            ),
            runtime=replace(
                commit_runtime,
                clock=lambda: now + timedelta(minutes=2),
            ),
        )
    assert expired.value.code is EntryErrorCode.INTENT_EXPIRED

    stale = prepare_entry(
        book_id=scenario.book_id,
        entry=_entry(scenario, category_id),
        runtime=prepare_runtime,
    )
    assert stale.commit_token is not None
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update accounts set status = 'closed' "
                "where book_id = :book_id and account_id = :account_id"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.credit_account_id,
            },
        )
    with pytest.raises(EntryGatewayError) as changed:
        commit_entry(
            book_id=scenario.book_id,
            command=CommitEntryInput(
                intent_id=stale.intent_id,
                commit_token=stale.commit_token,
                request_id=uuid4(),
            ),
            runtime=commit_runtime,
        )
    assert changed.value.code is EntryErrorCode.INTENT_STALE

    with Session(pg_engine) as session:
        for prepared in (expiring, stale):
            intent = session.get(
                PreparedEntryIntentRecord,
                (scenario.book_id, prepared.intent_id),
            )
            assert intent is not None
            assert intent.lifecycle_status == "created"
