from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import hmac
import json
import os
import re
import sys
from typing import BinaryIO, Final
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from ..application.catalogs.close_account import (
    AccountAlreadyClosed,
    CloseAccount,
    close_account,
)
from ..application.idempotency import CommandActor
from ..application.repairs import (
    RepairMisclassifiedExpense,
    canonical_expense_clearing_account_id,
    ensure_internal_accounts,
    ensure_repair_categories,
    execute_misclassified_expense_repair,
    repair_category,
    repair_command_id,
    replacement_transaction_id,
    reversal_transaction_id,
)
from ..domain.journal import AccountSystemRole
from ..infrastructure.db.engine import create_v2_engine
from ..infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    CategoryRecord,
)
from ..infrastructure.db.models.async_projection import ProjectionCheckpointRecord
from ..infrastructure.db.models.event_store import BookEventHeadRecord
from ..infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
)
from ..infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from ..infrastructure.projections.checkpoints import (
    PROJECTION_NAME,
    PROJECTOR_VERSION,
)
from ..infrastructure.projections.dirty_periods import month_start, utc_date
from ..infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from ..infrastructure.projections.worker import AsyncProjectionWorker
from ..serialization.canonical_json import JSONValue, canonical_json_bytes


MAX_STDIN_BYTES: Final = 1024 * 1024
DATABASE_URL_ENV: Final = "TRACK_ANYWHERE_DATABASE_URL"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


