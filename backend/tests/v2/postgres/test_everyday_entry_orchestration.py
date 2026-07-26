from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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
    CreditCardPaymentEntryInput,
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
from track_anywhere.application.payment_instruments import (
    CreatePaymentInstrument,
    PaymentInstrumentError,
    PaymentInstrumentRef,
    create_payment_instrument,
)
from track_anywhere.infrastructure.crypto import (
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.entries import PreparedEntryIntentRecord
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.event_store import (
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.models.payment_instruments import (
    PaymentInstrumentTransactionRecord,
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


def test_prepare_derives_account_last4_from_current_names(pg_engine) -> None:
    scenario, category_id = _seed(pg_engine)
    savings_id = uuid4()
    other_savings_id = uuid4()
    card_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, account_subtype, "
                "current_name, status) values "
                "(:book_id, :savings_id, 'USD', 'asset', 'debit_card', "
                "'工商银行 6184', 'active'), "
                "(:book_id, :other_savings_id, 'USD', 'asset', 'debit_card', "
                "'工商银行 (9988)', 'active'), "
                "(:book_id, :card_id, 'USD', 'liability', 'credit_card', "
                "'工商银行信用卡 1242', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "savings_id": savings_id,
                "other_savings_id": other_savings_id,
                "card_id": card_id,
            },
        )
    prepare_runtime, _ = _runtime(pg_engine, scenario)

    def expense(
        source_account: AccountRef,
        *,
        minute: int,
    ) -> ExpenseEntryInput:
        return ExpenseEntryInput(
            amount=MoneyInput(
                value=str(20 + minute),
                denomination="asset_unit",
                asset_code="USD",
                source_text=f"{20 + minute} USD",
            ),
            source_account=source_account,
            category=CategoryRef(category_id=category_id),
            occurred_at=OCCURRED_AT + timedelta(minutes=minute),
        )

    resolved_base = prepare_entry(
        book_id=scenario.book_id,
        entry=expense(
            AccountRef(
                query="工商银行",
                last4="6184",
                subtype="debit_card",
            ),
            minute=1,
        ),
        runtime=prepare_runtime,
    )
    assert resolved_base.status is PreparedEntryStatus.READY
    assert resolved_base.resolved.source_account_id == savings_id

    resolved_full_name = prepare_entry(
        book_id=scenario.book_id,
        entry=expense(
            AccountRef(query="工商银行 6184", last4="6184"),
            minute=2,
        ),
        runtime=prepare_runtime,
    )
    assert resolved_full_name.status is PreparedEntryStatus.READY
    assert resolved_full_name.resolved.source_account_id == savings_id

    resolved_card = prepare_entry(
        book_id=scenario.book_id,
        entry=expense(
            AccountRef(
                query="工商银行信用卡",
                last4="1242",
                subtype="credit_card",
            ),
            minute=3,
        ),
        runtime=prepare_runtime,
    )
    assert resolved_card.status is PreparedEntryStatus.READY
    assert resolved_card.resolved.source_account_id == card_id

    ambiguous = prepare_entry(
        book_id=scenario.book_id,
        entry=expense(AccountRef(query="工商银行"), minute=4),
        runtime=prepare_runtime,
    )
    assert ambiguous.status is PreparedEntryStatus.NEEDS_CLARIFICATION
    assert ambiguous.commit_token is None
    assert len(ambiguous.clarifications) == 1
    assert ambiguous.clarifications[0].field == "source_account"
    assert {
        choice.resolved_id for choice in ambiguous.clarifications[0].choices
    } == {savings_id, other_savings_id}

    with pytest.raises(EntryGatewayError) as raised:
        prepare_entry(
            book_id=scenario.book_id,
            entry=expense(
                AccountRef(query="工商银行", last4="0000"),
                minute=5,
            ),
            runtime=prepare_runtime,
        )
    assert raised.value.code is EntryErrorCode.ACCOUNT_NOT_FOUND

    direct = prepare_entry(
        book_id=scenario.book_id,
        entry=expense(AccountRef(account_id=card_id), minute=6),
        runtime=prepare_runtime,
    )
    assert direct.status is PreparedEntryStatus.READY
    assert direct.resolved.source_account_id == card_id


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


def test_virtual_cards_resolve_generic_prepaid_and_statement_accounting(
    pg_engine,
) -> None:
    scenario, category_id = _seed(pg_engine)
    statement_account_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update book_members set scopes = "
                "'[\"book:write\",\"ledger:write\"]'::jsonb "
                "where book_id = :book_id and user_id = :user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, account_subtype, "
                "current_name, status) values ("
                ":book_id, :account_id, 'USD', 'liability', 'credit_card', "
                "'Generic statement card', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": statement_account_id,
            },
        )
        account_count = connection.execute(
            text("select count(*) from accounts where book_id = :book_id"),
            {"book_id": scenario.book_id},
        ).scalar_one()

    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(scenario.actor_subject_id)
    prepaid_id, prepaid_binding_id = uuid4(), uuid4()
    statement_id, statement_binding_id = uuid4(), uuid4()
    invalid_instrument_id = uuid4()
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    "insert into payment_instruments ("
                    "book_id, instrument_id, instrument_kind, form_factor, "
                    "network, provider_code, settlement_policy, current_name, "
                    "status) values ("
                    ":book_id, :instrument_id, 'card', 'virtual', 'other', "
                    "'generic', 'statement', 'Invalid direct binding', 'active')"
                ),
                {
                    "book_id": scenario.book_id,
                    "instrument_id": invalid_instrument_id,
                },
            )
            connection.execute(
                text(
                    "insert into payment_instrument_bindings ("
                    "book_id, binding_id, instrument_id, account_id, asset_code, "
                    "binding_role, priority, status, effective_from) values ("
                    ":book_id, :binding_id, :instrument_id, :account_id, 'USD', "
                    "'funding_asset', 100, 'active', :effective_from)"
                ),
                {
                    "book_id": scenario.book_id,
                    "binding_id": uuid4(),
                    "instrument_id": invalid_instrument_id,
                    "account_id": scenario.credit_account_id,
                    "effective_from": OCCURRED_AT - timedelta(days=1),
                },
            )
    with pytest.raises(PaymentInstrumentError, match="require an asset"):
        create_payment_instrument(
            CreatePaymentInstrument(
                book_id=scenario.book_id,
                instrument_id=uuid4(),
                binding_id=uuid4(),
                current_name="Invalid prepaid liability card",
                form_factor="physical",
                network="other",
                provider_code="generic",
                settlement_policy="prepaid",
                settlement_account_id=statement_account_id,
                asset_code="USD",
                effective_from=OCCURRED_AT - timedelta(days=1),
            ),
            actor=actor,
            uow_factory=uow_factory,
        )
    prepaid = create_payment_instrument(
        CreatePaymentInstrument(
            book_id=scenario.book_id,
            instrument_id=prepaid_id,
            binding_id=prepaid_binding_id,
            current_name="SafePal USD virtual Mastercard",
            form_factor="virtual",
            network="mastercard",
            provider_code="safepal",
            settlement_policy="prepaid",
            settlement_account_id=scenario.credit_account_id,
            asset_code="USD",
            last4="0024",
            effective_from=OCCURRED_AT - timedelta(days=1),
        ),
        actor=actor,
        uow_factory=uow_factory,
    )
    statement = create_payment_instrument(
        CreatePaymentInstrument(
            book_id=scenario.book_id,
            instrument_id=statement_id,
            binding_id=statement_binding_id,
            current_name="Provider-neutral virtual Visa",
            form_factor="virtual",
            network="visa",
            provider_code="other_provider",
            settlement_policy="statement",
            settlement_account_id=statement_account_id,
            asset_code="USD",
            last4="4242",
            effective_from=OCCURRED_AT - timedelta(days=1),
        ),
        actor=actor,
        uow_factory=uow_factory,
    )
    assert prepaid.binding_role.value == "funding_asset"
    assert statement.binding_role.value == "card_liability"

    prepare_runtime, commit_runtime = _runtime(pg_engine, scenario)

    def instrument_expense(
        instrument_id: UUID,
        *,
        value: str,
        occurred_at: datetime,
    ) -> ExpenseEntryInput:
        return ExpenseEntryInput(
            amount=MoneyInput(
                value=value,
                denomination="asset_unit",
                asset_code="USD",
                source_text=f"{value} USD",
            ),
            payment_instrument=PaymentInstrumentRef(
                instrument_id=instrument_id
            ),
            category=CategoryRef(category_id=category_id),
            occurred_at=occurred_at,
        )

    prepared_prepaid = prepare_entry(
        book_id=scenario.book_id,
        entry=instrument_expense(
            prepaid_id,
            value="8",
            occurred_at=OCCURRED_AT,
        ),
        runtime=prepare_runtime,
    )
    assert prepared_prepaid.status is PreparedEntryStatus.READY
    assert prepared_prepaid.resolved.source_account_id == scenario.credit_account_id
    assert prepared_prepaid.resolved.payment_instrument_id == prepaid_id
    assert (
        prepared_prepaid.resolved.payment_instrument_binding_id
        == prepaid_binding_id
    )
    assert prepared_prepaid.commit_token is not None
    prepaid_commit = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=prepared_prepaid.intent_id,
            commit_token=prepared_prepaid.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )

    prepared_statement = prepare_entry(
        book_id=scenario.book_id,
        entry=instrument_expense(
            statement_id,
            value="9",
            occurred_at=OCCURRED_AT + timedelta(minutes=1),
        ),
        runtime=prepare_runtime,
    )
    assert prepared_statement.status is PreparedEntryStatus.READY
    assert prepared_statement.resolved.source_account_id == statement_account_id
    assert prepared_statement.commit_token is not None
    statement_commit = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=prepared_statement.intent_id,
            commit_token=prepared_statement.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )
    prepared_payment = prepare_entry(
        book_id=scenario.book_id,
        entry=CreditCardPaymentEntryInput(
            amount=MoneyInput(
                value="9",
                denomination="asset_unit",
                asset_code="USD",
                source_text="Monthly statement payment: 9 USD",
            ),
            funding_account=AccountRef(
                account_id=scenario.credit_account_id
            ),
            payment_instrument=PaymentInstrumentRef(
                instrument_id=statement_id
            ),
            occurred_at=OCCURRED_AT + timedelta(days=1),
        ),
        runtime=prepare_runtime,
    )
    assert prepared_payment.status is PreparedEntryStatus.READY
    assert prepared_payment.resolved.card_account_id == statement_account_id
    assert prepared_payment.resolved.payment_instrument_id == statement_id
    assert prepared_payment.commit_token is not None
    payment_commit = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=prepared_payment.intent_id,
            commit_token=prepared_payment.commit_token,
            request_id=uuid4(),
        ),
        runtime=commit_runtime,
    )

    with Session(pg_engine) as session:
        assert session.get(
            JournalTransactionRecord,
            (scenario.book_id, prepaid_commit.transaction_id),
        ).transaction_kind == "standard"
        assert session.get(
            JournalTransactionRecord,
            (scenario.book_id, statement_commit.transaction_id),
        ).transaction_kind == "credit_card_charge"
        assert session.get(
            JournalTransactionRecord,
            (scenario.book_id, payment_commit.transaction_id),
        ).transaction_kind == "credit_card_payment"
        prepaid_link = session.get(
            PaymentInstrumentTransactionRecord,
            (scenario.book_id, prepaid_commit.transaction_id),
        )
        statement_link = session.get(
            PaymentInstrumentTransactionRecord,
            (scenario.book_id, statement_commit.transaction_id),
        )
        payment_link = session.get(
            PaymentInstrumentTransactionRecord,
            (scenario.book_id, payment_commit.transaction_id),
        )
        assert (prepaid_link.instrument_id, prepaid_link.binding_id) == (
            prepaid_id,
            prepaid_binding_id,
        )
        assert (statement_link.instrument_id, statement_link.binding_id) == (
            statement_id,
            statement_binding_id,
        )
        assert (payment_link.instrument_id, payment_link.binding_id) == (
            statement_id,
            statement_binding_id,
        )
        assert session.scalar(
            select(func.count())
            .select_from(AccountRecord)
            .where(AccountRecord.book_id == scenario.book_id)
        ) == account_count

    stale = prepare_entry(
        book_id=scenario.book_id,
        entry=instrument_expense(
            prepaid_id,
            value="11",
            occurred_at=OCCURRED_AT + timedelta(hours=1),
        ),
        runtime=prepare_runtime,
    )
    assert stale.commit_token is not None
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update payment_instrument_bindings set status = 'closed', "
                "effective_to = :effective_to "
                "where book_id = :book_id and binding_id = :binding_id"
            ),
            {
                "book_id": scenario.book_id,
                "binding_id": prepaid_binding_id,
                "effective_to": OCCURRED_AT + timedelta(minutes=30),
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
