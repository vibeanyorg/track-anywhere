from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...application.catalogs.close_account import (
    AccountAlreadyClosed,
    AccountBalanceNonzero,
    AccountBalanceProjectionMismatch,
)
from ...application.catalogs.reopen_account import AccountAlreadyActive
from ...application.credit_cards.record import CreditCardRefundConflict
from ...application.idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyConflict,
    IdempotencyValidationError,
)
from ...application.journal.correct_external_reference import (
    ExternalReferenceUnchanged,
)
from ...application.journal.correct_transaction import (
    CreditCardGeneralCorrectionForbidden,
)
from ...application.journal.post_transaction import CreditCardSemanticWriteRequired
from ...application.journal.reverse_transaction import (
    CreditCardChargeHasActiveRefunds,
    CreditCardReversalChainForbidden,
    CreditCardReversalPrecedesOriginal,
    CreditCardReversalRequiresActiveAccount,
    InvalidTransactionSource,
    TransactionAlreadyReversed,
    TransactionIdAlreadyExists,
)
from ...auth.errors import AuthPolicyDenied, AuthSecurityError
from ...auth.http import SESSION_COOKIE
from ...auth.security import require_same_origin
from ...auth.sessions import PersistentSessionService
from ...domain.investments.allocation import AllocationMethod
from ...domain.journal.events import ExternalReferenceKind, ReversalReasonCode
from ...domain.journal.models import (
    ACCOUNT_SUBTYPE_PATTERN,
    AccountType,
    PostingSide,
    TransactionKind,
)
from ...domain.reporting.events import ReportingDimension, ReportingLineKind
from ...infrastructure.db.event_store import StreamVersionConflict
from ..dependencies import SessionDependency


PlainDecimal = Annotated[
    StrictStr,
    Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$", min_length=1, max_length=96),
]
CanonicalUnits = Annotated[
    StrictStr,
    Field(pattern=r"^[1-9][0-9]{0,37}$", min_length=1, max_length=38),
]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
ZeroStreamVersion = Annotated[StrictInt, Field(ge=0, le=0)]
NonBlankText = Annotated[StrictStr, Field(min_length=1, max_length=512)]
AssetCode = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Z][A-Z0-9._-]{0,15}$"),
]
ProviderCode = Annotated[
    StrictStr,
    Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$"),
]
ExternalReferenceValue = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"),
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class CreateBookRequest(StrictRequest):
    book_id: UUID
    current_name: NonBlankText
    base_asset_code: AssetCode | None = None


class CreateAssetRequest(StrictRequest):
    asset_code: AssetCode
    kind: Annotated[StrictStr, Field(min_length=1, max_length=32)]
    ledger_scale: Annotated[StrictInt, Field(ge=0, le=30)]
    input_scale: Annotated[StrictInt, Field(ge=0, le=30)]
    display_scale: Annotated[StrictInt, Field(ge=0, le=30)]
    current_name: NonBlankText


class CreateAccountRequest(StrictRequest):
    account_id: UUID
    asset_code: AssetCode
    account_type: AccountType
    account_subtype: (
        Annotated[
            StrictStr,
            Field(pattern=ACCOUNT_SUBTYPE_PATTERN, max_length=64),
        ]
        | None
    ) = None
    current_name: NonBlankText
    system_role: Annotated[StrictStr, Field(min_length=1, max_length=32)] | None = None


class CreateCategoryRequest(StrictRequest):
    category_id: UUID
    category_version_id: UUID
    name: NonBlankText
    parent_category_id: UUID | None = None
    change_reason_code: Annotated[StrictStr, Field(min_length=1, max_length=64)]


class ExternalReferenceInput(StrictRequest):
    provider_code: ProviderCode
    kind: ExternalReferenceKind
    reference: ExternalReferenceValue


class JournalPostingInput(StrictRequest):
    posting_id: UUID
    account_id: UUID
    asset_code: AssetCode
    side: PostingSide
    amount: PlainDecimal


class PostTransactionRequest(StrictRequest):
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: NonNegativeInt
    kind: TransactionKind
    effective_at: AwareDatetime
    description_ref: UUID | None = None
    external_references: tuple[ExternalReferenceInput, ...] = ()
    postings: Annotated[tuple[JournalPostingInput, ...], Field(min_length=2)]


