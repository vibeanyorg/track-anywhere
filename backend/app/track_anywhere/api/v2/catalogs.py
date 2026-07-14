from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ...application.catalogs.close_account import (
    CloseAccount,
    close_account as execute_close_account,
)
from ...application.catalogs.create_account import (
    CreateAccount,
    create_account as execute_create_account,
)
from ...application.catalogs.create_asset import (
    CreateAsset,
    create_asset as execute_create_asset,
)
from ...application.catalogs.create_book import (
    CreateBook,
    create_book as execute_create_book,
)
from ...application.catalogs.create_category import (
    CreateCategory,
    create_category as execute_create_category,
)
from ...application.ledger_committer import LedgerCommitter
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .schemas import (
    CreateAccountRequest,
    CreateAssetRequest,
    CreateBookRequest,
    CreateCategoryRequest,
    RequestActor,
    call_application,
    create_actor_dependency,
)


def create_catalog_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter | None = None,
) -> APIRouter:
    router = APIRouter(tags=["catalogs"])
    request_actor = create_actor_dependency(get_session)
    committer = ledger_committer or LedgerCommitter()

    @router.post("/books", status_code=201)
    def create_book(
        payload: CreateBookRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> dict[str, object]:
        command_actor = actor.require_global_scope("book:write")
        return call_application(
            lambda: execute_create_book(
                CreateBook(
                    book_id=payload.book_id,
                    current_name=payload.current_name,
                    base_asset_code=payload.base_asset_code,
                ),
                actor=command_actor,
                uow_factory=uow_factory,
            )
        )

    @router.post("/books/{book_id}/assets", status_code=201)
    def create_asset(
        book_id: UUID,
        payload: CreateAssetRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> dict[str, object]:
        command_actor = actor.require_book_scope(book_id, "book:write")
        return call_application(
            lambda: execute_create_asset(
                CreateAsset(
                    book_id=book_id,
                    asset_code=payload.asset_code,
                    kind=payload.kind,
                    ledger_scale=payload.ledger_scale,
                    input_scale=payload.input_scale,
                    display_scale=payload.display_scale,
                    current_name=payload.current_name,
                ),
                actor=command_actor,
                uow_factory=uow_factory,
            )
        )

    @router.post("/books/{book_id}/accounts", status_code=201)
    def create_account(
        book_id: UUID,
        payload: CreateAccountRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> dict[str, object]:
        command_actor = actor.require_book_scope(book_id, "book:write")
        return call_application(
            lambda: execute_create_account(
                CreateAccount(
                    book_id=book_id,
                    account_id=payload.account_id,
                    asset_code=payload.asset_code,
                    account_type=payload.account_type,
                    current_name=payload.current_name,
                    system_role=payload.system_role,
                ),
                actor=command_actor,
                uow_factory=uow_factory,
            )
        )

    @router.post("/books/{book_id}/categories", status_code=201)
    def create_category(
        book_id: UUID,
        payload: CreateCategoryRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> dict[str, object]:
        command_actor = actor.require_book_scope(book_id, "book:write")
        return call_application(
            lambda: execute_create_category(
                CreateCategory(
                    book_id=book_id,
                    category_id=payload.category_id,
                    category_version_id=payload.category_version_id,
                    name=payload.name,
                    parent_category_id=payload.parent_category_id,
                    change_reason_code=payload.change_reason_code,
                ),
                actor=command_actor,
                uow_factory=uow_factory,
            )
        )

    @router.post("/books/{book_id}/accounts/{account_id}/close")
    def close_account(
        book_id: UUID,
        account_id: UUID,
        actor: RequestActor = Depends(request_actor),
    ) -> dict[str, object]:
        command_actor = actor.require_book_scope(book_id, "book:write")
        return call_application(
            lambda: execute_close_account(
                CloseAccount(book_id=book_id, account_id=account_id),
                actor=command_actor,
                uow_factory=uow_factory,
                ledger_committer=committer,
            )
        )

    return router


__all__ = ["create_catalog_router"]
