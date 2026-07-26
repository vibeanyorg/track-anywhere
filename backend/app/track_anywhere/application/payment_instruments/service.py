from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ...infrastructure.db.models.catalog import AccountRecord
from ...infrastructure.db.models.payment_instruments import (
    PaymentInstrumentBindingRecord,
    PaymentInstrumentRecord,
)
from ..catalogs._authorization import require_catalog_write
from ..idempotency import CommandActor
from ..unit_of_work import UnitOfWork
from .contracts import (
    BindingRole,
    CreatePaymentInstrument,
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
            return _view_from_records(existing, existing_binding)
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
        return _view(instrument=command, binding=binding, role=role)


def get_payment_instrument(
    session: Session,
    *,
    book_id: UUID,
    instrument_id: UUID,
) -> PaymentInstrumentView:
    row = session.execute(
        _active_binding_query(book_id).where(
            PaymentInstrumentRecord.instrument_id == instrument_id
        )
    ).one_or_none()
    if row is None:
        raise LookupError("payment instrument was not found")
    return _view_from_records(*row)


def list_payment_instruments(
    session: Session,
    *,
    book_id: UUID,
    status: str | None = None,
    asset_code: str | None = None,
    name: str | None = None,
) -> tuple[PaymentInstrumentView, ...]:
    statement = _active_binding_query(book_id)
    if status is not None:
        statement = statement.where(PaymentInstrumentRecord.status == status)
    if asset_code is not None:
        statement = statement.where(
            PaymentInstrumentBindingRecord.asset_code == asset_code
        )
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("name filter must be nonblank")
        statement = statement.where(
            PaymentInstrumentRecord.current_name.ilike(f"%{normalized}%")
        )
    rows = session.execute(
        statement.order_by(
            PaymentInstrumentRecord.current_name,
            PaymentInstrumentRecord.instrument_id,
        )
    ).all()
    return tuple(_view_from_records(*row) for row in rows)


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
            PaymentInstrumentRecord.current_name.ilike(f"%{reference.query}%")
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
            "no active payment instrument binding matches the expense asset and time"
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
            (
                PaymentInstrumentBindingRecord.book_id
                == PaymentInstrumentRecord.book_id
            )
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
    if (
        account.account_type != "liability"
        or account.account_subtype != "credit_card"
    ):
        raise PaymentInstrumentError(
            "statement cards require a credit-card liability settlement account"
        )
    return BindingRole.CARD_LIABILITY


def _view(
    *,
    instrument: CreatePaymentInstrument,
    binding: PaymentInstrumentBindingRecord,
    role: BindingRole,
) -> PaymentInstrumentView:
    return PaymentInstrumentView(
        book_id=instrument.book_id,
        instrument_id=instrument.instrument_id,
        binding_id=instrument.binding_id,
        instrument_kind="card",
        current_name=instrument.current_name,
        form_factor=instrument.form_factor,
        network=instrument.network,
        provider_code=instrument.provider_code,
        settlement_policy=instrument.settlement_policy,
        settlement_account_id=instrument.settlement_account_id,
        asset_code=instrument.asset_code,
        binding_role=role,
        last4=instrument.last4,
        status="active",
        effective_from=binding.effective_from,
        effective_to=binding.effective_to,
    )


def _view_from_records(
    instrument: PaymentInstrumentRecord,
    binding: PaymentInstrumentBindingRecord,
) -> PaymentInstrumentView:
    return PaymentInstrumentView(
        book_id=instrument.book_id,
        instrument_id=instrument.instrument_id,
        binding_id=binding.binding_id,
        instrument_kind=instrument.instrument_kind,
        current_name=instrument.current_name,
        form_factor=instrument.form_factor,
        network=instrument.network,
        provider_code=instrument.provider_code,
        settlement_policy=instrument.settlement_policy,
        settlement_account_id=binding.account_id,
        asset_code=binding.asset_code,
        binding_role=binding.binding_role,
        last4=instrument.last4,
        status=instrument.status,
        effective_from=binding.effective_from,
        effective_to=binding.effective_to,
    )


__all__ = [
    "PaymentInstrumentError",
    "create_payment_instrument",
    "get_payment_instrument",
    "list_payment_instruments",
    "resolve_payment_instrument",
]
