from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Annotated, Literal, TypeVar
from uuid import UUID, NAMESPACE_URL, uuid5

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.auth.provider import AccessToken
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from ..api.dependencies import RuntimeDependencies
from ..api.v2.schemas import AssetCode, PlainDecimal
from ..api.v2.query_routes.catalog import (
    AccountResponse,
    AssetListResponse,
    AssetResponse,
    BalanceItemResponse,
    BookListResponse,
    BookResponse,
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
from ..application.credit_cards.record import (
    PaymentCreditCardCommand,
    execute_payment_credit_card,
)
from ..application.catalogs.create_account import (
    CreateAccount,
    create_account as execute_create_account,
)
from ..application.catalogs.create_asset import (
    CreateOrReuseAssetCommand,
    execute_create_or_reuse_asset,
)
from ..application.catalogs.create_book import (
    CreateBook,
    create_book as execute_create_book,
)
from ..application.idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyConflict,
)
from ..application.journal.post_transaction import CreditCardSemanticWriteRequired
from ..application.journal.record_adjustment import (
    RecordAdjustmentCommand,
    execute_record_adjustment,
)
from ..application.journal.record_simple import (
    RecordTransferCommand,
    execute_record_transfer,
)
from ..infrastructure.db.event_store import StreamVersionConflict
from .auth import (
    require_access_token,
    require_book_access,
    require_book_catalog_write_access,
    require_book_read_access_token,
    require_book_write_access,
    require_catalog_write_access_token,
    require_global_catalog_write_access_token,
    require_write_access_token,
)


SECURITY_SCHEMES = [{"type": "oauth2", "scopes": ["ledger:read"]}]
BOOK_READ_SECURITY_SCHEMES = [
    {"type": "oauth2", "scopes": ["book:read", "ledger:read"]}
]
WRITE_SECURITY_SCHEMES = [{"type": "oauth2", "scopes": ["ledger:read", "ledger:write"]}]
CATALOG_WRITE_SECURITY_SCHEMES = [
    {
        "type": "oauth2",
        "scopes": ["book:read", "book:write", "ledger:read"],
    }
]
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
TOOL_META = {"securitySchemes": SECURITY_SCHEMES}
BOOK_READ_TOOL_META = {"securitySchemes": BOOK_READ_SECURITY_SCHEMES}
WRITE_TOOL_META = {"securitySchemes": WRITE_SECURITY_SCHEMES}
CATALOG_WRITE_TOOL_META = {"securitySchemes": CATALOG_WRITE_SECURITY_SCHEMES}
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
CATALOG_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_MCP_WRITE_ID_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/mcp/write-identity",
)
_LOGGER = logging.getLogger(__name__)
_CatalogValue = TypeVar("_CatalogValue")
_CatalogResult = TypeVar("_CatalogResult")


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


class LedgerWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    transaction_id: UUID
    committed: Literal[True] = True
    replayed: bool
    first_book_position: int
    last_book_position: int
    verification_status: Literal["verified", "pending"]
    transaction: JournalItemResponse | None
    retry_guidance: str


class BookCatalogWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    book: BookResponse | None
    verification_status: Literal["verified", "pending"] = "verified"
    retry_guidance: str = "No retry is needed after verified readback."


class AssetCatalogWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    created: bool
    asset: AssetResponse | None
    verification_status: Literal["verified", "pending"] = "verified"
    retry_guidance: str = "No retry is needed after verified readback."


class AccountCatalogWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    account: AccountResponse | None
    verification_status: Literal["verified", "pending"] = "verified"
    retry_guidance: str = "No retry is needed after verified readback."


