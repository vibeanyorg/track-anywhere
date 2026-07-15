from __future__ import annotations

from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from ..api.dependencies import SessionFactory
from ..api.v2.query_routes.catalog import (
    AccountResponse,
    AssetListResponse,
    BalanceItemResponse,
    BookListResponse,
    CategoryListResponse,
    serialize_account,
    serialize_asset,
    serialize_balance_item,
    serialize_book,
    serialize_category,
)
from ..api.v2.query_routes.journal import (
    JournalItemResponse,
    JournalPageResponse,
    serialize_journal_item,
    serialize_journal_page,
)
from ..queries.balances import get_book_balances, get_verified_book_balances
from ..queries.catalogs import (
    get_account,
    list_accessible_books,
    list_accounts,
    list_assets,
    list_categories,
)
from ..queries.journal import get_journal_transaction, list_journal
from .auth import require_access_token, require_book_access


SECURITY_SCHEMES = [{"type": "oauth2", "scopes": ["ledger:read"]}]
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
TOOL_META = {"securitySchemes": SECURITY_SCHEMES}


class AccountPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AccountResponse, ...]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class BalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BalanceItemResponse, ...]
    as_of_book_position: int
    projection_matches_reference: bool | None


def register_ledger_tools(mcp: FastMCP, session_factory: SessionFactory) -> None:
    @mcp.tool(
        name="ledger_list_books",
        title="List accessible Books",
        description=(
            "Use this when you need to discover which Track Anywhere Books the "
            "connected user can read. Returns stable Book IDs for other tools."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_books() -> BookListResponse:
        token = require_access_token()
        restricted = (token.claims or {}).get("book_id")
        with session_factory() as session:
            values = list_accessible_books(
                session,
                user_id=token.subject or "",
                restricted_book_id=None if restricted is None else UUID(str(restricted)),
            )
        return BookListResponse(items=tuple(serialize_book(item) for item in values))

    @mcp.tool(
        name="ledger_list_assets",
        title="List Book assets",
        description=(
            "Use this when you need asset scales and display metadata before "
            "interpreting integer ledger units in a Book."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_assets(book_id: UUID) -> AssetListResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                values = list_assets(session, book_id)
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return AssetListResponse(items=tuple(serialize_asset(item) for item in values))

    @mcp.tool(
        name="ledger_list_accounts",
        title="List Book accounts",
        description=(
            "Use this when you need accounts and their natural balances, including "
            "credit-card outstanding and overpayment units. Results are paginated."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_accounts(
        book_id: UUID,
        account_type: str | None = None,
        account_subtype: str | None = None,
        status: str | None = None,
        asset_code: str | None = None,
        name: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> AccountPage:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                values = list_accounts(
                    session,
                    book_id,
                    account_type=account_type,
                    account_subtype=account_subtype,
                    status=status,
                    asset_code=asset_code,
                    name=name,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        total = len(values)
        page = values[offset : offset + limit]
        next_offset = offset + limit if offset + limit < total else None
        return AccountPage(
            items=tuple(serialize_account(item) for item in page),
            total=total,
            offset=offset,
            limit=limit,
            next_offset=next_offset,
        )

    @mcp.tool(
        name="ledger_get_account",
        title="Get an account",
        description=(
            "Use this when you need one exact account and its current natural "
            "balance semantics from a known Book and account ID."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_get_account(book_id: UUID, account_id: UUID) -> AccountResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                value = get_account(session, book_id, account_id)
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return serialize_account(value)

    @mcp.tool(
        name="ledger_get_balances",
        title="Get Book balances",
        description=(
            "Use this when you need a complete balance snapshot at the current "
            "Book head or an immutable historical Book position."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_get_balances(
        book_id: UUID,
        as_of_book_position: Annotated[int | None, Field(ge=0)] = None,
    ) -> BalanceSnapshotResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                snapshot = (
                    get_verified_book_balances(session, book_id)
                    if as_of_book_position is None
                    else get_book_balances(
                        session,
                        book_id,
                        as_of_book_position=as_of_book_position,
                    )
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return BalanceSnapshotResponse(
            items=tuple(serialize_balance_item(item) for item in snapshot.items),
            as_of_book_position=snapshot.as_of_book_position,
            projection_matches_reference=snapshot.projection_matches_reference,
        )

    @mcp.tool(
        name="ledger_list_transactions",
        title="List Book transactions",
        description=(
            "Use this when you need chronological journal transactions. Follow "
            "next_cursor to continue without losing the snapshot position."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_transactions(
        book_id: UUID,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Field(max_length=256)] = None,
        as_of_book_position: Annotated[int | None, Field(ge=0)] = None,
    ) -> JournalPageResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                page = list_journal(
                    session,
                    book_id,
                    limit=limit,
                    cursor=cursor,
                    as_of_book_position=as_of_book_position,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return serialize_journal_page(page)

    @mcp.tool(
        name="ledger_get_transaction",
        title="Get a journal transaction",
        description=(
            "Use this when you need one exact journal transaction, including its "
            "postings, reversal links, and credit-card relationship."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_get_transaction(
        book_id: UUID,
        transaction_id: UUID,
        as_of_book_position: Annotated[int | None, Field(ge=0)] = None,
    ) -> JournalItemResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                item = get_journal_transaction(
                    session,
                    book_id,
                    transaction_id,
                    as_of_book_position=as_of_book_position,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return serialize_journal_item(item)

    @mcp.tool(
        name="ledger_list_categories",
        title="List Book categories",
        description=(
            "Use this when you need the current category catalog for a Book, "
            "including parent relationships and stable category IDs."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_categories(book_id: UUID) -> CategoryListResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                values = list_categories(session, book_id)
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return CategoryListResponse(
            items=tuple(serialize_category(item) for item in values)
        )


__all__ = [
    "AccountPage",
    "BalanceSnapshotResponse",
    "READ_ONLY_ANNOTATIONS",
    "SECURITY_SCHEMES",
    "register_ledger_tools",
]
