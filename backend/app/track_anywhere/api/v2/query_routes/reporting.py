from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ....queries.balances import BalanceSnapshot, get_book_balances
from ....queries.reporting import ReportingLine, list_current_reporting_lines
from .authorization import AuthorizedSessionDependency
from .catalog import BalanceItemResponse, serialize_balance_item


class BalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BalanceItemResponse, ...]
    as_of_book_position: int
    projection_matches_reference: bool | None


class ReportingLineResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction_id: UUID
    classification_revision: int
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
    line_position: int
    asset_code: str
    units: str
    line_kind: str
    dimension: str
    dimension_id: UUID | None


class ReportingLinesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReportingLineResponse, ...]
    as_of_book_position: int


def create_reporting_query_router(
    authorized_session: AuthorizedSessionDependency,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/books/{book_id}/balances",
        response_model=BalanceSnapshotResponse,
    )
    def balances(
        book_id: UUID,
        session: Session = Depends(authorized_session),
        as_of_book_position: int | None = Query(default=None, ge=0),
    ) -> BalanceSnapshotResponse:
        try:
            snapshot = get_book_balances(
                session,
                book_id,
                as_of_book_position=as_of_book_position,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return serialize_balance_snapshot(snapshot)

    @router.get(
        "/books/{book_id}/reporting-lines",
        response_model=ReportingLinesResponse,
    )
    def reporting_lines(
        book_id: UUID,
        as_of_book_position: int = Query(ge=0),
        session: Session = Depends(authorized_session),
    ) -> ReportingLinesResponse:
        try:
            lines = list_current_reporting_lines(
                session,
                book_id,
                as_of_book_position=as_of_book_position,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return serialize_reporting_lines(lines, as_of_book_position)

    return router


def serialize_balance_snapshot(
    snapshot: BalanceSnapshot,
) -> BalanceSnapshotResponse:
    return BalanceSnapshotResponse(
        items=tuple(serialize_balance_item(item) for item in snapshot.items),
        as_of_book_position=snapshot.as_of_book_position,
        projection_matches_reference=snapshot.projection_matches_reference,
    )


def serialize_reporting_lines(
    lines: tuple[ReportingLine, ...],
    as_of_book_position: int,
) -> ReportingLinesResponse:
    return ReportingLinesResponse(
        items=tuple(
            ReportingLineResponse(
                transaction_id=line.transaction_id,
                classification_revision=line.classification_revision,
                line_id=line.line_id,
                line_version_id=line.line_version_id,
                catalog_id=line.catalog_id,
                line_position=line.line_position,
                asset_code=line.asset_code,
                units=str(line.units),
                line_kind=line.line_kind,
                dimension=line.dimension,
                dimension_id=line.dimension_id,
            )
            for line in lines
        ),
        as_of_book_position=as_of_book_position,
    )


__all__ = [
    "BalanceSnapshotResponse",
    "ReportingLineResponse",
    "ReportingLinesResponse",
    "create_reporting_query_router",
    "serialize_balance_snapshot",
    "serialize_reporting_lines",
]