def register_ledger_tools(mcp: FastMCP, dependencies: RuntimeDependencies) -> None:
    session_factory = dependencies.session_factory

    @mcp.tool(
        name="ledger_list_books",
        title="List accessible Books",
        description=(
            "Use this when you need to discover which Track Anywhere Books the "
            "connected user can read. Returns stable Book IDs for other tools."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=BOOK_READ_TOOL_META,
    )
    def ledger_list_books() -> BookListResponse:
        token = require_book_read_access_token()
        restricted = (token.claims or {}).get("book_id")
        with session_factory() as session:
            values = list_accessible_books(
                session,
                user_id=token.subject or "",
                restricted_book_id=None
                if restricted is None
                else UUID(str(restricted)),
            )
        return BookListResponse(items=tuple(serialize_book(item) for item in values))

    @mcp.tool(
        name="ledger_create_book",
        title="Create a Book",
        description=(
            "Use this when the connected user has no Track Anywhere Book and has "
            "explicitly asked to create one. The request_id deterministically "
            "identifies the new Book and must be reused only for an exact retry. "
            "base_asset_code must already exist; omit it during an empty-instance "
            "bootstrap."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_create_book(
        request_id: UUID,
        current_name: Annotated[str, Field(min_length=1, max_length=512)],
        base_asset_code: AssetCode | None = None,
    ) -> BookCatalogWriteResponse:
        token = require_global_catalog_write_access_token()
        book_id = _catalog_entity_id(
            token.subject or "",
            None,
            "ledger_create_book",
            request_id,
        )
        existing = _read_catalog_before_write(
            lambda: _read_created_book(dependencies, token, book_id),
            request_id=request_id,
            entity_label="Book",
        )
        if existing is not None:
            if (
                existing.current_name != current_name.strip()
                or existing.base_asset_code != base_asset_code
            ):
                raise ToolError(
                    "request_id already identifies a Book created with different "
                    "arguments. Reuse it only for the exact same request."
                )
            return BookCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                book=existing,
            )
        _call_catalog_write(
            lambda: execute_create_book(
                CreateBook(
                    book_id=book_id,
                    current_name=current_name,
                    base_asset_code=base_asset_code,
                ),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
            ),
            request_id=request_id,
        )
        created = _read_catalog_after_commit(
            lambda: _read_created_book(dependencies, token, book_id),
            request_id=request_id,
            entity_label="Book",
        )
        if created is None:
            return BookCatalogWriteResponse(
                request_id=request_id,
                replayed=False,
                book=None,
                verification_status="pending",
                retry_guidance=_catalog_retry_guidance(request_id),
            )
        return BookCatalogWriteResponse(
            request_id=request_id,
            replayed=False,
            book=created,
        )

    @mcp.tool(
        name="ledger_create_asset",
        title="Create or reuse an asset definition",
        description=(
            "Use this when a confirmed Book needs a currency, crypto, security, "
            "or other asset definition before accounts can reference it. Exact "
            "asset scale metadata must be user-confirmed; an identical existing "
            "asset is returned safely."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_create_asset(
        book_id: UUID,
        request_id: UUID,
        asset_code: AssetCode,
        kind: Annotated[str, Field(min_length=1, max_length=32)],
        ledger_scale: Annotated[int, Field(ge=0, le=30)],
        input_scale: Annotated[int, Field(ge=0, le=30)],
        display_scale: Annotated[int, Field(ge=0, le=30)],
        current_name: Annotated[str, Field(min_length=1, max_length=512)],
    ) -> AssetCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        command_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_asset",
            request_id,
        )
        outcome = _call_catalog_write(
            lambda: execute_create_or_reuse_asset(
                CreateOrReuseAssetCommand(
                    book_id=book_id,
                    command_id=command_id,
                    asset_code=asset_code,
                    kind=kind,
                    ledger_scale=ledger_scale,
                    input_scale=input_scale,
                    display_scale=display_scale,
                    current_name=current_name,
                ),
                raw_key=f"mcp:ledger_create_asset:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
            ),
            request_id=request_id,
        )
        created_by_command = _asset_created_by_command(outcome, request_id)
        created = _read_catalog_after_commit(
            lambda: _read_created_asset(dependencies, book_id, asset_code),
            request_id=request_id,
            entity_label="asset",
        )
        if created is None:
            return AssetCatalogWriteResponse(
                request_id=request_id,
                replayed=outcome.replayed,
                created=created_by_command,
                asset=None,
                verification_status="pending",
                retry_guidance=_catalog_retry_guidance(request_id),
            )
        return AssetCatalogWriteResponse(
            request_id=request_id,
            replayed=outcome.replayed,
            created=created_by_command,
            asset=created,
        )

    @mcp.tool(
        name="ledger_create_account",
        title="Create a standard account",
        description=(
            "Use this when the user has explicitly confirmed a Book, an existing "
            "asset definition, asset or liability account type, optional subtype, "
            "and account name. This ordinary Agent tool cannot create expense, "
            "income, equity, fund, investment, or system-managed accounts. Those "
            "remain available only through their dedicated or administrative "
            "workflows. Reuse request_id only for an exact retry."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_create_account(
        book_id: UUID,
        request_id: UUID,
        asset_code: AssetCode,
        account_type: Literal["asset", "liability"],
        current_name: Annotated[str, Field(min_length=1, max_length=512)],
        account_subtype: Annotated[
            str | None,
            Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", max_length=64),
        ] = None,
    ) -> AccountCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        account_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_account",
            request_id,
        )
        existing = _read_catalog_before_write(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if existing is not None:
            if (
                existing.asset_code != asset_code
                or existing.account_type != account_type
                or existing.account_subtype != account_subtype
                or existing.current_name != current_name.strip()
                or existing.system_role is not None
            ):
                raise ToolError(
                    "request_id already identifies an account created with different "
                    "arguments. Reuse it only for the exact same request."
                )
            return AccountCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                account=existing,
            )
        _call_catalog_write(
            lambda: execute_create_account(
                CreateAccount(
                    book_id=book_id,
                    account_id=account_id,
                    asset_code=asset_code,
                    account_type=account_type,
                    account_subtype=account_subtype,
                    current_name=current_name,
                    system_role=None,
                ),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
            ),
            request_id=request_id,
        )
        created = _read_catalog_after_commit(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if created is None:
            return AccountCatalogWriteResponse(
                request_id=request_id,
                replayed=False,
                account=None,
                verification_status="pending",
                retry_guidance=_catalog_retry_guidance(request_id),
            )
        return AccountCatalogWriteResponse(
            request_id=request_id,
            replayed=False,
            account=created,
        )

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

    @mcp.tool(
        name="ledger_record_transfer",
        title="Record an asset transfer",
        description=(
            "Use this when the user has explicitly confirmed a same-asset transfer "
            "between two standard user, non-credit-card asset accounts, including "
            "the exact amount and effective time. amount is a decimal string in the "
            "asset's major unit, never integer ledger units; `660` means 660.00 for "
            "a scale-2 asset. Never select a system-managed account. Reuse "
            "request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_record_transfer(
        book_id: UUID,
        request_id: UUID,
        source_account_id: UUID,
        target_account_id: UUID,
        asset_code: AssetCode,
        amount: PlainDecimal,
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_record_transfer",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_record_transfer(
                RecordTransferCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_stream_version=0,
                    source_account_id=source_account_id,
                    target_account_id=target_account_id,
                    asset_code=asset_code,
                    amount=amount,
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_record_transfer:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        return _write_response(
            dependencies,
            book_id,
            request_id,
            transaction_id,
            outcome,
        )

    @mcp.tool(
        name="ledger_record_adjustment",
        title="Reconcile an account balance",
        description=(
            "Use this when the user has explicitly confirmed a standard asset "
            "account's current ledger balance and actual counted balance, or a "
            "standard credit-card account's current and actual outstanding balance. "
            "Balance values are human-readable decimal amounts such as 90.00, never "
            "integer ledger units. The service records the difference as an "
            "adjustment against the Book's system adjustment account; it never edits "
            "a balance field directly. Reuse request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_record_adjustment(
        book_id: UUID,
        request_id: UUID,
        account_id: UUID,
        asset_code: AssetCode,
        expected_balance: PlainDecimal,
        actual_balance: PlainDecimal,
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_record_adjustment",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_record_adjustment(
                RecordAdjustmentCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_stream_version=0,
                    account_id=account_id,
                    asset_code=asset_code,
                    expected_balance=expected_balance,
                    actual_balance=actual_balance,
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_record_adjustment:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        return _write_response(
            dependencies,
            book_id,
            request_id,
            transaction_id,
            outcome,
        )

    @mcp.tool(
        name="ledger_record_credit_card_payment",
        title="Record a credit-card payment",
        description=(
            "Use this when the user has explicitly confirmed a payment from a "
            "standard user asset account to a standard credit-card account, including "
            "the exact amount, asset, and effective time. amount is a decimal string "
            "in the asset's major unit, never integer ledger units; `660` means "
            "660.00 for a scale-2 asset. Never select a system-managed account. "
            "Reuse request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_record_credit_card_payment(
        book_id: UUID,
        request_id: UUID,
        source_account_id: UUID,
        card_account_id: UUID,
        asset_code: AssetCode,
        amount: PlainDecimal,
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_record_credit_card_payment",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_payment_credit_card(
                PaymentCreditCardCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_stream_version=0,
                    card_account_id=card_account_id,
                    source_account_id=source_account_id,
                    asset_code=asset_code,
                    amount=amount,
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_record_credit_card_payment:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        return _write_response(
            dependencies,
            book_id,
            request_id,
            transaction_id,
            outcome,
        )


def _require_catalog_book(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    request_id: UUID,
) -> AccessToken:
    try:
        token = require_catalog_write_access_token()
        with dependencies.session_factory() as session:
            require_book_catalog_write_access(session, token, book_id)
        return token
    except ToolError:
        raise
    except Exception:
        _log_write_boundary("mcp_catalog_access_check_failed", request_id)
        raise ToolError(
            "Unable to verify Book management access. No catalog write was "
            f"attempted. Retry only with request_id {request_id} and the exact "
            "same arguments."
        ) from None


def _catalog_entity_id(
    subject_id: str,
    book_id: UUID | None,
    tool_name: str,
    request_id: UUID,
) -> UUID:
    scope = "global" if book_id is None else str(book_id)
    material = f"{subject_id}:{scope}:{tool_name}:{request_id}"
    return uuid5(_MCP_WRITE_ID_NAMESPACE, f"{material}:catalog")


def _read_created_book(
    dependencies: RuntimeDependencies,
    token: AccessToken,
    book_id: UUID,
) -> BookResponse | None:
    with dependencies.session_factory() as session:
        values = list_accessible_books(
            session,
            user_id=token.subject or "",
            restricted_book_id=book_id,
        )
    return None if not values else serialize_book(values[0])


def _read_created_asset(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    asset_code: str,
) -> AssetResponse | None:
    with dependencies.session_factory() as session:
        values = list_assets(session, book_id)
    match = next((value for value in values if value.asset_code == asset_code), None)
    return None if match is None else serialize_asset(match)


def _read_created_account(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    account_id: UUID,
) -> AccountResponse | None:
    try:
        with dependencies.session_factory() as session:
            value = get_account(session, book_id, account_id)
    except LookupError:
        return None
    return serialize_account(value)


def _read_catalog_before_write(
    callback: Callable[[], _CatalogValue | None],
    *,
    request_id: UUID,
    entity_label: str,
) -> _CatalogValue | None:
    try:
        return callback()
    except ToolError:
        raise
    except Exception:
        _log_write_boundary("mcp_catalog_prewrite_read_failed", request_id)
        raise ToolError(
            f"Unable to verify the existing {entity_label}. No catalog write was "
            f"attempted. Retry only with request_id {request_id} and the exact "
            "same arguments."
        ) from None


def _read_catalog_after_commit(
    callback: Callable[[], _CatalogValue | None],
    *,
    request_id: UUID,
    entity_label: str,
) -> _CatalogValue | None:
    try:
        return callback()
    except Exception:
        _log_write_boundary("mcp_catalog_readback_pending", request_id)
        return None


def _catalog_retry_guidance(request_id: UUID) -> str:
    return (
        "The catalog write committed but readback is pending. Retry only with "
        f"request_id {request_id} and the exact same arguments."
    )


def _asset_created_by_command(
    outcome: CommandOutcome,
    request_id: UUID,
) -> bool:
    body = outcome.result.body
    if isinstance(body, dict) and type(body.get("created")) is bool:
        return bool(body["created"])
    _log_write_boundary("mcp_catalog_result_invalid", request_id)
    raise ToolError(
        "Asset write committed with an unreadable result. Retry only with "
        f"request_id {request_id} and the exact same arguments."
    )


def _call_catalog_write(
    callback: Callable[[], _CatalogResult],
    *,
    request_id: UUID,
) -> _CatalogResult:
    try:
        return callback()
    except ToolError:
        raise
    except (IdempotencyConflict, LookupError, PermissionError, ValueError) as error:
        raise ToolError(str(error)) from error
    except SQLAlchemyError:
        _log_write_boundary("mcp_catalog_write_outcome_unknown", request_id)
        raise ToolError(
            "Catalog write outcome is unknown. Do not create a new request_id. "
            f"List the affected catalog, then retry only with request_id {request_id} "
            "and the exact same arguments if the resource is absent."
        ) from None
    except Exception:
        _log_write_boundary("mcp_catalog_write_outcome_unknown", request_id)
        raise ToolError(
            "Catalog write outcome is unknown. Do not create a new request_id. "
            f"List the affected catalog, then retry only with request_id {request_id} "
            "and the exact same arguments if the resource is absent."
        ) from None


def _require_write_book(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    request_id: UUID,
) -> AccessToken:
    try:
        token = require_write_access_token()
        with dependencies.session_factory() as session:
            require_book_write_access(session, token, book_id)
        return token
    except ToolError:
        raise
    except Exception:
        _log_write_boundary("mcp_write_access_check_failed", request_id)
        raise ToolError(
            "Unable to verify ledger write access. No ledger write was attempted. "
            f"Retry only with request_id {request_id} and the exact same arguments."
        ) from None


def _write_ids(
    subject_id: str,
    book_id: UUID,
    tool_name: str,
    request_id: UUID,
) -> tuple[UUID, UUID]:
    material = f"{subject_id}:{book_id}:{tool_name}:{request_id}"
    return (
        uuid5(_MCP_WRITE_ID_NAMESPACE, f"{material}:command"),
        uuid5(_MCP_WRITE_ID_NAMESPACE, f"{material}:transaction"),
    )


def _call_write(
    callback: Callable[[], CommandOutcome],
    *,
    request_id: UUID,
) -> CommandOutcome:
    try:
        return callback()
    except ToolError:
        raise
    except SQLAlchemyError:
        _log_write_boundary("mcp_write_outcome_unknown", request_id)
        raise ToolError(_unknown_write_message(request_id)) from None
    except (
        CreditCardSemanticWriteRequired,
        IdempotencyConflict,
        StreamVersionConflict,
    ) as error:
        raise ToolError(str(error)) from error
    except (LookupError, PermissionError, ValueError) as error:
        raise ToolError(str(error)) from error
    except Exception:
        _log_write_boundary("mcp_write_outcome_unknown", request_id)
        raise ToolError(_unknown_write_message(request_id)) from None


def _write_response(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    request_id: UUID,
    transaction_id: UUID,
    outcome: CommandOutcome,
) -> LedgerWriteResponse:
    first_position = outcome.result.first_book_position
    last_position = outcome.result.last_book_position
    if first_position is None or last_position is None:
        raise ToolError("The ledger write completed without a Book position.")
    persisted_transaction_id = _persisted_transaction_id(outcome, transaction_id)
    transaction_response = None
    verification_status: Literal["verified", "pending"] = "pending"
    try:
        with dependencies.session_factory() as session:
            transaction = get_journal_transaction(
                session,
                book_id,
                persisted_transaction_id,
                as_of_book_position=last_position,
            )
            transaction_response = serialize_journal_item(transaction)
            verification_status = "verified"
    except Exception:
        # The financial command and receipt are already committed. A transient
        # readback failure must not disguise that success and invite a second
        # request_id that could create a duplicate transaction.
        _log_write_boundary("mcp_write_readback_pending", request_id)
    return LedgerWriteResponse(
        request_id=request_id,
        transaction_id=persisted_transaction_id,
        replayed=outcome.replayed,
        first_book_position=first_position,
        last_book_position=last_position,
        verification_status=verification_status,
        transaction=transaction_response,
        retry_guidance=(
            "If verification is pending, retry only with request_id "
            f"{request_id} and the exact same arguments."
        ),
    )


def _unknown_write_message(request_id: UUID) -> str:
    return (
        "Ledger write outcome is unknown. Do not create a new request_id. "
        f"Retry only with request_id {request_id} and the exact same arguments."
    )


def _log_write_boundary(event: str, request_id: UUID) -> None:
    # Exception text is deliberately excluded: database exceptions can contain
    # SQL statements and bind parameters with private ledger data.
    _LOGGER.error("%s request_id=%s", event, request_id)


def _persisted_transaction_id(
    outcome: CommandOutcome,
    fallback: UUID,
) -> UUID:
    body = outcome.result.body
    if isinstance(body, dict):
        value = body.get("transaction_id")
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            pass
    return fallback


__all__ = [
    "AccountCatalogWriteResponse",
    "AccountPage",
    "AssetCatalogWriteResponse",
    "BalanceSnapshotResponse",
    "BOOK_READ_SECURITY_SCHEMES",
    "BookCatalogWriteResponse",
    "CATALOG_WRITE_ANNOTATIONS",
    "CATALOG_WRITE_SECURITY_SCHEMES",
    "LedgerWriteResponse",
    "READ_ONLY_ANNOTATIONS",
    "SECURITY_SCHEMES",
    "WRITE_ANNOTATIONS",
    "WRITE_SECURITY_SCHEMES",
    "register_ledger_tools",
]
