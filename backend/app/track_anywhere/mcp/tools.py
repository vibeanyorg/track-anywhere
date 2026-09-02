from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
import logging
from typing import Annotated, Literal, TypeVar
from uuid import UUID, NAMESPACE_URL, uuid5

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.auth.provider import AccessToken
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..api.dependencies import RuntimeDependencies
from ..api.v2.entry_schemas import EverydayEntryReceiptResponse
from ..api.v2.schemas import AssetCode, PlainDecimal
from ..api.v2.query_routes.catalog import (
    AccountResponse,
    AssetListResponse,
    AssetResponse,
    BalanceItemResponse,
    BookListResponse,
    BookResponse,
    CategoryResponse,
    serialize_account,
    serialize_asset,
    serialize_balance_item,
    serialize_book,
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
    list_account_page,
    list_assets,
)
from ..queries.everyday_entries import (
    EverydayEntryView,
    get_everyday_entry,
    list_everyday_entries,
)
from ..queries.journal import get_journal_transaction, list_journal
from ..queries.reporting import ReportingLine, list_current_reporting_lines
from ..application.credit_cards.record import (
    PaymentCreditCardCommand,
    execute_payment_credit_card,
)
from ..application.catalogs.close_account import (
    AccountBalanceNonzero,
    AccountBalanceProjectionMismatch,
    CloseAccount,
    close_account as execute_close_account,
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
from ..application.catalogs.create_category import (
    CreateCategory,
    create_category as execute_create_category,
)
from ..application.catalogs.reopen_account import (
    ReopenAccount,
    reopen_account as execute_reopen_account,
)
from ..application.idempotency import (
    CommandActor,
    CommandOutcome,
    IdempotencyConflict,
)
from ..application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from ..application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    execute_assign_reporting_lines,
)
from ..application.journal.clear_reporting_lines import (
    ClearReportingLinesCommand,
    execute_clear_reporting_lines,
)
from ..application.journal.post_transaction import CreditCardSemanticWriteRequired
from ..application.journal.record_adjustment import (
    RecordAdjustmentCommand,
    execute_record_adjustment,
)
from ..application.journal.record_fx import (
    RecordFxCommand,
    RecordFxCreditCardPaymentCommand,
    execute_record_fx,
    execute_record_fx_credit_card_payment,
)
from ..application.journal.record_simple import (
    RecordTransferCommand,
    execute_record_transfer,
)
from ..application.payment_instruments import (
    CardFormFactor,
    CardNetwork,
    CreatePaymentInstrument,
    PaymentInstrumentView,
    SettlementPolicy,
    create_payment_instrument,
    get_payment_instrument,
    list_payment_instruments,
)
from ..domain.journal.events import ReversalReasonCode
from ..domain.reporting.events import ReportingDimension, ReportingLineKind
from ..infrastructure.db.models.catalog import (
    CategoryRecord,
    CategoryVersionRecord,
)
from ..infrastructure.db.event_store import StreamVersionConflict
from ..infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
)
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
DESTRUCTIVE_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
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


class CategoryDetailResponse(CategoryResponse):
    path: tuple[str, ...]
    usage_kind: Literal["expense", "income", "both"]


class CategoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CategoryDetailResponse, ...]


class CategoryCatalogWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    category: CategoryDetailResponse | None
    verification_status: Literal["verified", "pending"] = "verified"
    retry_guidance: str = "No retry is needed after verified readback."


class EverydayEntryPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[EverydayEntryReceiptResponse, ...]
    next_cursor: str | None
    as_of_book_position: int


class TransactionCategoryWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    transaction_id: UUID
    committed: Literal[True] = True
    replayed: bool
    category: CategoryDetailResponse | None
    classification_revision: int
    first_book_position: int | None
    last_book_position: int | None
    verification_status: Literal["verified", "pending"]
    retry_guidance: str


class PaymentInstrumentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[PaymentInstrumentView, ...]


class PaymentInstrumentCatalogWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    committed: Literal[True] = True
    replayed: bool
    instrument: PaymentInstrumentView


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
        name="ledger_create_category",
        title="Create a bookkeeping category",
        description=(
            "Use this when the user has explicitly confirmed that no existing "
            "category fits and wants a new root category or child category. List "
            "categories first, then pass the parent_category_id for a child. A "
            "category is an independent reporting dimension: never create or ask "
            "for an expense, income, clearing, or other internal account. The new "
            "category can be used for both expenses and income. Reuse request_id "
            "only for an exact retry."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_create_category(
        book_id: UUID,
        request_id: UUID,
        current_name: Annotated[str, Field(min_length=1, max_length=512)],
        parent_category_id: UUID | None = None,
    ) -> CategoryCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        category_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_category",
            request_id,
        )
        category_version_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_category_version",
            request_id,
        )
        existing = _read_catalog_before_write(
            lambda: _read_created_category(dependencies, book_id, category_id),
            request_id=request_id,
            entity_label="category",
        )
        if existing is not None:
            if (
                existing.current_name != current_name.strip()
                or existing.parent_category_id != parent_category_id
            ):
                raise ToolError(
                    "request_id already identifies a category created with different "
                    "arguments. Reuse it only for the exact same request."
                )
            return CategoryCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                category=existing,
            )
        if parent_category_id is not None:
            parent = _read_catalog_before_write(
                lambda: _read_created_category(
                    dependencies,
                    book_id,
                    parent_category_id,
                ),
                request_id=request_id,
                entity_label="parent category",
            )
            if parent is None or parent.status != "active":
                raise ToolError(
                    "parent_category_id must identify an active category in the "
                    "requested Book. List categories and choose a stable category ID."
                )
        _call_catalog_write(
            lambda: execute_create_category(
                CreateCategory(
                    book_id=book_id,
                    category_id=category_id,
                    category_version_id=category_version_id,
                    name=current_name,
                    parent_category_id=parent_category_id,
                    change_reason_code="mcp_created",
                ),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
            ),
            request_id=request_id,
        )
        created = _read_catalog_after_commit(
            lambda: _read_created_category(dependencies, book_id, category_id),
            request_id=request_id,
            entity_label="category",
        )
        if created is None:
            return CategoryCatalogWriteResponse(
                request_id=request_id,
                replayed=False,
                category=None,
                verification_status="pending",
                retry_guidance=_catalog_retry_guidance(request_id),
            )
        return CategoryCatalogWriteResponse(
            request_id=request_id,
            replayed=False,
            category=created,
        )

    @mcp.tool(
        name="ledger_close_account",
        title="Close an unused account",
        description=(
            "Use this when the user has explicitly confirmed an ordinary account "
            "should be hidden from future bookkeeping. Closing is reversible but "
            "requires a verified zero balance and never deletes transaction history. "
            "System-managed accounts cannot be closed through this tool. Reuse "
            "request_id only for an exact retry."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_close_account(
        book_id: UUID,
        request_id: UUID,
        account_id: UUID,
    ) -> AccountCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        existing = _read_catalog_before_write(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if existing is None:
            raise ToolError("account not found in requested Book")
        if existing.system_role is not None:
            raise ToolError("system-managed accounts cannot be closed by an Agent")
        if existing.status == "closed":
            return AccountCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                account=existing,
            )
        _call_catalog_write(
            lambda: execute_close_account(
                CloseAccount(book_id=book_id, account_id=account_id),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        updated = _read_catalog_after_commit(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if updated is None:
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
            account=updated,
        )

    @mcp.tool(
        name="ledger_reopen_account",
        title="Reopen a closed account",
        description=(
            "Use this when the user has explicitly confirmed that a previously "
            "closed ordinary account should accept new bookkeeping entries again. "
            "This does not alter historical transactions. System-managed accounts "
            "cannot be changed through this tool. Reuse request_id only for an "
            "exact retry."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_reopen_account(
        book_id: UUID,
        request_id: UUID,
        account_id: UUID,
    ) -> AccountCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        existing = _read_catalog_before_write(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if existing is None:
            raise ToolError("account not found in requested Book")
        if existing.system_role is not None:
            raise ToolError("system-managed accounts cannot be reopened by an Agent")
        if existing.status == "active":
            return AccountCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                account=existing,
            )
        _call_catalog_write(
            lambda: execute_reopen_account(
                ReopenAccount(book_id=book_id, account_id=account_id),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        updated = _read_catalog_after_commit(
            lambda: _read_created_account(dependencies, book_id, account_id),
            request_id=request_id,
            entity_label="account",
        )
        if updated is None:
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
            account=updated,
        )

    @mcp.tool(
        name="ledger_create_payment_card",
        title="Create a payment card",
        description=(
            "Use this when the user wants to create a generic physical or virtual "
            "payment card for the first time and bind it to the account that drives "
            "its purchases. This is one-time configuration, not a step repeated for "
            "every expense. Choose immediate or prepaid for an asset account, and "
            "statement for a credit-card liability account. Once configured, pass "
            "the returned instrument_id for purchases and let the service apply the "
            "saved settlement behavior. "
            "The card network and provider are descriptive; no provider-specific "
            "ledger behavior is used. Never submit a full card number, CVV, PIN, "
            "or credential. Reuse request_id only for an exact retry."
        ),
        annotations=CATALOG_WRITE_ANNOTATIONS,
        meta=CATALOG_WRITE_TOOL_META,
    )
    def ledger_create_payment_card(
        book_id: UUID,
        request_id: UUID,
        current_name: Annotated[str, Field(min_length=1, max_length=512)],
        form_factor: CardFormFactor,
        network: CardNetwork,
        provider_code: Annotated[
            str,
            Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$"),
        ],
        settlement_policy: SettlementPolicy,
        settlement_account_id: UUID,
        asset_code: AssetCode,
        effective_from: AwareDatetime,
        last4: Annotated[
            str | None,
            Field(pattern=r"^[0-9]{4}$"),
        ] = None,
    ) -> PaymentInstrumentCatalogWriteResponse:
        token = _require_catalog_book(dependencies, book_id, request_id)
        instrument_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_payment_card",
            request_id,
        )
        binding_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_create_payment_card_binding",
            request_id,
        )
        try:
            with session_factory() as session:
                existing = get_payment_instrument(
                    session,
                    book_id=book_id,
                    instrument_id=instrument_id,
                )
        except LookupError:
            existing = None
        if existing is not None:
            expected = (
                current_name.strip(),
                form_factor,
                network,
                provider_code,
                settlement_policy,
                settlement_account_id,
                asset_code,
                last4,
                effective_from,
            )
            actual = (
                existing.current_name,
                existing.form_factor,
                existing.network,
                existing.provider_code,
                existing.settlement_policy,
                existing.settlement_account_id,
                existing.asset_code,
                existing.last4,
                existing.effective_from,
            )
            if actual != expected:
                raise ToolError(
                    "request_id already identifies a payment card created with "
                    "different arguments. Reuse it only for the exact same request."
                )
            return PaymentInstrumentCatalogWriteResponse(
                request_id=request_id,
                replayed=True,
                instrument=existing,
            )
        created = _call_catalog_write(
            lambda: create_payment_instrument(
                CreatePaymentInstrument(
                    book_id=book_id,
                    instrument_id=instrument_id,
                    binding_id=binding_id,
                    current_name=current_name,
                    form_factor=form_factor,
                    network=network,
                    provider_code=provider_code,
                    settlement_policy=settlement_policy,
                    settlement_account_id=settlement_account_id,
                    asset_code=asset_code,
                    last4=last4,
                    effective_from=effective_from,
                ),
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
            ),
            request_id=request_id,
        )
        return PaymentInstrumentCatalogWriteResponse(
            request_id=request_id,
            replayed=False,
            instrument=created,
        )

    @mcp.tool(
        name="ledger_list_payment_instruments",
        title="List payment instruments",
        description=(
            "Use this when you need configured payment cards and their current "
            "account bindings. When the user names a card, select a unique match and "
            "use its instrument_id in expense or statement-payment preparation. The "
            "service selects the correct funding asset or card liability "
            "automatically; do not ask the user to choose that account again."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_payment_instruments(
        book_id: UUID,
        status: str | None = "active",
        asset_code: str | None = None,
        name: str | None = None,
    ) -> PaymentInstrumentPage:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                values = list_payment_instruments(
                    session,
                    book_id=book_id,
                    status=status,
                    asset_code=asset_code,
                    name=name,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return PaymentInstrumentPage(items=values)

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
                page = list_account_page(
                    session,
                    book_id,
                    account_type=account_type,
                    account_subtype=account_subtype,
                    status=status,
                    asset_code=asset_code,
                    name=name,
                    limit=limit,
                    offset=offset,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        next_offset = offset + limit if offset + limit < page.total else None
        return AccountPage(
            items=tuple(serialize_account(item) for item in page.items),
            total=page.total,
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
        name="ledger_list_entries",
        title="List everyday bookkeeping entries",
        description=(
            "Use this when the user wants transaction history in everyday "
            "bookkeeping terms such as expense, income, transfer, refund, or "
            "credit-card payment. Results include display account names, category "
            "paths, asset-unit amounts, and reversal relationships without exposing "
            "internal clearing accounts or debit and credit postings."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_entries(
        book_id: UUID,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Field(max_length=256)] = None,
        as_of_book_position: Annotated[int | None, Field(ge=0)] = None,
    ) -> EverydayEntryPageResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                page = list_everyday_entries(
                    session,
                    book_id,
                    limit=limit,
                    cursor=cursor,
                    as_of_book_position=as_of_book_position,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return EverydayEntryPageResponse(
            items=tuple(
                EverydayEntryReceiptResponse.from_view(item) for item in page.items
            ),
            next_cursor=page.next_cursor,
            as_of_book_position=page.as_of_book_position,
        )

    @mcp.tool(
        name="ledger_get_entry",
        title="Get an everyday bookkeeping entry",
        description=(
            "Use this when the user wants one known transaction in everyday "
            "bookkeeping terms, including its asset-unit amount, display accounts, "
            "category path, and reversal relationships. Use ledger_get_transaction "
            "only when raw journal postings are specifically needed for an audit."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_get_entry(
        book_id: UUID,
        transaction_id: UUID,
        as_of_book_position: Annotated[int | None, Field(ge=0)] = None,
    ) -> EverydayEntryReceiptResponse:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                item = get_everyday_entry(
                    session,
                    book_id,
                    transaction_id,
                    as_of_book_position=as_of_book_position,
                )
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return EverydayEntryReceiptResponse.from_view(item)

    @mcp.tool(
        name="ledger_list_categories",
        title="List Book categories",
        description=(
            "Use this when you need the current category catalog for a Book, "
            "including complete human-readable paths, expense or income usage, "
            "parent relationships, and stable category IDs. Call this before "
            "creating a category or preparing a categorized entry."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        meta=TOOL_META,
    )
    def ledger_list_categories(book_id: UUID) -> CategoryPage:
        token = require_access_token()
        with session_factory() as session:
            require_book_access(session, token, book_id)
            try:
                values = _read_category_details_from_session(session, book_id)
            except (LookupError, ValueError) as error:
                raise ToolError(str(error)) from error
        return CategoryPage(items=values)

    @mcp.tool(
        name="ledger_reverse_transaction",
        title="Reverse an incorrect transaction",
        description=(
            "Use this when the user has explicitly confirmed that a posted "
            "transaction is a duplicate, incorrect, imported incorrectly, or "
            "reversed by its provider. This append-only operation preserves the "
            "original audit trail and posts an opposite transaction; it never "
            "deletes or edits history. Inspect the entry first and confirm the "
            "target, reason, and effective time. A reversal itself cannot be "
            "reversed. Reuse request_id only for an exact retry."
        ),
        annotations=DESTRUCTIVE_WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_reverse_transaction(
        book_id: UUID,
        request_id: UUID,
        transaction_id: UUID,
        reason_code: Literal[
            "user_correction",
            "duplicate",
            "import_correction",
            "provider_reversal",
        ],
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, reversal_transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_reverse_transaction",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_reverse_transaction(
                ReverseTransactionCommand(
                    book_id=book_id,
                    command_id=command_id,
                    reversal_transaction_id=reversal_transaction_id,
                    reverses_transaction_id=transaction_id,
                    expected_stream_version=0,
                    reason_code=ReversalReasonCode(reason_code),
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_reverse_transaction:{request_id}",
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
            reversal_transaction_id,
            outcome,
        )

    @mcp.tool(
        name="ledger_set_transaction_category",
        title="Change a transaction category",
        description=(
            "Use this when the user has explicitly confirmed that an existing "
            "expense, income, or refund belongs to one different category. Inspect "
            "the entry and list categories first. This changes only the reporting "
            "category at the transaction's full amount; it never changes accounts, "
            "money, or journal postings and never asks for an internal account. "
            "Reuse request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_set_transaction_category(
        book_id: UUID,
        request_id: UUID,
        transaction_id: UUID,
        category_id: UUID,
        effective_at: AwareDatetime,
    ) -> TransactionCategoryWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        with session_factory() as session:
            entry, revision, current_lines = _transaction_category_state(
                session,
                book_id,
                transaction_id,
            )
            category = next(
                (
                    value
                    for value in _read_category_details_from_session(session, book_id)
                    if value.category_id == category_id
                ),
                None,
            )
        if category is None or category.status != "active":
            raise ToolError(
                "category_id must identify an active category in the requested Book"
            )
        asset_code, units, line_kind = _entry_category_allocation(entry)
        if (
            line_kind is ReportingLineKind.EXPENSE
            and category.usage_kind not in {"expense", "both"}
        ) or (
            line_kind is ReportingLineKind.INCOME
            and category.usage_kind not in {"income", "both"}
        ):
            raise ToolError(
                f"category usage_kind {category.usage_kind!r} does not support "
                f"{line_kind.value} entries"
            )
        if _category_lines_match(
            current_lines,
            category=category,
            asset_code=asset_code,
            units=units,
            line_kind=line_kind,
        ):
            return TransactionCategoryWriteResponse(
                request_id=request_id,
                transaction_id=transaction_id,
                replayed=True,
                category=category,
                classification_revision=revision,
                first_book_position=None,
                last_book_position=None,
                verification_status="verified",
                retry_guidance="No retry is needed; the requested category is current.",
            )
        command_id, _ = _write_ids(
            token.subject or "",
            book_id,
            "ledger_set_transaction_category",
            request_id,
        )
        line_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_set_transaction_category_line",
            request_id,
        )
        line_version_id = _catalog_entity_id(
            token.subject or "",
            book_id,
            "ledger_set_transaction_category_line_version",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_assign_reporting_lines(
                AssignReportingLinesCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_revision=revision,
                    lines=(
                        ReportingLineInput(
                            line_id=line_id,
                            line_version_id=line_version_id,
                            catalog_id=category.current_version_id,
                            asset_code=asset_code,
                            units=units,
                            line_kind=line_kind,
                            dimension=ReportingDimension.CATEGORY,
                            dimension_id=category.category_id,
                        ),
                    ),
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_set_transaction_category:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        return _transaction_category_write_response(
            dependencies,
            request_id=request_id,
            book_id=book_id,
            transaction_id=transaction_id,
            outcome=outcome,
            category=category,
        )

    @mcp.tool(
        name="ledger_clear_transaction_category",
        title="Clear a transaction category",
        description=(
            "Use this when the user has explicitly confirmed that an existing "
            "expense, income, or refund should be left uncategorized. Inspect the "
            "entry first. This clears only the reporting category and preserves "
            "the amount, accounts, journal postings, and audit history. Reuse "
            "request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_clear_transaction_category(
        book_id: UUID,
        request_id: UUID,
        transaction_id: UUID,
        effective_at: AwareDatetime,
    ) -> TransactionCategoryWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        with session_factory() as session:
            _, revision, current_lines = _transaction_category_state(
                session,
                book_id,
                transaction_id,
            )
        if not current_lines:
            return TransactionCategoryWriteResponse(
                request_id=request_id,
                transaction_id=transaction_id,
                replayed=True,
                category=None,
                classification_revision=revision,
                first_book_position=None,
                last_book_position=None,
                verification_status="verified",
                retry_guidance="No retry is needed; the entry is already uncategorized.",
            )
        command_id, _ = _write_ids(
            token.subject or "",
            book_id,
            "ledger_clear_transaction_category",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_clear_reporting_lines(
                ClearReportingLinesCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_revision=revision,
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_clear_transaction_category:{request_id}",
                actor=CommandActor(token.subject or ""),
                uow_factory=dependencies.uow_factory,
                ledger_committer=dependencies.ledger_committer,
            ),
            request_id=request_id,
        )
        return _transaction_category_write_response(
            dependencies,
            request_id=request_id,
            book_id=book_id,
            transaction_id=transaction_id,
            outcome=outcome,
            category=None,
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
        name="ledger_record_fx",
        title="Record an asset exchange",
        description=(
            "Use this when the user has explicitly confirmed an exchange between "
            "two different assets, including both exact decimal amounts and the "
            "effective time. Amounts are in each asset's major unit, never integer "
            "ledger units. Use the standard user accounts for source_account_id and "
            "target_account_id, and the matching system-managed fx_trading accounts "
            "for the trading account IDs. Never infer or round either amount from an "
            "exchange rate. Reuse request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_record_fx(
        book_id: UUID,
        request_id: UUID,
        source_account_id: UUID,
        source_trading_account_id: UUID,
        source_asset_code: AssetCode,
        source_amount: PlainDecimal,
        target_trading_account_id: UUID,
        target_account_id: UUID,
        target_asset_code: AssetCode,
        target_amount: PlainDecimal,
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_record_fx",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_record_fx(
                RecordFxCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_stream_version=0,
                    source_account_id=source_account_id,
                    source_trading_account_id=source_trading_account_id,
                    source_asset_code=source_asset_code,
                    source_amount=source_amount,
                    target_trading_account_id=target_trading_account_id,
                    target_account_id=target_account_id,
                    target_asset_code=target_asset_code,
                    target_amount=target_amount,
                    effective_at=effective_at,
                ),
                raw_key=f"mcp:ledger_record_fx:{request_id}",
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
        name="ledger_record_fx_credit_card_payment",
        title="Record a cross-asset credit-card payment",
        description=(
            "Use this when the user has explicitly confirmed one cross-asset "
            "credit-card payment, including the exact source principal, exact card "
            "payment amount, source-asset fee, fee category version, and effective "
            "time. source_amount excludes fee_amount; the source account is credited "
            "for their sum. Use the standard source asset account, the credit-card "
            "liability as target_account_id, and both matching system-managed "
            "fx_trading accounts. Never infer or round either amount from a rate. "
            "Reuse request_id only for an exact retry."
        ),
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_TOOL_META,
    )
    def ledger_record_fx_credit_card_payment(
        book_id: UUID,
        request_id: UUID,
        source_account_id: UUID,
        source_trading_account_id: UUID,
        source_asset_code: AssetCode,
        source_amount: PlainDecimal,
        target_trading_account_id: UUID,
        target_account_id: UUID,
        target_asset_code: AssetCode,
        target_amount: PlainDecimal,
        fee_amount: PlainDecimal,
        fee_category_id: UUID,
        fee_category_version_id: UUID,
        effective_at: AwareDatetime,
    ) -> LedgerWriteResponse:
        token = _require_write_book(dependencies, book_id, request_id)
        command_id, transaction_id = _write_ids(
            token.subject or "",
            book_id,
            "ledger_record_fx_credit_card_payment",
            request_id,
        )
        outcome = _call_write(
            lambda: execute_record_fx_credit_card_payment(
                RecordFxCreditCardPaymentCommand(
                    book_id=book_id,
                    command_id=command_id,
                    transaction_id=transaction_id,
                    expected_stream_version=0,
                    source_account_id=source_account_id,
                    source_trading_account_id=source_trading_account_id,
                    source_asset_code=source_asset_code,
                    source_amount=source_amount,
                    target_trading_account_id=target_trading_account_id,
                    target_account_id=target_account_id,
                    target_asset_code=target_asset_code,
                    target_amount=target_amount,
                    fee_amount=fee_amount,
                    fee_category_id=fee_category_id,
                    fee_category_version_id=fee_category_version_id,
                    effective_at=effective_at,
                ),
                raw_key=(
                    "mcp:ledger_record_fx_credit_card_payment:"
                    f"{request_id}"
                ),
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


def _read_created_category(
    dependencies: RuntimeDependencies,
    book_id: UUID,
    category_id: UUID,
) -> CategoryDetailResponse | None:
    with dependencies.session_factory() as session:
        values = _read_category_details_from_session(session, book_id)
    return next(
        (value for value in values if value.category_id == category_id),
        None,
    )


def _read_category_details_from_session(
    session: Session,
    book_id: UUID,
) -> tuple[CategoryDetailResponse, ...]:
    rows = session.execute(
        select(CategoryRecord, CategoryVersionRecord)
        .join(
            CategoryVersionRecord,
            (
                (CategoryVersionRecord.book_id == CategoryRecord.book_id)
                & (CategoryVersionRecord.category_id == CategoryRecord.category_id)
                & (
                    CategoryVersionRecord.category_version_id
                    == CategoryRecord.current_version_id
                )
            ),
        )
        .where(CategoryRecord.book_id == book_id)
        .order_by(CategoryRecord.current_name, CategoryRecord.category_id)
    ).all()
    records = {category.category_id: category for category, _ in rows}
    versions = {category.category_id: version for category, version in rows}

    def path_for(category_id: UUID) -> tuple[str, ...]:
        parts: list[str] = []
        visited: set[UUID] = set()
        current_id: UUID | None = category_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError("category parent relationship contains a cycle")
            visited.add(current_id)
            current = records.get(current_id)
            if current is None:
                raise ValueError("category parent is unavailable in requested Book")
            parts.append(current.current_name)
            current_id = current.parent_category_id
        return tuple(reversed(parts))

    values = (
        CategoryDetailResponse(
            category_id=category.category_id,
            parent_category_id=category.parent_category_id,
            current_version_id=category.current_version_id,
            current_name=category.current_name,
            status=category.status,
            path=path_for(category.category_id),
            usage_kind=versions[category.category_id].usage_kind,
        )
        for category, _ in rows
    )
    return tuple(sorted(values, key=lambda value: (value.path, value.category_id)))


def _transaction_category_state(
    session: Session,
    book_id: UUID,
    transaction_id: UUID,
) -> tuple[EverydayEntryView, int, tuple[ReportingLine, ...]]:
    head = session.get(BookEventHeadRecord, book_id)
    if head is None:
        raise ToolError("Book not found")
    try:
        entry = get_everyday_entry(session, book_id, transaction_id)
    except (LookupError, ValueError) as error:
        raise ToolError(str(error)) from error
    stream_head = session.get(
        EventStreamHeadRecord,
        (book_id, "reporting_lines", transaction_id),
    )
    revision = 0 if stream_head is None else stream_head.last_version
    lines = tuple(
        line
        for line in list_current_reporting_lines(
            session,
            book_id,
            as_of_book_position=head.last_position,
        )
        if line.transaction_id == transaction_id
    )
    return entry, revision, lines


def _entry_category_allocation(
    entry: EverydayEntryView,
) -> tuple[str, str, ReportingLineKind]:
    if entry.kind == "income":
        line_kind = ReportingLineKind.INCOME
    elif entry.kind in {"expense", "refund"}:
        line_kind = ReportingLineKind.EXPENSE
    else:
        raise ToolError("only expense, income, and refund entries can have a category")
    if entry.amount is None:
        raise ToolError("entry amount is unavailable for category assignment")
    scaled = Decimal(entry.amount.value) * (Decimal(10) ** entry.amount.scale)
    integral = scaled.to_integral_value()
    if scaled != integral or integral <= 0:
        raise ToolError("entry amount cannot be represented in exact ledger units")
    return entry.amount.asset_code, str(int(integral)), line_kind


def _category_lines_match(
    lines: tuple[ReportingLine, ...],
    *,
    category: CategoryDetailResponse,
    asset_code: str,
    units: str,
    line_kind: ReportingLineKind,
) -> bool:
    if len(lines) != 1:
        return False
    line = lines[0]
    return (
        line.catalog_id == category.current_version_id
        and line.asset_code == asset_code
        and line.units == int(units)
        and line.line_kind == line_kind.value
        and line.dimension == ReportingDimension.CATEGORY.value
        and line.dimension_id == category.category_id
    )


def _transaction_category_write_response(
    dependencies: RuntimeDependencies,
    *,
    request_id: UUID,
    book_id: UUID,
    transaction_id: UUID,
    outcome: CommandOutcome,
    category: CategoryDetailResponse | None,
) -> TransactionCategoryWriteResponse:
    first_position = outcome.result.first_book_position
    last_position = outcome.result.last_book_position
    if first_position is None or last_position is None:
        raise ToolError("The category write completed without a Book position.")
    body = outcome.result.body
    revision_value = (
        body.get("classification_revision") if isinstance(body, dict) else None
    )
    if type(revision_value) is not int or revision_value <= 0:
        raise ToolError("The category write completed without a valid revision.")
    verification_status: Literal["verified", "pending"] = "pending"
    try:
        with dependencies.session_factory() as session:
            _, stored_revision, lines = _transaction_category_state(
                session,
                book_id,
                transaction_id,
            )
        if stored_revision == revision_value:
            if category is None and not lines:
                verification_status = "verified"
            elif category is not None and any(
                line.dimension_id == category.category_id for line in lines
            ):
                verification_status = "verified"
    except Exception:
        _log_write_boundary("mcp_category_write_readback_pending", request_id)
    return TransactionCategoryWriteResponse(
        request_id=request_id,
        transaction_id=transaction_id,
        replayed=outcome.replayed,
        category=category,
        classification_revision=revision_value,
        first_book_position=first_position,
        last_book_position=last_position,
        verification_status=verification_status,
        retry_guidance=(
            "If verification is pending, retry only with request_id "
            f"{request_id} and the exact same arguments."
        ),
    )


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
    except (
        AccountBalanceNonzero,
        AccountBalanceProjectionMismatch,
        IdempotencyConflict,
        LookupError,
        PermissionError,
        ValueError,
    ) as error:
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
    "CategoryCatalogWriteResponse",
    "CategoryDetailResponse",
    "CategoryPage",
    "DESTRUCTIVE_WRITE_ANNOTATIONS",
    "EverydayEntryPageResponse",
    "LedgerWriteResponse",
    "READ_ONLY_ANNOTATIONS",
    "SECURITY_SCHEMES",
    "TransactionCategoryWriteResponse",
    "WRITE_ANNOTATIONS",
    "WRITE_SECURITY_SCHEMES",
    "register_ledger_tools",
]
