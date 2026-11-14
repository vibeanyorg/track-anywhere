from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.postgres.test_everyday_entry_orchestration import (
    _seed,
    _runtime,
    OCCURRED_AT,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.payment_instruments.contracts import (
    CreatePaymentInstrument,
    PaymentInstrumentMutation,
    PaymentInstrumentRef,
    CardNetwork,
)
from track_anywhere.application.payment_instruments.service import (
    create_payment_instrument,
    mutate_payment_instrument,
    get_payment_instrument,
    list_payment_instruments,
    resolve_payment_instrument,
    PaymentInstrumentError,
)
from track_anywhere.application.entries.prepare import prepare_entry
from track_anywhere.application.entries.commit import commit_entry
from track_anywhere.application.entries.errors import EntryGatewayError
from track_anywhere.application.entries.contracts import (
    AccountRef,
    CommitEntryInput,
    CreditCardPaymentEntryInput,
    ExpenseEntryInput,
    MoneyInput,
    CategoryRef,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.db.models.payment_instruments import (
    PaymentInstrumentMutationRecord,
)


def test_multicurrency_lifecycle_audit_and_idempotency(pg_engine):
    scenario, category_id = _seed(pg_engine)
    usd, cny, instrument_id = uuid4(), uuid4(), uuid4()
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                'update book_members set scopes=\'["book:write","ledger:write"]\'::jsonb where book_id=:b'
            ),
            {"b": scenario.book_id},
        )
        conn.execute(
            text(
                "insert into assets(asset_code,kind,ledger_scale,input_scale,display_scale,current_name,status) values ('CNY','fiat',2,2,2,'Yuan','active')"
            )
        )
        for code, account in [("USD", usd), ("CNY", cny)]:
            conn.execute(
                text(
                    "insert into accounts(book_id,account_id,asset_code,account_type,account_subtype,current_name,status) values (:b,:a,:c,'liability','credit_card',:n,'active')"
                ),
                dict(b=scenario.book_id, a=account, c=code, n="1945 - " + code),
            )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(scenario.actor_subject_id)
    card = create_payment_instrument(
        CreatePaymentInstrument(
            book_id=scenario.book_id,
            instrument_id=instrument_id,
            binding_id=uuid4(),
            current_name="1945 Mastercard",
            form_factor="physical",
            network="mastercard",
            provider_code="bocom",
            settlement_policy="statement",
            settlement_account_id=usd,
            asset_code="USD",
            last4="1945",
            effective_from=OCCURRED_AT - timedelta(days=1),
        ),
        actor=actor,
        uow_factory=uow,
    )

    def change(operation, **kwargs):
        command = PaymentInstrumentMutation(
            book_id=scenario.book_id,
            request_id=uuid4(),
            instrument_id=instrument_id,
            operation=operation,
            **kwargs,
        )
        view, replayed = mutate_payment_instrument(
            command, actor=actor, uow_factory=uow
        )
        assert not replayed
        retry, is_retry = mutate_payment_instrument(
            command, actor=actor, uow_factory=uow
        )
        assert is_retry and retry == view
        return command, view

    add, multi = change(
        "add_binding",
        settlement_account_id=cny,
        asset_code="CNY",
        settlement_policy="statement",
        effective_from=OCCURRED_AT - timedelta(days=1),
    )
    assert multi.binding_id is None and len(multi.bindings) == 2
    with pytest.raises(PaymentInstrumentError, match="overlapping"):
        change(
            "add_binding",
            settlement_account_id=cny,
            asset_code="CNY",
            settlement_policy="statement",
            effective_from=OCCURRED_AT,
        )
    with pytest.raises(PaymentInstrumentError, match="asset_code"):
        change(
            "add_binding",
            settlement_account_id=usd,
            asset_code="JPY",
            settlement_policy="statement",
            effective_from=OCCURRED_AT,
        )
    with Session(pg_engine) as session:
        assert (
            len(
                list_payment_instruments(
                    session, book_id=scenario.book_id, status="active"
                )
            )
            == 1
        )
        for code, account in [("CNY", cny), ("USD", usd)]:
            _, binding = resolve_payment_instrument(
                session,
                book_id=scenario.book_id,
                reference=PaymentInstrumentRef(query="1945"),
                asset_code=code,
                occurred_at=OCCURRED_AT,
            )
            assert binding.account_id == account
        with pytest.raises(PaymentInstrumentError, match="No active JPY"):
            resolve_payment_instrument(
                session,
                book_id=scenario.book_id,
                reference=PaymentInstrumentRef(query="1945"),
                asset_code="JPY",
                occurred_at=OCCURRED_AT,
            )
    preparation, committing = _runtime(pg_engine, scenario)
    entry = ExpenseEntryInput(
        amount=MoneyInput(value="20", asset_code="USD", source_text="20 USD"),
        occurred_at=OCCURRED_AT,
        payment_instrument=PaymentInstrumentRef(instrument_id=instrument_id),
        category=CategoryRef(category_id=category_id),
    )
    prepared = prepare_entry(book_id=scenario.book_id, entry=entry, runtime=preparation)
    assert prepared.resolved.payment_instrument_id == instrument_id
    committed = commit_entry(
        book_id=scenario.book_id,
        command=CommitEntryInput(
            intent_id=prepared.intent_id,
            commit_token=prepared.commit_token,
            request_id=uuid4(),
        ),
        runtime=committing,
    )
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "insert into accounts(book_id,account_id,asset_code,account_type,system_role,current_name,status) values (:b,:a,'CNY','expense','expense_clearing','CNY expense clearing','active')"
            ),
            dict(b=scenario.book_id, a=uuid4()),
        )
    cny_entry = entry.model_copy(
        update={
            "amount": MoneyInput(value="48", asset_code="CNY", source_text="48 CNY")
        }
    )
    cny_prepared = prepare_entry(
        book_id=scenario.book_id, entry=cny_entry, runtime=preparation
    )
    assert cny_prepared.resolved.source_account_id == cny
    payment = prepare_entry(
        book_id=scenario.book_id,
        entry=CreditCardPaymentEntryInput(
            amount=MoneyInput(value="20", asset_code="USD", source_text="20 USD"),
            funding_account=AccountRef(account_id=scenario.credit_account_id),
            payment_instrument=PaymentInstrumentRef(instrument_id=instrument_id),
            occurred_at=OCCURRED_AT,
        ),
        runtime=preparation,
    )
    assert payment.resolved.payment_instrument_id == instrument_id
    # Database guard covers bypasses of the service and history is insert-only.
    with pytest.raises(IntegrityError, match="overlapping"):
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "insert into payment_instrument_bindings(book_id,binding_id,instrument_id,account_id,asset_code,binding_role,priority,status,effective_from) values (:b,:id,:i,:a,'USD','card_liability',100,'active',:t)"
                ),
                dict(
                    b=scenario.book_id,
                    id=uuid4(),
                    i=instrument_id,
                    a=usd,
                    t=OCCURRED_AT,
                ),
            )
    with pytest.raises(ProgrammingError):
        with pg_engine.begin() as conn:
            conn.execute(
                text("delete from payment_instrument_mutations where book_id=:b"),
                dict(b=scenario.book_id),
            )
    with Session(pg_engine) as session:
        before = session.execute(
            text("select count(*) from journal_postings")
        ).scalar_one()
    update, corrected = change(
        "update", network="visa", current_name="交通银行信用卡 1945"
    )
    assert corrected.network.value == "visa" and corrected.bindings == multi.bindings
    renamed = prepare_entry(
        book_id=scenario.book_id,
        entry=entry.model_copy(
            update={"occurred_at": OCCURRED_AT + timedelta(minutes=2)}
        ),
        runtime=preparation,
    )
    assert renamed.resolved.payment_instrument_name == "交通银行信用卡 1945"
    assert renamed.resolved.payment_instrument_version == corrected.version
    with pytest.raises(PaymentInstrumentError, match="request_id conflicts"):
        mutate_payment_instrument(
            update.model_copy(update={"network": CardNetwork.AMEX}),
            actor=actor,
            uow_factory=uow,
        )
    _, closed = change("close")
    assert closed.effective_to is not None
    with pytest.raises(EntryGatewayError):
        commit_entry(
            book_id=scenario.book_id,
            command=CommitEntryInput(
                intent_id=cny_prepared.intent_id,
                commit_token=cny_prepared.commit_token,
                request_id=uuid4(),
            ),
            runtime=committing,
        )
    with Session(pg_engine) as session:
        assert not list_payment_instruments(
            session, book_id=scenario.book_id, status="active"
        )
        assert (
            len(
                list_payment_instruments(
                    session, book_id=scenario.book_id, status="inactive"
                )
            )
            == 1
        )
        with pytest.raises(PaymentInstrumentError, match="No active USD"):
            resolve_payment_instrument(
                session,
                book_id=scenario.book_id,
                reference=PaymentInstrumentRef(instrument_id=instrument_id),
                asset_code="USD",
                occurred_at=OCCURRED_AT,
            )
        assert (
            session.execute(text("select count(*) from journal_postings")).scalar_one()
            == before
        )
        receipt = session.get(
            PaymentInstrumentMutationRecord, (scenario.book_id, update.request_id)
        )
        assert (
            receipt.before["network"] == "mastercard"
            and receipt.after["network"] == "visa"
        )
    # Exact retry after later mutations returns the original durable result.
    retry, replayed = mutate_payment_instrument(update, actor=actor, uow_factory=uow)
    assert replayed and retry == corrected
    change("reopen")
    _, ended = change(
        "close_binding", binding_id=card.binding_id, effective_to=datetime.now(UTC)
    )
    assert any(b.status == "closed" for b in ended.bindings)
    with Session(pg_engine) as session:
        assert (
            get_payment_instrument(
                session, book_id=scenario.book_id, instrument_id=instrument_id
            ).bindings
            == ended.bindings
        )