class RepairRunnerFailure(Exception):
    __slots__ = ("code", "exit_code")

    def __init__(self, code: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PlannedRepair:
    original_transaction_id: UUID
    wrong_expense_account_id: UUID
    category_id: UUID


@dataclass(frozen=True, slots=True)
class RepairPlan:
    book_id: UUID
    actor_subject_id: str
    repairs: tuple[PlannedRepair, ...]
    create_category_paths: tuple[tuple[str, ...], ...]
    close_account_ids: tuple[UUID, ...]
    provision_all_active_internal_accounts: bool

    def canonical_value(self) -> dict[str, JSONValue]:
        return {
            "actor_subject_id": self.actor_subject_id,
            "book_id": str(self.book_id),
            "close_account_ids": [
                str(account_id) for account_id in self.close_account_ids
            ],
            "create_category_paths": [
                list(path) for path in self.create_category_paths
            ],
            "provision_all_active_internal_accounts": (
                self.provision_all_active_internal_accounts
            ),
            "repairs": [
                {
                    "category_id": str(item.category_id),
                    "original_transaction_id": str(
                        item.original_transaction_id
                    ),
                    "wrong_expense_account_id": str(
                        item.wrong_expense_account_id
                    ),
                }
                for item in self.repairs
            ],
            "version": 1,
        }


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    original_transaction_id: UUID
    wrong_expense_account_id: UUID
    wrong_account_name: str
    category_id: UUID
    source_account_id: UUID
    transaction_kind: str
    asset_code: str
    units: int
    source_balance_units: int
    effective_at: datetime
    description_ref: UUID | None
    external_references: tuple[tuple[str, str, str], ...]
    applied: bool

    def safe_value(self) -> dict[str, JSONValue]:
        return {
            "asset_code": self.asset_code,
            "category_id": str(self.category_id),
            "original_transaction_id": str(self.original_transaction_id),
            "source_account_id": str(self.source_account_id),
            "transaction_kind": self.transaction_kind,
            "units": self.units,
            "state": "applied" if self.applied else "pending",
            "wrong_account_name": self.wrong_account_name,
            "wrong_expense_account_id": str(self.wrong_expense_account_id),
        }


def _parse_arguments(argv: Sequence[str]) -> tuple[str, bool]:
    arguments = tuple(argv)
    apply = "--apply" in arguments
    expected_length = 6 if apply else 3
    if (
        len(arguments) != expected_length
        or arguments.count("--stdin") != 1
        or arguments.count("--plan-sha256") != 1
        or arguments.count("--apply") != (1 if apply else 0)
        or arguments.count("--confirm-plan-sha256") != (1 if apply else 0)
    ):
        raise RepairRunnerFailure("invalid_arguments")
    values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        name = arguments[index]
        if name in {"--stdin", "--apply"}:
            index += 1
            continue
        if name not in {"--plan-sha256", "--confirm-plan-sha256"}:
            raise RepairRunnerFailure("invalid_arguments")
        if index + 1 >= len(arguments):
            raise RepairRunnerFailure("invalid_arguments")
        values[name] = arguments[index + 1]
        index += 2
    expected_hash = values.get("--plan-sha256", "")
    if _HEX_SHA256.fullmatch(expected_hash) is None:
        raise RepairRunnerFailure("invalid_arguments")
    if apply and not hmac.compare_digest(
        expected_hash,
        values.get("--confirm-plan-sha256", ""),
    ):
        raise RepairRunnerFailure("plan_confirmation_mismatch")
    return expected_hash, apply


def _read_stdin(stdin: BinaryIO) -> bytes:
    raw = stdin.read(MAX_STDIN_BYTES + 1)
    if type(raw) is not bytes:
        raise RepairRunnerFailure("stdin_read_failed")
    if len(raw) > MAX_STDIN_BYTES:
        raise RepairRunnerFailure("stdin_too_large")
    return raw


def _parse_plan(raw: bytes, expected_hash: str) -> RepairPlan:
    try:
        payload = json.loads(raw)
        if type(payload) is not dict or set(payload) != {
            "actor_subject_id",
            "book_id",
            "close_account_ids",
            "create_category_paths",
            "repairs",
            "provision_all_active_internal_accounts",
            "version",
        }:
            raise ValueError
        if payload["version"] != 1:
            raise ValueError
        actor = payload["actor_subject_id"]
        if type(actor) is not str or not actor or len(actor) > 128:
            raise ValueError
        repairs_raw = payload["repairs"]
        paths_raw = payload["create_category_paths"]
        close_raw = payload["close_account_ids"]
        provision_all = payload["provision_all_active_internal_accounts"]
        if (
            type(repairs_raw) is not list
            or not repairs_raw
            or type(paths_raw) is not list
            or type(close_raw) is not list
            or type(provision_all) is not bool
        ):
            raise ValueError
        repairs = tuple(
            _parse_repair(item)
            for item in repairs_raw
        )
        paths = tuple(_parse_path(path) for path in paths_raw)
        close_ids = tuple(UUID(value) for value in close_raw)
        plan = RepairPlan(
            book_id=UUID(payload["book_id"]),
            actor_subject_id=actor,
            repairs=repairs,
            create_category_paths=paths,
            close_account_ids=close_ids,
            provision_all_active_internal_accounts=provision_all,
        )
    except (KeyError, TypeError, UnicodeError, ValueError):
        raise RepairRunnerFailure("invalid_plan") from None
    if len({item.original_transaction_id for item in plan.repairs}) != len(
        plan.repairs
    ):
        raise RepairRunnerFailure("invalid_plan")
    wrong_ids = {item.wrong_expense_account_id for item in plan.repairs}
    if (
        len(set(plan.close_account_ids)) != len(plan.close_account_ids)
        or set(plan.close_account_ids) != wrong_ids
    ):
        raise RepairRunnerFailure("invalid_plan")
    actual_hash = sha256(canonical_json_bytes(plan.canonical_value())).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise RepairRunnerFailure("plan_contract_mismatch")
    return plan


def _parse_repair(value: object) -> PlannedRepair:
    if type(value) is not dict or set(value) != {
        "category_id",
        "original_transaction_id",
        "wrong_expense_account_id",
    }:
        raise ValueError
    return PlannedRepair(
        original_transaction_id=UUID(value["original_transaction_id"]),
        wrong_expense_account_id=UUID(value["wrong_expense_account_id"]),
        category_id=UUID(value["category_id"]),
    )


def _parse_path(value: object) -> tuple[str, ...]:
    if (
        type(value) is not list
        or not value
        or any(type(part) is not str or not part.strip() for part in value)
    ):
        raise ValueError
    return tuple(part.strip() for part in value)


def _inspect(
    session: Session,
    plan: RepairPlan,
) -> tuple[CandidateSnapshot, ...]:
    declared_categories = {
        repair_category(plan.book_id, path).category_id
        for path in plan.create_category_paths
    }
    snapshots: list[CandidateSnapshot] = []
    for item in plan.repairs:
        transaction = session.get(
            JournalTransactionRecord,
            (plan.book_id, item.original_transaction_id),
        )
        wrong_account = session.get(
            AccountRecord,
            (plan.book_id, item.wrong_expense_account_id),
        )
        category = session.get(
            CategoryRecord,
            (plan.book_id, item.category_id),
        )
        reversal = session.scalar(
            select(TransactionReversalRecord.reversal_transaction_id).where(
                TransactionReversalRecord.book_id == plan.book_id,
                TransactionReversalRecord.original_transaction_id
                == item.original_transaction_id,
            )
        )
        lines = session.scalar(
            select(func.count())
            .select_from(ReportingLineRecord)
            .where(
                ReportingLineRecord.book_id == plan.book_id,
                ReportingLineRecord.transaction_id
                == item.original_transaction_id,
            )
        )
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord)
                .where(
                    JournalPostingRecord.book_id == plan.book_id,
                    JournalPostingRecord.transaction_id
                    == item.original_transaction_id,
                )
                .order_by(JournalPostingRecord.posting_position)
            )
        )
        expected_reversal = reversal_transaction_id(
            plan.book_id,
            item.original_transaction_id,
        )
        if (
            transaction is None
            or transaction.transaction_kind
            not in {"standard", "credit_card_charge"}
            or wrong_account is None
            or wrong_account.account_type != "expense"
            or wrong_account.system_role is not None
            or (
                category is None
                and item.category_id not in declared_categories
            )
            or (
                reversal is not None
                and reversal != expected_reversal
            )
            or lines != 0
            or len(postings) != 2
        ):
            raise RepairRunnerFailure("candidate_state_mismatch", exit_code=1)
        wrong = tuple(
            posting
            for posting in postings
            if posting.account_id == item.wrong_expense_account_id
        )
        source = tuple(
            posting
            for posting in postings
            if posting.account_id != item.wrong_expense_account_id
        )
        if (
            len(wrong) != 1
            or len(source) != 1
            or wrong[0].side != "debit"
            or source[0].side != "credit"
            or wrong[0].asset_code != source[0].asset_code
            or int(wrong[0].units) != int(source[0].units)
        ):
            raise RepairRunnerFailure("candidate_state_mismatch", exit_code=1)
        source_balance = session.scalar(
            select(AccountBalanceRecord.balance_units).where(
                AccountBalanceRecord.book_id == plan.book_id,
                AccountBalanceRecord.account_id == source[0].account_id,
                AccountBalanceRecord.asset_code == source[0].asset_code,
            )
        )
        snapshots.append(
            CandidateSnapshot(
                original_transaction_id=item.original_transaction_id,
                wrong_expense_account_id=item.wrong_expense_account_id,
                wrong_account_name=wrong_account.current_name,
                category_id=item.category_id,
                source_account_id=source[0].account_id,
                transaction_kind=transaction.transaction_kind,
                asset_code=source[0].asset_code,
                units=int(source[0].units),
                source_balance_units=(
                    0 if source_balance is None else int(source_balance)
                ),
                effective_at=transaction.effective_at,
                description_ref=transaction.description_ref,
                external_references=_external_references(
                    session,
                    book_id=plan.book_id,
                    transaction_id=item.original_transaction_id,
                ),
                applied=reversal == expected_reversal,
            )
        )
    return tuple(snapshots)


