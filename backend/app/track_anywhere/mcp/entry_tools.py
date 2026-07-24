from __future__ import annotations

from typing import Annotated, Literal, Protocol
from uuid import UUID

from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..application.entries.contracts import (
    AccountRef,
    AdjustmentEntryInput,
    BalanceInput,
    CategoryAllocationInput,
    CategoryRef,
    CreditCardPaymentEntryInput,
    Clarification,
    EntryNarrativeInput,
    EntryPreview,
    EntryWarning,
    EverydayEntryInput,
    EverydayEntryService,
    ExpenseEntryInput,
    IncomeEntryInput,
    MoneyInput,
    PreparedEntry,
    PreparedEntryStatus,
    RefundEntryInput,
    ResolvedEntryReferences,
    TransferEntryInput,
)
from ..application.entries.errors import EntryGatewayError
from ..application.entries.service import RequestScopedEverydayEntryService
from ..application.idempotency import CommandActor
from ..application.privacy.service import ProtectedContentService
from ..api.dependencies import RuntimeDependencies
from ..infrastructure.db.repositories.privacy import ProtectedContentRepository
from .auth import require_book_write_access, require_write_access_token


ENTRY_PREPARE_SECURITY_SCHEMES = [
    {"type": "oauth2", "scopes": ["ledger:read", "ledger:write"]}
]
ENTRY_PREPARE_TOOL_META = {
    "securitySchemes": ENTRY_PREPARE_SECURITY_SCHEMES,
    "track_anywhere/mode": "shadow_prepare_only",
}
ENTRY_PREPARE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

_AMOUNT_DESCRIPTION = (
    "Amounts are exact business facts, never ledger units. denomination defaults "
    "to asset_unit: for a scale-2 currency, bare `660` with asset_unit means "
    "660.00, never 6.60. Use minor_unit only when the source explicitly states "
    "minor units."
)
_SHADOW_DESCRIPTION = (
    "This Shadow Mode tool only prepares and previews an entry. It cannot commit "
    "or post a financial ledger event."
)


class EntryServiceProvider(Protocol):
    """Build an authenticated, actor-scoped service for one requested Book."""

    def __call__(
        self,
        token: AccessToken,
        book_id: UUID,
    ) -> EverydayEntryService: ...


def create_runtime_entry_service_provider(
    dependencies: RuntimeDependencies,
) -> EntryServiceProvider:
    """Compose the shared entry facade lazily for an authenticated MCP call."""

    def provide(
        token: AccessToken,
        book_id: UUID,
    ) -> RequestScopedEverydayEntryService:
        subject = token.subject
        if subject is None:
            raise ToolError(
                "Authentication is required. Reconnect the Track Anywhere app."
            )
        with dependencies.session_factory() as session:
            require_book_write_access(session, token, book_id)

        cipher = dependencies.protected_content_cipher
        protected_service = (
            None
            if cipher is None
            else ProtectedContentService(
                cipher=cipher,
                repository=ProtectedContentRepository(),
            )
        )
        return RequestScopedEverydayEntryService(
            actor=CommandActor(subject),
            uow_factory=dependencies.uow_factory,
            ledger_committer=dependencies.ledger_committer,
            protected_content_service=protected_service,
            duplicate_key_provider=dependencies.duplicate_detection_key_provider,
        )

    return provide