def test_backdate_binding_restores_historical_resolution(pg_engine, monkeypatch):
    scenario, category_id = _seed(pg_engine)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(scenario.actor_subject_id)
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                'update book_members set scopes=\'["book:write","ledger:write"]\'::jsonb where book_id=:b'
            ),
            {"b": scenario.book_id},
        )
    card = create_payment_instrument(
        CreatePaymentInstrument(
            book_id=scenario.book_id,
            instrument_id=uuid4(),
            binding_id=uuid4(),
            current_name="2863",
            form_factor="physical",
            network="visa",
            provider_code="bank",
            settlement_policy="immediate",
            settlement_account_id=scenario.credit_account_id,
            asset_code="USD",
            last4="2863",
            effective_from=OCCURRED_AT + timedelta(days=4),
        ),
        actor=actor,
        uow_factory=uow,
    )
    reference = PaymentInstrumentRef(instrument_id=card.instrument_id)
    with Session(pg_engine) as session:
        with pytest.raises(PaymentInstrumentError, match="No active"):
            resolve_payment_instrument(
                session,
                book_id=scenario.book_id,
                reference=reference,
                asset_code="USD",
                occurred_at=OCCURRED_AT,
            )
    command = PaymentInstrumentMutation(
        book_id=scenario.book_id,
        instrument_id=card.instrument_id,
        request_id=uuid4(),
        operation="backdate_binding",
        binding_id=card.binding_id,
        effective_from=OCCURRED_AT,
    )
    after, replayed = mutate_payment_instrument(command, actor=actor, uow_factory=uow)
    assert not replayed
    assert after.effective_from == after.bindings[0].effective_from == OCCURRED_AT
    assert after.version == card.version + 1
    assert mutate_payment_instrument(command, actor=actor, uow_factory=uow) == (
        after,
        True,
    )
    with Session(pg_engine) as session:
        _, binding = resolve_payment_instrument(
            session,
            book_id=scenario.book_id,
            reference=reference,
            asset_code="USD",
            occurred_at=OCCURRED_AT,
        )
        assert binding.account_id == scenario.credit_account_id
        receipt = session.get(
            PaymentInstrumentMutationRecord, (scenario.book_id, command.request_id)
        )
        assert (
            receipt.before["bindings"][0]["effective_from"]
            != receipt.after["bindings"][0]["effective_from"]
        )
        assert (
            session.execute(text("select count(*) from journal_postings")).scalar_one()
            == 0
        )
    preparation, _ = _runtime(pg_engine, scenario)
    prepared = prepare_entry(
        book_id=scenario.book_id,
        runtime=preparation,
        entry=ExpenseEntryInput(
            amount=MoneyInput(
                value="111.98", asset_code="USD", source_text="111.98 USD"
            ),
            occurred_at=OCCURRED_AT,
            payment_instrument=reference,
            category=CategoryRef(category_id=category_id),
        ),
    )
    assert prepared.resolved.source_account_id == scenario.credit_account_id
    for date in (OCCURRED_AT, OCCURRED_AT + timedelta(days=1)):
        with pytest.raises(PaymentInstrumentError, match="earlier"):
            mutate_payment_instrument(
                command.model_copy(
                    update={"request_id": uuid4(), "effective_from": date}
                ),
                actor=actor,
                uow_factory=uow,
            )
    with pytest.raises(PaymentInstrumentError, match="request_id conflicts"):
        mutate_payment_instrument(
            command.model_copy(
                update={"effective_from": OCCURRED_AT - timedelta(days=1)}
            ),
            actor=actor,
            uow_factory=uow,
        )
    # A closed historical interval must block extension, including partial overlap.
    mutate_payment_instrument(
        PaymentInstrumentMutation(
            book_id=scenario.book_id,
            instrument_id=card.instrument_id,
            request_id=uuid4(),
            operation="close_binding",
            binding_id=card.binding_id,
            effective_to=OCCURRED_AT + timedelta(days=1),
        ),
        actor=actor,
        uow_factory=uow,
    )
    later, _ = mutate_payment_instrument(
        PaymentInstrumentMutation(
            book_id=scenario.book_id,
            instrument_id=card.instrument_id,
            request_id=uuid4(),
            operation="add_binding",
            settlement_account_id=scenario.credit_account_id,
            asset_code="USD",
            settlement_policy="immediate",
            effective_from=OCCURRED_AT + timedelta(days=2),
        ),
        actor=actor,
        uow_factory=uow,
    )
    active = next(b for b in later.bindings if b.status == "active")
    with pytest.raises(PaymentInstrumentError, match="overlapping"):
        mutate_payment_instrument(
            command.model_copy(
                update={"request_id": uuid4(), "binding_id": active.binding_id}
            ),
            actor=actor,
            uow_factory=uow,
        )
    with pytest.raises(PaymentInstrumentError, match="active"):
        mutate_payment_instrument(
            command.model_copy(
                update={
                    "request_id": uuid4(),
                    "effective_from": OCCURRED_AT - timedelta(days=1),
                }
            ),
            actor=actor,
            uow_factory=uow,
        )
    # Half-open adjacent intervals are valid.
    adjacent, _ = mutate_payment_instrument(
        command.model_copy(
            update={
                "request_id": uuid4(),
                "binding_id": active.binding_id,
                "effective_from": OCCURRED_AT + timedelta(days=1),
            }
        ),
        actor=actor,
        uow_factory=uow,
    )
    assert next(
        b for b in adjacent.bindings if b.status == "active"
    ).effective_from == OCCURRED_AT + timedelta(days=1)

    # Exercise the HTTP command boundary with the same real persistence layer.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from track_anywhere.api.v2 import payment_instruments as routes
    from track_anywhere.api.v2.schemas import RequestActor

    request_actor = RequestActor(actor, scenario.book_id, frozenset({"book:write"}))
    monkeypatch.setattr(
        routes, "create_actor_dependency", lambda _: lambda: request_actor
    )
    app = FastAPI()
    app.include_router(
        routes.create_payment_instrument_router(
            get_session=lambda: None, uow_factory=uow
        ),
        prefix="/api/v2",
    )
    url = f"/api/v2/books/{scenario.book_id}/payment-instruments/{card.instrument_id}/mutations"
    payload = command.model_dump(mode="json", exclude_unset=True)
    with TestClient(app) as client:
        response = client.post(url, json=payload)
        assert response.status_code == 200
        assert response.json()["replayed"] is True
        assert response.json()["verification_status"] == "verified"
        invalid = dict(payload)
        invalid.pop("effective_from")
        assert client.post(url, json=invalid).status_code == 422
        assert (
            client.post(url, json={**payload, "book_id": str(uuid4())}).status_code
            == 422
        )
        request_actor = RequestActor(
            actor, scenario.book_id, frozenset({"ledger:write"})
        )
        assert client.post(url, json=payload).status_code == 403


def test_backdate_permission_migration_is_reversible(migrated_postgres_database):
    from sqlalchemy import create_engine
    from backend.tests.v2.postgres.test_v2_schema_guard import _run_alembic

    database = migrated_postgres_database
    engine = create_engine(database.runtime_url)
    try:
        for revision, action, expected in (
            ("v2_0019_binding_backdate", "upgrade", True),
            ("v2_0018_account_rename", "downgrade", False),
            ("v2_0019_binding_backdate", "upgrade", True),
        ):
            result = _run_alembic(
                action,
                revision,
                database_url=database.migrator_url,
                runtime_role=database.runtime_role,
            )
            assert result.returncode == 0, result.stderr
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "select has_column_privilege(current_user, 'payment_instrument_bindings', 'effective_from', 'UPDATE')"
                        )
                    )
                    is expected
                )
                assert (
                    connection.scalar(
                        text(
                            "select has_column_privilege(current_user, 'payment_instrument_bindings', 'account_id', 'UPDATE')"
                        )
                    )
                    is False
                )
    finally:
        engine.dispose()