def _apply(
    plan: RepairPlan,
    snapshots: tuple[CandidateSnapshot, ...],
    *,
    session_factory: sessionmaker[Session],
    active_asset_codes: tuple[str, ...],
) -> dict[str, JSONValue]:
    actor = CommandActor(plan.actor_subject_id)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    categories = tuple(
        repair_category(plan.book_id, path)
        for path in plan.create_category_paths
    )
    ensure_repair_categories(
        book_id=plan.book_id,
        categories=categories,
        actor=actor,
        uow_factory=uow_factory,
    )
    provision_assets = (
        active_asset_codes
        if plan.provision_all_active_internal_accounts
        else tuple(sorted({snapshot.asset_code for snapshot in snapshots}))
    )
    provisioned_accounts = ensure_internal_accounts(
        book_id=plan.book_id,
        asset_codes=provision_assets,
        roles=(
            AccountSystemRole.EXPENSE_CLEARING,
            AccountSystemRole.INCOME_CLEARING,
            AccountSystemRole.BALANCE_ADJUSTMENT,
        ),
        actor=actor,
        uow_factory=uow_factory,
    )

    replayed = 0
    for item in plan.repairs:
        outcome = execute_misclassified_expense_repair(
            RepairMisclassifiedExpense(
                book_id=plan.book_id,
                command_id=repair_command_id(
                    plan.book_id,
                    item.original_transaction_id,
                ),
                original_transaction_id=item.original_transaction_id,
                reversal_transaction_id=reversal_transaction_id(
                    plan.book_id,
                    item.original_transaction_id,
                ),
                replacement_transaction_id=replacement_transaction_id(
                    plan.book_id,
                    item.original_transaction_id,
                ),
                wrong_expense_account_id=item.wrong_expense_account_id,
                category_id=item.category_id,
            ),
            actor=actor,
            uow_factory=uow_factory,
        )
        replayed += int(outcome.replayed)

    closed = 0
    for account_id in plan.close_account_ids:
        try:
            close_account(
                CloseAccount(book_id=plan.book_id, account_id=account_id),
                actor=actor,
                uow_factory=uow_factory,
            )
            closed += 1
        except AccountAlreadyClosed:
            pass
    target_book_position = _book_position(session_factory, plan.book_id)
    projection = _catch_up_monthly_projection(
        session_factory,
        book_id=plan.book_id,
        target_book_position=target_book_position,
    )
    verification = _verify(
        session_factory,
        plan,
        snapshots,
        target_book_position=target_book_position,
    )
    return {
        "accounts_closed": closed,
        "internal_accounts_verified": len(provisioned_accounts),
        "monthly_projection": projection,
        "repairs_replayed": replayed,
        "verification": verification,
    }