class ShadowPreparedEntry(BaseModel):
    """Prepare result that cannot be used to commit through another surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["shadow_preview"] = "shadow_preview"
    intent_id: UUID
    status: PreparedEntryStatus
    expires_at: AwareDatetime
    preview: EntryPreview
    resolved: ResolvedEntryReferences
    warnings: tuple[EntryWarning, ...] = ()
    clarifications: tuple[Clarification, ...] = ()

    @classmethod
    def from_prepared(cls, prepared: PreparedEntry) -> ShadowPreparedEntry:
        return cls(
            intent_id=prepared.intent_id,
            status=prepared.status,
            expires_at=prepared.expires_at,
            preview=prepared.preview,
            resolved=prepared.resolved,
            warnings=prepared.warnings,
            clarifications=prepared.clarifications,
        )


def register_entry_prepare_tools(
    mcp: FastMCP,
    service_provider: EntryServiceProvider,
) -> None:
    @mcp.tool(
        name="ledger_prepare_expense",
        title="Preview an expense",
        description=(
            "Use this when the user wants to record a purchase or other expense. "
            f"{_AMOUNT_DESCRIPTION} Supply a funding account and either one "
            "category or exact category allocations. "
            f"{_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_expense(
        book_id: UUID,
        amount: MoneyInput,
        source_account: AccountRef,
        occurred_at: AwareDatetime,
        category: CategoryRef | None = None,
        category_allocations: Annotated[
            tuple[CategoryAllocationInput, ...],
            Field(max_length=64),
        ] = (),
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=ExpenseEntryInput(
                amount=amount,
                source_account=source_account,
                occurred_at=occurred_at,
                category=category,
                category_allocations=category_allocations,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_income",
        title="Preview income",
        description=(
            "Use this when the user wants to record salary, reimbursement, or "
            f"other income. {_AMOUNT_DESCRIPTION} Supply the receiving account "
            "and either one income category or exact category allocations. "
            f"{_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_income(
        book_id: UUID,
        amount: MoneyInput,
        destination_account: AccountRef,
        occurred_at: AwareDatetime,
        category: CategoryRef | None = None,
        category_allocations: Annotated[
            tuple[CategoryAllocationInput, ...],
            Field(max_length=64),
        ] = (),
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=IncomeEntryInput(
                amount=amount,
                destination_account=destination_account,
                occurred_at=occurred_at,
                category=category,
                category_allocations=category_allocations,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_transfer",
        title="Preview a transfer",
        description=(
            "Use this when the user moves money between two asset accounts. "
            f"{_AMOUNT_DESCRIPTION} Transfers never take an expense or income "
            f"category. {_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_transfer(
        book_id: UUID,
        amount: MoneyInput,
        source_account: AccountRef,
        destination_account: AccountRef,
        occurred_at: AwareDatetime,
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=TransferEntryInput(
                amount=amount,
                source_account=source_account,
                destination_account=destination_account,
                occurred_at=occurred_at,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_credit_card_payment",
        title="Preview a credit-card payment",
        description=(
            "Use this when the user pays a credit-card liability from an asset "
            f"account. {_AMOUNT_DESCRIPTION} A card payment is a balance-sheet "
            "transfer and never takes an expense category. "
            f"{_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_credit_card_payment(
        book_id: UUID,
        amount: MoneyInput,
        funding_account: AccountRef,
        card_account: AccountRef,
        occurred_at: AwareDatetime,
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=CreditCardPaymentEntryInput(
                amount=amount,
                funding_account=funding_account,
                card_account=card_account,
                occurred_at=occurred_at,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_refund",
        title="Preview a refund",
        description=(
            "Use this when money is returned for an existing transaction. Supply "
            "the original transaction ID; omit amount only for a full refund. "
            f"{_AMOUNT_DESCRIPTION} Split partial refunds may require exact "
            f"category allocations. {_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_refund(
        book_id: UUID,
        original_transaction_id: UUID,
        occurred_at: AwareDatetime,
        amount: MoneyInput | None = None,
        category_allocations: Annotated[
            tuple[CategoryAllocationInput, ...],
            Field(max_length=64),
        ] = (),
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=RefundEntryInput(
                original_transaction_id=original_transaction_id,
                occurred_at=occurred_at,
                amount=amount,
                category_allocations=category_allocations,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_adjustment",
        title="Preview a balance adjustment",
        description=(
            "Use this when the user supplies the observed balance of one asset "
            "account for reconciliation. actual_balance is an exact business "
            "amount and never ledger units; for a scale-2 currency, bare `660` "
            "with asset_unit means 660.00, never 6.60. "
            f"{_SHADOW_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_adjustment(
        book_id: UUID,
        account: AccountRef,
        actual_balance: BalanceInput,
        occurred_at: AwareDatetime,
        narrative: EntryNarrativeInput | None = None,
    ) -> ShadowPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=AdjustmentEntryInput(
                account=account,
                actual_balance=actual_balance,
                occurred_at=occurred_at,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )


def _prepare(
    *,
    book_id: UUID,
    entry: EverydayEntryInput,
    service_provider: EntryServiceProvider,
) -> ShadowPreparedEntry:
    token = require_write_access_token()
    restricted_book_id = (token.claims or {}).get("book_id")
    if restricted_book_id is not None and str(restricted_book_id) != str(book_id):
        raise ToolError("This connection is restricted to a different Book.")
    try:
        service = service_provider(token, book_id)
        return ShadowPreparedEntry.from_prepared(
            service.prepare(book_id=book_id, entry=entry)
        )
    except ToolError:
        raise
    except EntryGatewayError as error:
        field = "" if error.field is None else f" field={error.field}"
        raise ToolError(f"{error.code.value}:{field} {error}") from None
    except Exception:
        # Tool responses must not echo private narrative from unexpected failures.
        raise ToolError(
            "Entry preparation failed unexpectedly. No ledger entry was committed."
        ) from None


__all__ = [
    "ENTRY_PREPARE_ANNOTATIONS",
    "ENTRY_PREPARE_SECURITY_SCHEMES",
    "ENTRY_PREPARE_TOOL_META",
    "EntryServiceProvider",
    "ShadowPreparedEntry",
    "create_runtime_entry_service_provider",
    "register_entry_prepare_tools",
]
