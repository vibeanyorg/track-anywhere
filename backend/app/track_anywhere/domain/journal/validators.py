from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..money import AmountOutOfRange, ScaledUnits
from .commands import PostTransaction
from .models import (
    AccountCatalogSnapshot,
    AccountSnapshot,
    AccountSystemRole,
    InvalidAccountCatalog,
    JournalError,
    PostingDraft,
    PostingSide,
    TransactionKind,
)


class InvalidJournalCommand(JournalError):
    """Raised when a transaction command has an invalid runtime shape."""


class TooFewPostings(JournalError):
    def __init__(self) -> None:
        super().__init__("a journal transaction requires at least two postings")


class DuplicatePostingId(JournalError):
    def __init__(self, posting_id: str) -> None:
        super().__init__(f"duplicate posting id: {posting_id}")


class DuplicatePostingPosition(JournalError):
    def __init__(self, position: int) -> None:
        super().__init__(f"duplicate posting position: {position}")


class PostingValidationError(JournalError):
    """Raised when a posting has an invalid runtime shape."""


class InvalidPostingUnits(PostingValidationError):
    def __init__(self, posting_id: str) -> None:
        super().__init__(
            f"posting {posting_id} units must be a positive integer "
            "with at most 38 digits"
        )


class UnknownAccount(JournalError):
    def __init__(self, account_id: str) -> None:
        super().__init__(f"unknown account: {account_id}")


class CrossBookAccount(JournalError):
    def __init__(self, account_id: str, *, expected_book_id: str) -> None:
        super().__init__(
            f"account {account_id} does not belong to book {expected_book_id}"
        )


class PostingAssetMismatch(JournalError):
    def __init__(
        self,
        account_id: str,
        *,
        posting_asset: str,
        account_asset: str,
    ) -> None:
        super().__init__(
            f"posting asset {posting_asset} does not match account "
            f"{account_id} asset {account_asset}"
        )


class UnbalancedAsset(JournalError):
    def __init__(self, imbalances: dict[str, int]) -> None:
        self.imbalances = tuple(sorted(imbalances.items()))
        self.assets = tuple(asset for asset, _ in self.imbalances)
        detail = ", ".join(
            f"{asset}={net_units}" for asset, net_units in self.imbalances
        )
        super().__init__(f"journal is unbalanced for assets: {detail}")


class InvalidFxTransaction(JournalError):
    """Raised when an FX journal is not an explicit multi-asset exchange."""


