from __future__ import annotations

from typing import Annotated, Literal, Protocol
from uuid import UUID

from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, Field

from ..application.entries.contracts import (
    AccountRef,
    AdjustmentEntryInput,
    BalanceInput,
    CategoryAllocationInput,
    CategoryRef,
    CreditCardPaymentEntryInput,
    CommitEntryInput,
    CommittedEntry,
    EntryNarrativeInput,
    EverydayEntryInput,
    EverydayEntryService,
    ExpenseEntryInput,
    IncomeEntryInput,
    MoneyInput,
    OpaqueToken,
    PreparedEntry,
    RefundEntryInput,
    TransferEntryInput,
)
from ..application.payment_instruments.contracts import PaymentInstrumentRef
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
    "track_anywhere/mode": "entry_prepare",
    "track_anywhere/requires_write": True,
}
ENTRY_COMMIT_TOOL_META = {
    "securitySchemes": ENTRY_PREPARE_SECURITY_SCHEMES,
    "track_anywhere/mode": "entry_commit",
    "track_anywhere/requires_write": True,
}
ENTRY_PREPARE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
ENTRY_COMMIT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)

_AMOUNT_DESCRIPTION = (
    "Amounts are exact business facts, never ledger units. denomination defaults "
    "to asset_unit: for a scale-2 currency, bare `660` with asset_unit means "
    "660.00, never 6.60. Use minor_unit only when the source explicitly states "
    "minor units."
)
_PREPARE_DESCRIPTION = (
    "This tool only prepares and previews an entry; it never posts a financial "
    "ledger event by itself. When status is ready it returns an opaque commit "
    "token. Show the preview and warnings to the user, obtain explicit "
    "confirmation, then pass the unchanged intent ID and token to "
    "ledger_commit_entry."
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


class McpPreparedEntry(PreparedEntry):
    """Agent-facing prepare result with a short-lived opaque commit capability."""

    mode: Literal["prepare"] = "prepare"

    @classmethod
    def from_prepared(cls, prepared: PreparedEntry) -> McpPreparedEntry:
        return cls.model_validate(
            {"mode": "prepare", **prepared.model_dump(mode="python")}
        )


def register_entry_tools(
    mcp: FastMCP,
    service_provider: EntryServiceProvider,
) -> None:
    @mcp.tool(
        name="ledger_prepare_expense",
        title="Preview an expense",
        description=(
            "Use this when the user wants to record a purchase or other expense. "
            f"{_AMOUNT_DESCRIPTION} Supply exactly one ordinary source account or "
            "payment instrument. A payment instrument resolves its configured "
            "asset/prepaid funding or statement liability account automatically. "
            "For a named physical or virtual card, list configured payment "
            "instruments and pass the unique matching instrument; do not select or "
            "create an account for that purchase. "
            "Supply either one category or exact category allocations. "
            f"{_PREPARE_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_expense(
        book_id: UUID,
        amount: MoneyInput,
        occurred_at: AwareDatetime,
        source_account: AccountRef | None = None,
        payment_instrument: PaymentInstrumentRef | None = None,
        category: CategoryRef | None = None,
        category_allocations: Annotated[
            tuple[CategoryAllocationInput, ...],
            Field(max_length=64),
        ] = (),
        narrative: EntryNarrativeInput | None = None,
    ) -> McpPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=ExpenseEntryInput(
                amount=amount,
                source_account=source_account,
                payment_instrument=payment_instrument,
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
            f"{_PREPARE_DESCRIPTION}"
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
    ) -> McpPreparedEntry:
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
            f"category. {_PREPARE_DESCRIPTION}"
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
    ) -> McpPreparedEntry:
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
            f"account. {_AMOUNT_DESCRIPTION} Supply the exact liability account "
            "or a statement payment instrument; the latter resolves its bound "
            "liability automatically. A card payment is a balance-sheet "
            "transfer and never takes an expense category. "
            f"{_PREPARE_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_credit_card_payment(
        book_id: UUID,
        amount: MoneyInput,
        funding_account: AccountRef,
        occurred_at: AwareDatetime,
        card_account: AccountRef | None = None,
        payment_instrument: PaymentInstrumentRef | None = None,
        narrative: EntryNarrativeInput | None = None,
    ) -> McpPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=CreditCardPaymentEntryInput(
                amount=amount,
                funding_account=funding_account,
                card_account=card_account,
                payment_instrument=payment_instrument,
                occurred_at=occurred_at,
                narrative=narrative,
            ),
            service_provider=service_provider,
        )

    @mcp.tool(
        name="ledger_prepare_fx_credit_card_payment",
        title="Preview a cross-asset credit-card payment",
        description=(
            "Use this when the user pays a credit-card liability in one asset "
            "from an account in another asset. "
            f"{_AMOUNT_DESCRIPTION} target_amount is the exact card payment; "
            "source_amount is the exchanged principal and excludes fee_amount. "
            "The funding account is charged source_amount plus fee_amount. "
            "Supply an expense-eligible fee category. Never infer either amount "
            "or an exchange rate. "
            f"{_PREPARE_DESCRIPTION}"
        ),
        annotations=ENTRY_PREPARE_ANNOTATIONS,
        meta=ENTRY_PREPARE_TOOL_META,
    )
    def ledger_prepare_fx_credit_card_payment(
        book_id: UUID,
        target_amount: MoneyInput,
        source_amount: MoneyInput,
        fee_amount: MoneyInput,
        funding_account: AccountRef,
        fee_category: CategoryRef,
        occurred_at: AwareDatetime,
        card_account: AccountRef | None = None,
        payment_instrument: PaymentInstrumentRef | None = None,
        narrative: EntryNarrativeInput | None = None,
    ) -> McpPreparedEntry:
        return _prepare(
            book_id=book_id,
            entry=CreditCardPaymentEntryInput(
                amount=target_amount,
                source_amount=source_amount,
                fee_amount=fee_amount,
                funding_account=funding_account,
                fee_category=fee_category,
                card_account=card_account,
                payment_instrument=payment_instrument,
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
            f"category allocations. {_PREPARE_DESCRIPTION}"
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
    ) -> McpPreparedEntry:
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
            f"{_PREPARE_DESCRIPTION}"
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
    ) -> McpPreparedEntry:
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

    @mcp.tool(
        name="ledger_commit_entry",
        title="Commit a prepared entry",
        description=(
            "Use this only after ledger_prepare_* returned status ready and the "
            "user explicitly confirmed its preview and warnings. Pass the exact "
            "Book ID, intent ID, and opaque commit token returned by prepare. "
            "Generate one fresh request_id UUID for the first attempt and reuse "
            "that same request_id for every retry. Never prepare a replacement "
            "merely because a commit response timed out. This tool posts the "
            "already prepared financial ledger event and does not accept amount, "
            "account, category, time, or narrative overrides."
        ),
        annotations=ENTRY_COMMIT_ANNOTATIONS,
        meta=ENTRY_COMMIT_TOOL_META,
    )
    def ledger_commit_entry(
        book_id: UUID,
        intent_id: UUID,
        commit_token: OpaqueToken,
        request_id: UUID,
    ) -> CommittedEntry:
        return _commit(
            book_id=book_id,
            command=CommitEntryInput(
                intent_id=intent_id,
                commit_token=commit_token,
                request_id=request_id,
            ),
            service_provider=service_provider,
        )