class ReverseTransactionRequest(StrictRequest):
    command_id: UUID
    reversal_transaction_id: UUID
    expected_stream_version: ZeroStreamVersion = 0
    reason_code: ReversalReasonCode
    effective_at: AwareDatetime
    description_ref: UUID | None = None


class CorrectionReplacementInput(StrictRequest):
    transaction_id: UUID
    expected_stream_version: ZeroStreamVersion = 0
    kind: TransactionKind
    effective_at: AwareDatetime
    description_ref: UUID | None = None
    external_references: tuple[ExternalReferenceInput, ...] = ()
    postings: Annotated[tuple[JournalPostingInput, ...], Field(min_length=2)]


class CorrectTransactionRequest(StrictRequest):
    command_id: UUID
    reversal_transaction_id: UUID
    expected_reversal_stream_version: ZeroStreamVersion = 0
    reason_code: ReversalReasonCode
    reversal_effective_at: AwareDatetime
    replacement: CorrectionReplacementInput
    reversal_description_ref: UUID | None = None


class CorrectExternalReferenceRequest(StrictRequest):
    command_id: UUID
    provider_code: ProviderCode
    reference_kind: ExternalReferenceKind
    corrected_reference: ExternalReferenceValue
    expected_stream_version: NonNegativeInt
    effective_at: AwareDatetime


class RecordFxRequest(StrictRequest):
    command_id: UUID
    transaction_id: UUID
    expected_stream_version: NonNegativeInt
    source_account_id: UUID
    source_trading_account_id: UUID
    source_asset_code: AssetCode
    source_amount: PlainDecimal
    target_trading_account_id: UUID
    target_account_id: UUID
    target_asset_code: AssetCode
    target_amount: PlainDecimal
    effective_at: AwareDatetime
    description_ref: UUID | None = None
    external_references: tuple[ExternalReferenceInput, ...] = ()


class ReportingLineInputRequest(StrictRequest):
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
    asset_code: AssetCode
    units: CanonicalUnits
    line_kind: ReportingLineKind
    dimension: ReportingDimension
    dimension_id: UUID | None = None
    description_ref: UUID | None = None


class AssignReportingLinesRequest(StrictRequest):
    command_id: UUID
    expected_revision: NonNegativeInt
    lines: Annotated[tuple[ReportingLineInputRequest, ...], Field(min_length=1)]
    effective_at: AwareDatetime


class ClearReportingLinesRequest(StrictRequest):
    command_id: UUID
    expected_revision: NonNegativeInt
    effective_at: AwareDatetime


class AcquireLotRequest(StrictRequest):
    command_id: UUID
    transaction_id: UUID
    lot_id: UUID
    instrument_asset_code: AssetCode
    settlement_asset_code: AssetCode
    quantity_units: CanonicalUnits
    cost_units: CanonicalUnits
    effective_at: AwareDatetime
    fee_units: CanonicalUnits | None = None
    expected_stream_version: ZeroStreamVersion = 0


class SpecificLotInput(StrictRequest):
    lot_id: UUID
    quantity_units: CanonicalUnits


class DisposeLotRequest(StrictRequest):
    command_id: UUID
    transaction_id: UUID
    instrument_asset_code: AssetCode
    settlement_asset_code: AssetCode
    quantity_units: CanonicalUnits
    proceeds_units: CanonicalUnits
    allocation_method: AllocationMethod
    effective_at: AwareDatetime
    fee_units: CanonicalUnits | None = None
    specific_lots: tuple[SpecificLotInput, ...] = ()
    expected_stream_version: ZeroStreamVersion = 0


@dataclass(frozen=True, slots=True)
class RequestActor:
    command_actor: CommandActor
    credential_book_id: UUID | None
    scopes: frozenset[str]

    def require_global_scope(self, scope: str) -> CommandActor:
        if self.credential_book_id is not None or scope not in self.scopes:
            raise HTTPException(status_code=403, detail="request is not authorized")
        return self.command_actor

    def require_book_scope(self, book_id: UUID, scope: str) -> CommandActor:
        if (
            self.credential_book_id is not None and self.credential_book_id != book_id
        ) or scope not in self.scopes:
            raise HTTPException(status_code=403, detail="request is not authorized")
        return self.command_actor


