from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ...infrastructure.db.models.catalog import AccountRecord
from ...infrastructure.db.models.payment_instruments import (
    PaymentInstrumentBindingRecord,
    PaymentInstrumentMutationRecord,
    PaymentInstrumentRecord,
)
from ..catalogs._authorization import require_catalog_write
from ..idempotency import CommandActor
from ..ledger_committer import LedgerCommitter
from ..unit_of_work import UnitOfWork
from .contracts import (
    BindingRole,
    CreatePaymentInstrument,
    PaymentInstrumentBindingView,
    PaymentInstrumentMutation,
    PaymentInstrumentRef,
    PaymentInstrumentView,
    SettlementPolicy,
)


class PaymentInstrumentError(ValueError):
    pass


def create_payment_instrument(
    command: CreatePaymentInstrument,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> PaymentInstrumentView:
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        LedgerCommitter().execute_under_book_lock(uow.session, command.book_id)
        receipt = uow.session.get(
            PaymentInstrumentMutationRecord, (command.book_id, command.instrument_id)
        )
        if receipt is not None:
            if (
                receipt.command != command.model_dump(mode="json")
                or receipt.actor_subject_id != actor.subject_id
            ):
                raise PaymentInstrumentError(
                    "payment instrument identity conflicts with existing configuration"
                )
            return PaymentInstrumentView.model_validate(receipt.after)
        account = uow.session.get(
            AccountRecord,
            (command.book_id, command.settlement_account_id),
        )
        if (
            account is None
            or account.status != "active"
            or account.asset_code != command.asset_code
            or account.system_role is not None
        ):
            raise PaymentInstrumentError(
                "settlement account is unavailable or does not match asset_code"
            )
        role = _validate_account_policy(account, command.settlement_policy)
        existing = uow.session.get(
            PaymentInstrumentRecord,
            (command.book_id, command.instrument_id),
        )
        if existing is not None:
            existing_binding = uow.session.get(
                PaymentInstrumentBindingRecord,
                (command.book_id, command.binding_id),
            )
            if (
                existing_binding is None
                or existing_binding.instrument_id != command.instrument_id
                or existing.instrument_kind != "card"
                or existing.form_factor != command.form_factor.value
                or existing.network != command.network.value
                or existing.provider_code != command.provider_code
                or existing.settlement_policy != command.settlement_policy.value
                or existing.current_name != command.current_name
                or existing.last4 != command.last4
                or existing.status != "active"
                or existing_binding.account_id != command.settlement_account_id
                or existing_binding.asset_code != command.asset_code
                or existing_binding.binding_role != role.value
                or existing_binding.priority != 100
                or existing_binding.status != "active"
                or existing_binding.effective_from != command.effective_from
                or existing_binding.effective_to is not None
            ):
                raise PaymentInstrumentError(
                    "payment instrument identity conflicts with existing configuration"
                )
            return get_payment_instrument(
                uow.session,
                book_id=command.book_id,
                instrument_id=command.instrument_id,
            )
        if command.last4 is not None:
            duplicate = uow.session.scalar(
                select(PaymentInstrumentRecord).where(
                    PaymentInstrumentRecord.book_id == command.book_id,
                    PaymentInstrumentRecord.provider_code == command.provider_code,
                    PaymentInstrumentRecord.last4 == command.last4,
                    PaymentInstrumentRecord.status == "active",
                )
            )
            if duplicate is not None:
                raise PaymentInstrumentError(
                    f"card already exists ({duplicate.instrument_id}); update its metadata or add a currency binding"
                )
        uow.session.add(
            PaymentInstrumentRecord(
                book_id=command.book_id,
                instrument_id=command.instrument_id,
                instrument_kind="card",
                form_factor=command.form_factor.value,
                network=command.network.value,
                provider_code=command.provider_code,
                settlement_policy=command.settlement_policy.value,
                current_name=command.current_name,
                last4=command.last4,
                status="active",
                effective_from=command.effective_from,
            )
        )
        uow.session.flush()
        binding = PaymentInstrumentBindingRecord(
            book_id=command.book_id,
            binding_id=command.binding_id,
            instrument_id=command.instrument_id,
            account_id=command.settlement_account_id,
            asset_code=command.asset_code,
            binding_role=role.value,
            priority=100,
            status="active",
            effective_from=command.effective_from,
            effective_to=None,
        )
        uow.session.add(binding)
        uow.session.flush()
        view = get_payment_instrument(
            uow.session, book_id=command.book_id, instrument_id=command.instrument_id
        )
        uow.session.add(
            PaymentInstrumentMutationRecord(
                book_id=command.book_id,
                request_id=command.instrument_id,
                instrument_id=command.instrument_id,
                actor_subject_id=actor.subject_id,
                command=command.model_dump(mode="json"),
                before={},
                after=view.model_dump(mode="json"),
            )
        )
    with uow_factory() as uow:
        receipt = uow.session.get(
            PaymentInstrumentMutationRecord, (command.book_id, command.instrument_id)
        )
        if receipt is None:
            raise PaymentInstrumentError(
                "verification pending; retry creation with the same identifiers"
            )
        return PaymentInstrumentView.model_validate(receipt.after)


def get_payment_instrument(
    session: Session,
    *,
    book_id: UUID,
    instrument_id: UUID,
) -> PaymentInstrumentView:
    instrument = session.get(PaymentInstrumentRecord, (book_id, instrument_id))
    if instrument is None:
        raise LookupError("payment instrument was not found")
    rows = session.execute(
        select(PaymentInstrumentBindingRecord, AccountRecord)
        .join(
            AccountRecord,
            (AccountRecord.book_id == PaymentInstrumentBindingRecord.book_id)
            & (AccountRecord.account_id == PaymentInstrumentBindingRecord.account_id),
        )
        .where(
            PaymentInstrumentBindingRecord.book_id == book_id,
            PaymentInstrumentBindingRecord.instrument_id == instrument_id,
        )
        .order_by(
            PaymentInstrumentBindingRecord.effective_from,
            PaymentInstrumentBindingRecord.binding_id,
        )
    ).all()
    bindings = tuple(
        PaymentInstrumentBindingView(
            binding_id=b.binding_id,
            asset_code=b.asset_code,
            settlement_policy=instrument.settlement_policy,
            settlement_account_id=b.account_id,
            settlement_account_name=a.current_name,
            binding_role=b.binding_role,
            status=b.status,
            effective_from=b.effective_from,
            effective_to=b.effective_to,
        )
        for b, a in rows
    )
    # Legacy flat fields remain usable only when there is exactly one active binding.
    active = [b for b in bindings if b.status == "active"]
    single = active[0] if len(active) == 1 else None
    return PaymentInstrumentView(
        book_id=book_id,
        instrument_id=instrument_id,
        instrument_kind=instrument.instrument_kind,
        current_name=instrument.current_name,
        form_factor=instrument.form_factor,
        network=instrument.network,
        provider_code=instrument.provider_code,
        last4=instrument.last4,
        status=instrument.status,
        version=instrument.version,
        effective_from=instrument.effective_from,
        effective_to=instrument.effective_to,
        bindings=bindings,
        **{
            key: getattr(single, key) if single else None
            for key in (
                "binding_id",
                "asset_code",
                "settlement_policy",
                "settlement_account_id",
                "binding_role",
            )
        },
    )


def list_payment_instruments(
    session: Session,
    *,
    book_id: UUID,
    status: str | None = None,
    asset_code: str | None = None,
    name: str | None = None,
) -> tuple[PaymentInstrumentView, ...]:
    statement = select(PaymentInstrumentRecord).where(
        PaymentInstrumentRecord.book_id == book_id
    )
    if status == "inactive":
        statement = statement.where(PaymentInstrumentRecord.status != "active")
    elif status not in (None, "all"):
        if status not in {"active", "closed", "frozen"}:
            raise ValueError("invalid instrument status")
        statement = statement.where(PaymentInstrumentRecord.status == status)
    if name is not None:
        if not name.strip():
            raise ValueError("name filter must be nonblank")
        statement = statement.where(
            PaymentInstrumentRecord.current_name.ilike(f"%{name.strip()}%")
        )
    if asset_code is not None:
        statement = statement.where(
            select(PaymentInstrumentBindingRecord.binding_id)
            .where(
                PaymentInstrumentBindingRecord.book_id == book_id,
                PaymentInstrumentBindingRecord.instrument_id
                == PaymentInstrumentRecord.instrument_id,
                PaymentInstrumentBindingRecord.asset_code == asset_code,
                PaymentInstrumentBindingRecord.status == "active",
            )
            .exists()
        )
    return tuple(
        get_payment_instrument(session, book_id=book_id, instrument_id=i.instrument_id)
        for i in session.scalars(
            statement.order_by(
                PaymentInstrumentRecord.current_name,
                PaymentInstrumentRecord.instrument_id,
            )
        )
    )


def mutate_payment_instrument(
    command: PaymentInstrumentMutation,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> tuple[PaymentInstrumentView, bool]:
    payload = command.model_dump(mode="json", exclude_unset=True)
    with uow_factory() as uow:
        session = uow.session
        require_catalog_write(session, actor, command.book_id)
        LedgerCommitter().execute_under_book_lock(session, command.book_id)
        previous = session.get(
            PaymentInstrumentMutationRecord, (command.book_id, command.request_id)
        )
        if previous is not None:
            if (
                previous.command != payload
                or previous.actor_subject_id != actor.subject_id
            ):
                raise PaymentInstrumentError(
                    "request_id conflicts with an earlier payment instrument mutation"
                )
            return PaymentInstrumentView.model_validate(previous.after), True
        instrument = session.get(
            PaymentInstrumentRecord, (command.book_id, command.instrument_id)
        )
        if instrument is None:
            raise PaymentInstrumentError("payment instrument was not found")
        before = get_payment_instrument(
            session, book_id=command.book_id, instrument_id=command.instrument_id
        )
        now = datetime.now(timezone.utc)
        if command.operation == "update":
            for key in command.model_fields_set - {
                "book_id",
                "request_id",
                "instrument_id",
                "operation",
            }:
                value = getattr(command, key)
                setattr(
                    instrument, key, value.strip() if key == "current_name" else value
                )
        elif command.operation == "close":
            if instrument.status != "active":
                raise PaymentInstrumentError("instrument is already inactive")
            instrument.status = "closed"
            instrument.effective_to = now
        elif command.operation == "reopen":
            if instrument.status == "active":
                raise PaymentInstrumentError("instrument is already active")
            instrument.status = "active"
            instrument.effective_from = now
            instrument.effective_to = None
        elif command.operation == "add_binding":
            if instrument.status != "active":
                raise PaymentInstrumentError("cannot bind an inactive instrument")
            if command.settlement_policy.value != instrument.settlement_policy:
                raise PaymentInstrumentError(
                    "binding policy must match the card settlement policy"
                )
            account = session.get(
                AccountRecord, (command.book_id, command.settlement_account_id)
            )
            if (
                account is None
                or account.status != "active"
                or account.system_role is not None
                or account.asset_code != command.asset_code
            ):
                raise PaymentInstrumentError(
                    "settlement account is unavailable or does not match asset_code"
                )
            role = _validate_account_policy(account, command.settlement_policy)
            conflict = session.scalar(
                select(PaymentInstrumentBindingRecord).where(
                    PaymentInstrumentBindingRecord.book_id == command.book_id,
                    PaymentInstrumentBindingRecord.instrument_id
                    == command.instrument_id,
                    PaymentInstrumentBindingRecord.asset_code == command.asset_code,
                    (
                        PaymentInstrumentBindingRecord.effective_to.is_(None)
                        | (
                            PaymentInstrumentBindingRecord.effective_to
                            > command.effective_from
                        )
                    ),
                )
            )
            if conflict is not None:
                raise PaymentInstrumentError(
                    "overlapping settlement binding for asset_code"
                )
            session.add(
                PaymentInstrumentBindingRecord(
                    book_id=command.book_id,
                    instrument_id=command.instrument_id,
                    binding_id=uuid5(command.request_id, str(command.instrument_id)),
                    account_id=command.settlement_account_id,
                    asset_code=command.asset_code,
                    binding_role=role.value,
                    status="active",
                    priority=100,
                    effective_from=command.effective_from,
                )
            )
        else:
            binding = session.get(
                PaymentInstrumentBindingRecord, (command.book_id, command.binding_id)
            )
            if binding is None or binding.instrument_id != command.instrument_id:
                raise PaymentInstrumentError("binding was not found on this instrument")
            if (
                binding.status != "active"
                or not binding.effective_from < command.effective_to <= now
            ):
                raise PaymentInstrumentError(
                    "close requires an active binding and effective_from < effective_to <= now"
                )
            binding.status = "closed"
            binding.effective_to = command.effective_to
        if (
            instrument.status == "active"
            and instrument.last4 is not None
            and command.operation in {"update", "reopen"}
        ):
            with session.no_autoflush:
                duplicate = session.scalar(
                    select(PaymentInstrumentRecord).where(
                        PaymentInstrumentRecord.book_id == command.book_id,
                        PaymentInstrumentRecord.instrument_id != command.instrument_id,
                        PaymentInstrumentRecord.provider_code
                        == instrument.provider_code,
                        PaymentInstrumentRecord.last4 == instrument.last4,
                        PaymentInstrumentRecord.status == "active",
                    )
                )
            # Existing duplicates may still correct their metadata; don't create new collisions.
            if duplicate is not None and (
                command.operation == "reopen"
                or (before.provider_code, before.last4)
                != (instrument.provider_code, instrument.last4)
            ):
                raise PaymentInstrumentError(
                    "card identity matches another active instrument"
                )
        instrument.version += 1
        instrument.updated_at = now
        session.flush()
        after = get_payment_instrument(
            session, book_id=command.book_id, instrument_id=command.instrument_id
        )
        session.add(
            PaymentInstrumentMutationRecord(
                book_id=command.book_id,
                request_id=command.request_id,
                instrument_id=command.instrument_id,
                actor_subject_id=actor.subject_id,
                command=payload,
                before=before.model_dump(mode="json"),
                after=after.model_dump(mode="json"),
            )
        )
    # Read a durable receipt in a fresh transaction after the commit.
    with uow_factory() as uow:
        receipt = uow.session.get(
            PaymentInstrumentMutationRecord, (command.book_id, command.request_id)
        )
        if receipt is None or receipt.command != payload:
            raise PaymentInstrumentError(
                "verification pending; retry the exact same request_id"
            )
        return PaymentInstrumentView.model_validate(receipt.after), False


def resolve_payment_instrument(
    session: Session,
    *,
    book_id: UUID,
    reference: PaymentInstrumentRef,
    asset_code: str,
    occurred_at: datetime,
) -> tuple[PaymentInstrumentRecord, PaymentInstrumentBindingRecord]:
    statement = _active_binding_query(book_id).where(
        PaymentInstrumentRecord.status == "active",
        PaymentInstrumentRecord.effective_from <= occurred_at,
        PaymentInstrumentBindingRecord.asset_code == asset_code,
        PaymentInstrumentBindingRecord.status == "active",
        PaymentInstrumentBindingRecord.effective_from <= occurred_at,
        (
            PaymentInstrumentBindingRecord.effective_to.is_(None)
            | (PaymentInstrumentBindingRecord.effective_to > occurred_at)
        ),
    )
    if reference.instrument_id is not None:
        statement = statement.where(
            PaymentInstrumentRecord.instrument_id == reference.instrument_id
        )
    else:
        statement = statement.where(
            (
                PaymentInstrumentRecord.current_name.ilike(f"%{reference.query}%")
                | (PaymentInstrumentRecord.last4 == reference.query)
            )
        )
        if reference.last4 is not None:
            statement = statement.where(
                PaymentInstrumentRecord.last4 == reference.last4
            )
        if reference.provider_code is not None:
            statement = statement.where(
                PaymentInstrumentRecord.provider_code == reference.provider_code
            )
    rows = session.execute(
        statement.order_by(PaymentInstrumentBindingRecord.priority)
    ).all()
    if not rows:
        raise PaymentInstrumentError(
            f"No active {asset_code} settlement binding exists for this payment instrument at the requested time"
        )
    if len(rows) != 1:
        raise PaymentInstrumentError(
            "payment instrument reference is ambiguous; use instrument_id"
        )
    instrument, binding = rows[0]
    account = session.get(AccountRecord, (book_id, binding.account_id))
    if (
        account is None
        or account.status != "active"
        or account.system_role is not None
        or account.asset_code != binding.asset_code
        or _validate_account_policy(
            account,
            SettlementPolicy(instrument.settlement_policy),
        ).value
        != binding.binding_role
    ):
        raise PaymentInstrumentError("payment instrument binding is stale")
    return instrument, binding


def _active_binding_query(
    book_id: UUID,
) -> Select[tuple[PaymentInstrumentRecord, PaymentInstrumentBindingRecord]]:
    return (
        select(PaymentInstrumentRecord, PaymentInstrumentBindingRecord)
        .join(
            PaymentInstrumentBindingRecord,
            (PaymentInstrumentBindingRecord.book_id == PaymentInstrumentRecord.book_id)
            & (
                PaymentInstrumentBindingRecord.instrument_id
                == PaymentInstrumentRecord.instrument_id
            ),
        )
        .where(
            PaymentInstrumentRecord.book_id == book_id,
            PaymentInstrumentBindingRecord.status == "active",
        )
    )


def _validate_account_policy(
    account: AccountRecord,
    policy: SettlementPolicy,
) -> BindingRole:
    if policy in {SettlementPolicy.IMMEDIATE, SettlementPolicy.PREPAID}:
        if account.account_type != "asset":
            raise PaymentInstrumentError(
                "immediate or prepaid cards require an asset settlement account"
            )
        return BindingRole.FUNDING_ASSET
    if account.account_type != "liability" or account.account_subtype != "credit_card":
        raise PaymentInstrumentError(
            "statement cards require a credit-card liability settlement account"
        )
    return BindingRole.CARD_LIABILITY


__all__ = [
    "PaymentInstrumentError",
    "create_payment_instrument",
    "get_payment_instrument",
    "list_payment_instruments",
    "resolve_payment_instrument",
]