def _book_position(
    session_factory: sessionmaker[Session],
    book_id: UUID,
) -> int:
    with session_factory() as session, session.begin():
        head = session.get(BookEventHeadRecord, book_id)
        if head is None:
            raise RepairRunnerFailure("repair_verification_failed", exit_code=1)
        return int(head.last_position)


def _catch_up_monthly_projection(
    session_factory: sessionmaker[Session],
    *,
    book_id: UUID,
    target_book_position: int,
) -> dict[str, JSONValue]:
    worker = AsyncProjectionWorker(session_factory)
    processed_events = 0
    for _ in range(10_000):
        with session_factory() as session, session.begin():
            checkpoint = session.get(
                ProjectionCheckpointRecord,
                (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
            )
            checkpoint_position = (
                0 if checkpoint is None else int(checkpoint.last_book_position)
            )
        if checkpoint_position >= target_book_position:
            return {
                "last_book_position": checkpoint_position,
                "processed_events": processed_events,
                "target_book_position": target_book_position,
            }
        result = worker.run_once(book_id)
        processed_events += result.processed_events
        if result.paused or (
            result.processed_events == 0
            and result.last_book_position < target_book_position
        ):
            raise RepairRunnerFailure(
                "monthly_projection_catchup_failed",
                exit_code=1,
            )
    raise RepairRunnerFailure("monthly_projection_catchup_failed", exit_code=1)


def _verify(
    session_factory: sessionmaker[Session],
    plan: RepairPlan,
    snapshots: tuple[CandidateSnapshot, ...],
    *,
    target_book_position: int,
) -> dict[str, JSONValue]:
    with session_factory() as session, session.begin():
        affected_periods = {
            month_start(utc_date(snapshot.effective_at))
            for snapshot in snapshots
        }
        for item, before in zip(plan.repairs, snapshots, strict=True):
            expected_reversal = reversal_transaction_id(
                plan.book_id,
                item.original_transaction_id,
            )
            expected_replacement = replacement_transaction_id(
                plan.book_id,
                item.original_transaction_id,
            )
            reversal = session.scalar(
                select(TransactionReversalRecord.reversal_transaction_id).where(
                    TransactionReversalRecord.book_id == plan.book_id,
                    TransactionReversalRecord.original_transaction_id
                    == item.original_transaction_id,
                )
            )
            replacement = session.get(
                JournalTransactionRecord,
                (plan.book_id, expected_replacement),
            )
            category_units = session.scalar(
                select(func.coalesce(func.sum(ReportingLineRecord.units), 0)).where(
                    ReportingLineRecord.book_id == plan.book_id,
                    ReportingLineRecord.transaction_id == expected_replacement,
                    ReportingLineRecord.dimension == "category",
                    ReportingLineRecord.dimension_id == item.category_id,
                    ReportingLineRecord.line_kind == "expense",
                )
            )
            wrong_account = session.get(
                AccountRecord,
                (plan.book_id, item.wrong_expense_account_id),
            )
            wrong_balance = session.scalar(
                select(AccountBalanceRecord.balance_units).where(
                    AccountBalanceRecord.book_id == plan.book_id,
                    AccountBalanceRecord.account_id
                    == item.wrong_expense_account_id,
                    AccountBalanceRecord.asset_code == before.asset_code,
                )
            )
            source_balance = session.scalar(
                select(AccountBalanceRecord.balance_units).where(
                    AccountBalanceRecord.book_id == plan.book_id,
                    AccountBalanceRecord.account_id == before.source_account_id,
                    AccountBalanceRecord.asset_code == before.asset_code,
                )
            )
            clearing_id = canonical_expense_clearing_account_id(
                plan.book_id,
                before.asset_code,
            )
            clearing = session.get(
                AccountRecord,
                (plan.book_id, clearing_id),
            )
            replacement_references = _external_references(
                session,
                book_id=plan.book_id,
                transaction_id=expected_replacement,
            )
            if (
                reversal != expected_reversal
                or replacement is None
                or replacement.transaction_kind != before.transaction_kind
                or replacement.effective_at != before.effective_at
                or replacement.description_ref != before.description_ref
                or replacement_references != before.external_references
                or int(category_units or 0) != before.units
                or wrong_account is None
                or wrong_account.status != "closed"
                or int(wrong_balance or 0) != 0
                or int(source_balance or 0) != before.source_balance_units
                or clearing is None
                or clearing.system_role != "expense_clearing"
            ):
                raise RepairRunnerFailure("repair_verification_failed", exit_code=1)
        head = session.get(BookEventHeadRecord, plan.book_id)
        if head is None:
            raise RepairRunnerFailure("repair_verification_failed", exit_code=1)
        checkpoint = session.get(
            ProjectionCheckpointRecord,
            (PROJECTION_NAME, PROJECTOR_VERSION, plan.book_id),
        )
        if (
            checkpoint is None
            or int(checkpoint.last_book_position) < target_book_position
        ):
            raise RepairRunnerFailure("repair_verification_failed", exit_code=1)
        replay = cold_replay_monthly_summary(
            session,
            plan.book_id,
            through_book_position=int(checkpoint.last_book_position),
        )
        for period in affected_periods:
            if read_monthly_summary(
                session,
                plan.book_id,
                period_start=period,
            ) != replay.get(period, ()):
                raise RepairRunnerFailure(
                    "repair_verification_failed",
                    exit_code=1,
                )
        return {
            "book_position": int(head.last_position),
            "category_units_verified": sum(item.units for item in snapshots),
            "closed_accounts_verified": len(plan.close_account_ids),
            "descriptions_preserved": True,
            "external_references_preserved": True,
            "monthly_periods_verified": len(affected_periods),
            "repairs_verified": len(plan.repairs),
            "source_balances_unchanged": True,
            "wrong_account_balances_zero": True,
        }


def _external_references(
    session: Session,
    *,
    book_id: UUID,
    transaction_id: UUID,
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            row.provider_code,
            row.reference_kind,
            row.reference_value,
        )
        for row in session.scalars(
            select(TransactionExternalReferenceRecord)
            .where(
                TransactionExternalReferenceRecord.book_id == book_id,
                TransactionExternalReferenceRecord.transaction_id
                == transaction_id,
            )
            .order_by(
                TransactionExternalReferenceRecord.provider_code,
                TransactionExternalReferenceRecord.reference_kind,
            )
        )
    )