def _prepare(
    *,
    book_id: UUID,
    entry: EverydayEntryInput,
    service_provider: EntryServiceProvider,
) -> McpPreparedEntry:
    token = require_write_access_token()
    _require_token_book(token, book_id)
    try:
        service = service_provider(token, book_id)
        return McpPreparedEntry.from_prepared(
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


def _commit(
    *,
    book_id: UUID,
    command: CommitEntryInput,
    service_provider: EntryServiceProvider,
) -> CommittedEntry:
    token = require_write_access_token()
    _require_token_book(token, book_id)
    try:
        service = service_provider(token, book_id)
        return service.commit(book_id=book_id, command=command)
    except ToolError:
        raise
    except EntryGatewayError as error:
        field = "" if error.field is None else f" field={error.field}"
        raise ToolError(f"{error.code.value}:{field} {error}") from None
    except Exception:
        # A transport failure may happen after an idempotent commit succeeded.
        raise ToolError(
            "Entry commit failed unexpectedly. Retry with the same request_id "
            "before preparing a replacement."
        ) from None


def _require_token_book(token: AccessToken, book_id: UUID) -> None:
    restricted_book_id = (token.claims or {}).get("book_id")
    if restricted_book_id is not None and str(restricted_book_id) != str(book_id):
        raise ToolError("This connection is restricted to a different Book.")


# Compatibility import for integrations that registered the original shadow tools.
register_entry_prepare_tools = register_entry_tools


__all__ = [
    "ENTRY_COMMIT_ANNOTATIONS",
    "ENTRY_COMMIT_TOOL_META",
    "ENTRY_PREPARE_ANNOTATIONS",
    "ENTRY_PREPARE_SECURITY_SCHEMES",
    "ENTRY_PREPARE_TOOL_META",
    "EntryServiceProvider",
    "McpPreparedEntry",
    "create_runtime_entry_service_provider",
    "register_entry_tools",
    "register_entry_prepare_tools",
]