def _require_nonempty_string(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise InvalidJournalCommand(f"{label} must be a non-empty string")
    return value


def _validate_command_shape(command: object) -> PostTransaction:
    if type(command) is not PostTransaction:
        raise InvalidJournalCommand("command must be a PostTransaction")

    _require_nonempty_string(command.transaction_id, label="transaction id")
    _require_nonempty_string(command.book_id, label="book id")
    if type(command.kind) is not TransactionKind:
        raise InvalidJournalCommand("transaction kind must be a TransactionKind")
    if type(command.postings) is not tuple:
        raise InvalidJournalCommand("postings must be an immutable tuple")
    if len(command.postings) < 2:
        raise TooFewPostings()
    return command


def _validate_posting_shape(posting: object) -> PostingDraft:
    if type(posting) is not PostingDraft:
        raise PostingValidationError("each posting must be a PostingDraft")
    if type(posting.posting_id) is not str or not posting.posting_id:
        raise PostingValidationError("posting id must be a non-empty string")
    if type(posting.position) is not int or posting.position < 0:
        raise PostingValidationError("posting position must be a non-negative integer")
    return posting


def _reject_duplicate_posting_identity(postings: Sequence[PostingDraft]) -> None:
    posting_ids: set[str] = set()
    positions: set[int] = set()
    for posting in postings:
        if posting.posting_id in posting_ids:
            raise DuplicatePostingId(posting.posting_id)
        posting_ids.add(posting.posting_id)

        if posting.position in positions:
            raise DuplicatePostingPosition(posting.position)
        positions.add(posting.position)


def _validate_posting_values(posting: PostingDraft) -> None:
    if type(posting.account_id) is not str or not posting.account_id:
        raise PostingValidationError("posting account id must be a non-empty string")
    if type(posting.asset_code) is not str or not posting.asset_code:
        raise PostingValidationError("posting asset code must be a non-empty string")
    if type(posting.side) is not PostingSide:
        raise PostingValidationError("posting side must be a PostingSide")
    try:
        ScaledUnits(units=posting.units, scale=0)
    except AmountOutOfRange:
        raise InvalidPostingUnits(posting.posting_id) from None


def _index_catalog(
    catalog: object,
) -> dict[tuple[str, str], AccountSnapshot]:
    if type(catalog) is not AccountCatalogSnapshot:
        raise InvalidAccountCatalog("catalog must be an AccountCatalogSnapshot")
    return catalog._index_by_identity()


class JournalValidator:
    @staticmethod
    def validate(
        command: PostTransaction,
        *,
        catalog: AccountCatalogSnapshot,
    ) -> None:
        checked_command = _validate_command_shape(command)
        postings = tuple(
            _validate_posting_shape(posting) for posting in checked_command.postings
        )
        _reject_duplicate_posting_identity(postings)
        for posting in postings:
            _validate_posting_values(posting)

        accounts = _index_catalog(catalog)
        resolved_accounts: list[AccountSnapshot] = []
        balances: defaultdict[str, int] = defaultdict(int)
        for posting in postings:
            account = accounts.get((checked_command.book_id, posting.account_id))
            if account is None:
                if any(account_id == posting.account_id for _, account_id in accounts):
                    raise CrossBookAccount(
                        posting.account_id,
                        expected_book_id=checked_command.book_id,
                    )
                raise UnknownAccount(posting.account_id)
            if posting.asset_code != account.asset_code:
                raise PostingAssetMismatch(
                    posting.account_id,
                    posting_asset=posting.asset_code,
                    account_asset=account.asset_code,
                )

            resolved_accounts.append(account)
            direction = 1 if posting.side is PostingSide.DEBIT else -1
            balances[posting.asset_code] += direction * posting.units

        imbalances = {
            asset_code: net_units
            for asset_code, net_units in balances.items()
            if net_units != 0
        }
        if imbalances:
            raise UnbalancedAsset(imbalances)

        if checked_command.kind is TransactionKind.FX:
            exchanged_assets = set(balances)
            if len(exchanged_assets) < 2:
                raise InvalidFxTransaction(
                    "an FX transaction must exchange at least two assets"
                )
            trading_nets: defaultdict[str, int] = defaultdict(int)
            for posting, account in zip(postings, resolved_accounts, strict=True):
                if account.system_role is not AccountSystemRole.FX_TRADING:
                    continue
                direction = 1 if posting.side is PostingSide.DEBIT else -1
                trading_nets[posting.asset_code] += direction * posting.units

            missing_trading_assets = sorted(exchanged_assets - set(trading_nets))
            if missing_trading_assets:
                missing = ", ".join(missing_trading_assets)
                raise InvalidFxTransaction(
                    "an FX transaction requires a Book-owned trading-account "
                    f"posting leg for every asset; missing: {missing}"
                )

            zero_net_assets = sorted(
                asset_code
                for asset_code in exchanged_assets
                if trading_nets[asset_code] == 0
            )
            if zero_net_assets:
                zero = ", ".join(zero_net_assets)
                raise InvalidFxTransaction(
                    "FX trading-account net units must be nonzero for every asset; "
                    f"zero: {zero}"
                )

            if not (
                any(net_units > 0 for net_units in trading_nets.values())
                and any(net_units < 0 for net_units in trading_nets.values())
            ):
                directions = ", ".join(
                    f"{asset_code}="
                    f"{'debit' if trading_nets[asset_code] > 0 else 'credit'}"
                    for asset_code in sorted(exchanged_assets)
                )
                raise InvalidFxTransaction(
                    "FX trading-account nets must include opposite directions; "
                    f"got: {directions}"
                )