def _execute(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    environ: Mapping[str, str],
) -> dict[str, JSONValue]:
    expected_hash, apply = _parse_arguments(argv)
    plan = _parse_plan(_read_stdin(stdin), expected_hash)
    database_url = environ.get(DATABASE_URL_ENV)
    if type(database_url) is not str or not database_url:
        raise RepairRunnerFailure("runtime_configuration_invalid")
    engine = create_v2_engine(database_url)
    try:
        session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            future=True,
        )
        with session_factory() as session, session.begin():
            if not apply:
                session.execute(text("set transaction read only"))
            snapshots = _inspect(session, plan)
            active_asset_codes = tuple(
                session.scalars(
                    select(AssetRecord.asset_code)
                    .where(AssetRecord.status == "active")
                    .order_by(AssetRecord.asset_code)
                )
            )
        assets = sorted({snapshot.asset_code for snapshot in snapshots})
        summary: dict[str, JSONValue] = {
            "apply": apply,
            "assets": assets,
            "candidate_count": len(snapshots),
            "candidates": [snapshot.safe_value() for snapshot in snapshots],
            "create_category_paths": [
                list(path) for path in plan.create_category_paths
            ],
            "plan_sha256": expected_hash,
            "provision_internal_account_count": (
                len(active_asset_codes) * 3
                if plan.provision_all_active_internal_accounts
                else len(assets) * 3
            ),
            "total_units_by_asset": {
                asset: sum(
                    snapshot.units
                    for snapshot in snapshots
                    if snapshot.asset_code == asset
                )
                for asset in assets
            },
        }
        if apply:
            summary["result"] = _apply(
                plan,
                snapshots,
                session_factory=session_factory,
                active_asset_codes=active_asset_codes,
            )
        return summary
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        summary = _execute(
            tuple(sys.argv[1:] if argv is None else argv),
            stdin=sys.stdin.buffer,
            environ=os.environ,
        )
    except RepairRunnerFailure as error:
        sys.stderr.write(
            json.dumps({"error": error.code}, separators=(",", ":")) + "\n"
        )
        return error.exit_code
    except Exception:
        sys.stderr.write('{"error":"misclassified_expense_repair_failed"}\n')
        return 1
    sys.stdout.write(
        json.dumps(summary, separators=(",", ":"), sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