def create_actor_dependency(
    get_session: SessionDependency,
) -> Callable[..., RequestActor]:
    def authenticate_request_actor(
        request: Request,
        session: Session = Depends(get_session),
    ) -> RequestActor:
        authorization = request.headers.get("Authorization")
        api_key = request.headers.get("X-API-Key")
        session_token = request.cookies.get(SESSION_COOKIE)

        service = PersistentSessionService(session)
        if authorization is None and api_key is None and session_token is not None:
            active = service.current(session_token, lock=True)
            if active is None:
                raise _authentication_required()
            if not service.verify_csrf(
                active,
                request.headers.get("X-CSRF-Token"),
            ):
                raise HTTPException(
                    status_code=403,
                    detail="request is not authorized",
                )
            try:
                require_same_origin(
                    origin=request.headers.get("Origin"),
                    referer=request.headers.get("Referer"),
                    allowed_origin=str(request.base_url).rstrip("/"),
                )
            except AuthSecurityError as error:
                raise HTTPException(
                    status_code=403,
                    detail="request is not authorized",
                ) from error
            return RequestActor(
                command_actor=CommandActor(active.user.user_id),
                credential_book_id=active.credential.book_id,
                scopes=frozenset(active.credential.scopes),
            )

        raw_credential = api_key
        if authorization is not None:
            scheme, separator, token = authorization.partition(" ")
            if (
                scheme.lower() != "bearer"
                or separator != " "
                or not token
                or token != token.strip()
            ):
                raise _authentication_required()
            raw_credential = token
        if not raw_credential:
            raise _authentication_required()
        try:
            credential, user = service.authenticate_credential(raw_credential)
        except AuthPolicyDenied as error:
            raise _authentication_required() from error
        if (
            authorization is None
            and api_key is not None
            and credential.auth_kind != "api_key"
        ):
            raise _authentication_required()
        return RequestActor(
            command_actor=CommandActor(user.user_id),
            credential_book_id=credential.book_id,
            scopes=frozenset(credential.scopes),
        )

    return authenticate_request_actor


def require_idempotency_key(
    raw_key: Annotated[
        str | None,
        Header(alias="X-Idempotency-Key"),
    ] = None,
) -> str:
    if raw_key is None or not raw_key:
        raise HTTPException(
            status_code=400,
            detail="X-Idempotency-Key is required",
        )
    return raw_key


T = TypeVar("T")


def call_application(action: Callable[[], T]) -> T:
    try:
        return action()
    except IdempotencyConflict as error:
        raise HTTPException(
            status_code=409,
            detail="idempotency key conflict",
        ) from error
    except PermissionError as error:
        raise HTTPException(
            status_code=403,
            detail="request is not authorized",
        ) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="resource not found") from error
    except (
        AccountAlreadyClosed,
        AccountAlreadyActive,
        AccountBalanceNonzero,
        AccountBalanceProjectionMismatch,
        CreditCardChargeHasActiveRefunds,
        CreditCardGeneralCorrectionForbidden,
        CreditCardRefundConflict,
        CreditCardSemanticWriteRequired,
        CreditCardReversalChainForbidden,
        CreditCardReversalPrecedesOriginal,
        CreditCardReversalRequiresActiveAccount,
        ExternalReferenceUnchanged,
        InvalidTransactionSource,
        StreamVersionConflict,
        TransactionAlreadyReversed,
        TransactionIdAlreadyExists,
        IntegrityError,
    ) as error:
        raise HTTPException(status_code=409, detail="command conflict") from error
    except IdempotencyValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def command_response(outcome: CommandOutcome) -> JSONResponse:
    response = JSONResponse(
        content=outcome.result.body,
        status_code=outcome.result.status_code,
    )
    response.headers["Idempotency-Replayed"] = str(outcome.replayed).lower()
    return response


def _authentication_required() -> HTTPException:
    return HTTPException(status_code=401, detail="authentication is required")


__all__ = [
    "AcquireLotRequest",
    "AssignReportingLinesRequest",
    "ClearReportingLinesRequest",
    "CorrectExternalReferenceRequest",
    "CorrectTransactionRequest",
    "CreateAccountRequest",
    "CreateAssetRequest",
    "CreateBookRequest",
    "CreateCategoryRequest",
    "DisposeLotRequest",
    "PostTransactionRequest",
    "RecordFxRequest",
    "RequestActor",
    "ReverseTransactionRequest",
    "call_application",
    "command_response",
    "create_actor_dependency",
    "require_idempotency_key",
]
