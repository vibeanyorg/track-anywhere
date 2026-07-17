from __future__ import annotations

import re
import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictBytes,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ...application.privacy.service import ImportArchiveRecordCounts
from ...application.privacy.protected_content import TransactionDescription
from ...domain.journal.events import (
    ExternalReferenceKind,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from ...domain.privacy import AssetCode, FrozenContract
from ...domain.journal.models import PostingSide
from ...domain.reporting.events import ReportingDimension, ReportingLinesAssigned
from ...serialization.canonical_json import (
    EventHashEnvelope,
    canonical_json_bytes,
    event_hash,
)
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY


StrictUUID = Annotated[UUID, Field(strict=True)]
HexSha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
StrictAwareDatetime = Annotated[datetime, Field(strict=True)]
_ACCOUNT_SUBTYPE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_OPAQUE_V1_REFERENCE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPAQUE_REFERENCE_DOMAIN = b"track-anywhere:frozen-v1:opaque-reference:v1\0"
_INSTITUTION_METADATA_HASH_DOMAIN = (
    b"track-anywhere:frozen-v1:institution-metadata:v1\0"
)
FROZEN_IMPORT_ACTOR_SUBJECT_ID = "offline:frozen-v1-history"
_EXPECTED_SOURCE_REVISION = "0019_posting_constraints"
_EXPECTED_SOURCE_COUNTS = {
    "accounts": 121,
    "assets": 20,
    "categories": 37,
    "category_versions": 37,
    "classification_events": 43,
    "counterparties": 2,
    "investment_events": 6,
    "investment_valuations": 0,
    "ledger_books": 1,
    "postings": 284,
    "transaction_lines": 43,
    "transactions": 135,
}
_EXPECTED_SOURCE_TABLE_HASHES = {
    "accounts": "adb97d5b9e2959dd72d67a51dc80b00fe837eca3d8c2821a96ef96d389329890",
    "assets": "c0ccdfdebe14fb8ed6008901546c25838df380d775a62bb82b30a64cde51ad48",
    "categories": "d3d9c750c48ce5bc3dabd0d9d1423e60bca9b9aaf1c4d7673b447ea460c4d354",
    "category_versions": "d540c3f42ec321321ab1f3b07a2096fc6bff221c998637df802202767dcc9b3b",
    "classification_events": "36ffd21df519c2eba57e6206801f13078a1c9c5b23a44ac7d21ca79d77c7c171",
    "counterparties": "b6d201c5076c0acf4da0a0ce6197e5d5f9acb0b4b8511ed8dc66a054435b6a8e",
    "investment_events": "4382cdc5fb1d1afe86790d66e90cd07e0903cfbf711eaa0c126614d68f988bd6",
    "investment_valuations": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "ledger_books": "ed4001620a8ea1f415682758e46e3916b161edde867d5fcd1dcf580800e9c7a4",
    "postings": "958b8cd9c751592dd16c2ed00467c2314416eb652fadd94b002c506364221f0b",
    "transaction_lines": "7c4c19396714bf018109b2ddb545a7f65513f657a7f17d1e3a7daa49a57b4f70",
    "transactions": "4f4704a2636f49116cc963dac13b147e2ab2259a77c5d694b1d5a6bdd3865ee8",
}
_OMISSION_CODES_AND_COUNTS = (
    ("classification_audit_not_current_event", 43),
    ("investment_activity_lacks_lot_semantics", 6),
    ("uncategorized_fx_lacks_category", 5),
    ("institution_metadata_no_v2_catalog", 117),
    ("counterparty_metadata_no_v2_catalog", 2),
)
_CORRECTION_DESCRIPTION_BYTES = canonical_json_bytes(
    TransactionDescription(
        purpose="reviewed_card_direction_correction",
        transaction_memo=None,
        line_memos=(),
    ).model_dump(mode="json")
)


class ImportFrozenContract(FrozenContract):
    """Strict canonical contract used only by the private offline import."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class PlannedAsset(ImportFrozenContract):
    asset_code: AssetCode
    kind: StrictStr = Field(min_length=1, max_length=32)
    ledger_scale: StrictInt = Field(ge=0, le=30)
    input_scale: StrictInt = Field(ge=0, le=30)
    display_scale: StrictInt = Field(ge=0, le=30)
    current_name: StrictStr = Field(min_length=1, repr=False)
    status: Literal["active", "disabled"]

    @model_validator(mode="after")
    def validate_scales(self) -> PlannedAsset:
        if self.input_scale > self.ledger_scale:
            raise ValueError("asset input scale exceeds ledger scale")
        if self.display_scale > self.ledger_scale:
            raise ValueError("asset display scale exceeds ledger scale")
        if not self.current_name.strip():
            raise ValueError("asset name must be nonblank")
        if not self.kind.strip():
            raise ValueError("asset kind must be nonblank")
        return self


class PlannedProtectedContent(ImportFrozenContract):
    sidecar_id: StrictUUID
    kind: Literal["transaction_description", "import_archive"]
    canonical_plaintext: StrictBytes = Field(min_length=1, repr=False)
    record_counts: ImportArchiveRecordCounts | None

    @model_validator(mode="after")
    def validate_record_counts(self) -> PlannedProtectedContent:
        if (self.kind == "import_archive") != (self.record_counts is not None):
            raise ValueError("protected content record-count shape is invalid")
        return self


class PlannedAccount(ImportFrozenContract):
    account_id: StrictUUID
    asset_code: AssetCode
    account_type: Literal[
        "asset", "liability", "equity", "income", "expense", "fund", "system"
    ]
    account_subtype: StrictStr | None = Field(default=None, max_length=64)
    system_role: Literal["fx_trading"] | None = None
    current_name: StrictStr = Field(min_length=1, repr=False)
    status: Literal["active"]
    expected_natural_units: StrictInt = Field(repr=False)
    close_after_import: StrictBool

    @model_validator(mode="after")
    def validate_account(self) -> PlannedAccount:
        if not self.current_name.strip():
            raise ValueError("account name must be nonblank")
        if (
            self.account_subtype is not None
            and _ACCOUNT_SUBTYPE.fullmatch(self.account_subtype) is None
        ):
            raise ValueError("account subtype is invalid")
        if self.account_subtype == "credit_card" and self.account_type != "liability":
            raise ValueError("credit-card account must be a liability")
        if self.system_role is not None and self.account_type != "system":
            raise ValueError("only system accounts may have a system role")
        return self


class PlannedCategoryVersion(ImportFrozenContract):
    category_version_id: StrictUUID
    parent_category_id: StrictUUID | None
    name: StrictStr = Field(min_length=1, repr=False)
    status: Literal["active", "archived"]
    change_reason_code: StrictStr = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_text(self) -> PlannedCategoryVersion:
        if not self.name.strip() or not self.change_reason_code.strip():
            raise ValueError("category-version text must be nonblank")
        return self


class PlannedCategory(ImportFrozenContract):
    category_id: StrictUUID
    parent_category_id: StrictUUID | None
    current_name: StrictStr = Field(min_length=1, repr=False)
    current_version_id: StrictUUID
    status: Literal["active", "archived"]
    version: PlannedCategoryVersion

    @model_validator(mode="after")
    def validate_current_version(self) -> PlannedCategory:
        if not self.current_name.strip():
            raise ValueError("category name must be nonblank")
        if self.current_version_id != self.version.category_version_id:
            raise ValueError("category current version identity mismatch")
        if self.parent_category_id != self.version.parent_category_id:
            raise ValueError("category parent identity mismatch")
        if self.current_name != self.version.name or self.status != self.version.status:
            raise ValueError("category current version metadata mismatch")
        return self


PlannedEventPayload = (
    JournalTransactionPosted | JournalTransactionReversed | ReportingLinesAssigned
)


def _postings_are_exact_inverse(
    source: tuple[JournalPostingFact, ...],
    inverse: tuple[JournalPostingFact, ...],
) -> bool:
    if len(source) != len(inverse):
        return False
    if {item.posting_id for item in source} & {item.posting_id for item in inverse}:
        return False
    return all(
        candidate.position == original.position
        and candidate.account_id == original.account_id
        and candidate.asset_code == original.asset_code
        and candidate.units == original.units
        and candidate.side
        is (
            PostingSide.CREDIT
            if original.side is PostingSide.DEBIT
            else PostingSide.DEBIT
        )
        for original, candidate in zip(source, inverse, strict=True)
    )


def _contains_key(value: object, forbidden: str) -> bool:
    if type(value) is dict:
        return forbidden in value or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if type(value) is list:
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _validate_archive_ndjson(raw: bytes) -> tuple[dict[str, object], ...]:
    try:
        if type(raw) is not bytes or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
            raise ValueError
        lines = raw[:-1].split(b"\n")
        if not lines or any(not line for line in lines):
            raise ValueError
        records: list[dict[str, object]] = []
        for line in lines:
            parsed = json.loads(
                line.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if (
                type(parsed) is not dict
                or canonical_json_bytes(parsed) != line
                or _contains_key(parsed, "plan_hash")
            ):
                raise ValueError
            record_type = parsed.get("record_type")
            if type(record_type) is not str or not record_type:
                raise ValueError
            records.append(parsed)
        return tuple(records)
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError(
            "frozen financial history archive content is invalid"
        ) from None


def _validate_archive_source_records(
    records: tuple[dict[str, object], ...],
    *,
    record_type: str,
    reference_field: str,
    identity_field: str | None,
) -> None:
    references: list[str] = []
    expected_keys = (
        {
            "record_type",
            "source_account_ref",
            "source_row_hash",
            "archived_metadata_hash",
            "institution_type",
            "institution",
        }
        if reference_field == "source_account_ref"
        else {"record_type", "source_ref", "source_row_hash", "source"}
    )
    for record in records:
        reference = record.get(reference_field)
        row_hash = record.get("source_row_hash")
        if (
            set(record) != expected_keys
            or record.get("record_type") != record_type
            or type(reference) is not str
            or _OPAQUE_V1_REFERENCE.fullmatch(reference) is None
            or type(row_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", row_hash) is None
        ):
            raise ValueError("frozen financial history archive content is invalid")
        if identity_field is not None:
            source = record.get("source")
            if type(source) is not dict:
                raise ValueError("frozen financial history archive content is invalid")
            identity = source.get(identity_field)
            if type(identity) is str and identity.strip():
                canonical_identity = identity
            elif type(identity) is int and identity >= 0:
                canonical_identity = str(identity)
            else:
                raise ValueError("frozen financial history archive content is invalid")
            expected_row_hash = hashlib.sha256(canonical_json_bytes(source)).hexdigest()
            expected_reference = (
                "sha256:"
                + hashlib.sha256(
                    _OPAQUE_REFERENCE_DOMAIN
                    + record_type.encode("ascii")
                    + b"\0"
                    + canonical_identity.encode("utf-8")
                ).hexdigest()
            )
            if row_hash != expected_row_hash or reference != expected_reference:
                raise ValueError("frozen financial history archive content is invalid")
        else:
            archived_metadata_hash = record.get("archived_metadata_hash")
            expected_metadata_hash = hashlib.sha256(
                _INSTITUTION_METADATA_HASH_DOMAIN
                + canonical_json_bytes(
                    {
                        "source_account_ref": reference,
                        "institution_type": record.get("institution_type"),
                        "institution": record.get("institution"),
                    }
                )
            ).hexdigest()
            if (
                type(archived_metadata_hash) is not str
                or archived_metadata_hash != expected_metadata_hash
            ):
                raise ValueError("frozen financial history archive content is invalid")
        references.append(reference)
    if references != sorted(references) or len(references) != len(set(references)):
        raise ValueError("frozen financial history archive content is invalid")


class PlannedLedgerEvent(ImportFrozenContract):
    event_id: StrictUUID
    book_position: StrictInt = Field(gt=0)
    stream_type: StrictStr = Field(min_length=1, max_length=32)
    stream_id: StrictUUID
    expected_stream_version: Literal[0]
    stream_version: Literal[1]
    event_type: Literal[
        "JournalTransactionPosted",
        "JournalTransactionReversed",
        "ReportingLinesAssigned",
    ]
    event_schema_version: Literal[1]
    payload: PlannedEventPayload = Field(repr=False)
    command_id: StrictUUID
    actor_subject_id: StrictStr = Field(min_length=1, max_length=128)
    correlation_id: StrictUUID
    causation_event_id: StrictUUID | None
    effective_at: StrictAwareDatetime
    previous_hash: HexSha256
    event_hash: HexSha256

    @field_validator("effective_at", mode="after")
    @classmethod
    def normalize_effective_at(cls, value: datetime) -> datetime:
        try:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError
            return value.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                "planned event effective time must be timezone-aware"
            ) from None

    @model_validator(mode="after")
    def validate_envelope(self) -> PlannedLedgerEvent:
        if type(self.payload).event_type != self.event_type:
            raise ValueError("planned event payload type mismatch")
        if type(self.payload).schema_version != self.event_schema_version:
            raise ValueError("planned event payload schema mismatch")
        return self


class FrozenFinancialHistoryPlan(ImportFrozenContract):
    contract_version: Literal[1]
    target_book_id: StrictUUID
    source_dump_hash: HexSha256
    manifest_hash: HexSha256
    card_review_hash: HexSha256
    assets: tuple[PlannedAsset, ...]
    accounts: tuple[PlannedAccount, ...]
    categories: tuple[PlannedCategory, ...]
    descriptions: tuple[PlannedProtectedContent, ...] = Field(repr=False)
    archive: PlannedProtectedContent = Field(repr=False)
    events: tuple[PlannedLedgerEvent, ...]
    expected_terminal_hash: HexSha256
    quarantine_count: Literal[0]

    @model_validator(mode="after")
    def validate_fixed_counts(self) -> FrozenFinancialHistoryPlan:
        if (
            len(self.assets) != 20
            or len(self.accounts) != 121
            or len(self.categories) != 37
            or len(self.descriptions) != 138
            or len(self.events) != 176
        ):
            raise ValueError("frozen financial history has invalid fixed plan counts")
        identity_groups = (
            tuple(item.asset_code for item in self.assets),
            tuple(item.account_id for item in self.accounts),
            tuple(item.category_id for item in self.categories),
            tuple(item.version.category_version_id for item in self.categories),
            tuple(item.sidecar_id for item in self.descriptions),
            tuple(item.event_id for item in self.events),
            tuple((item.stream_type, item.stream_id) for item in self.events),
        )
        if any(len(values) != len(set(values)) for values in identity_groups):
            raise ValueError(
                "frozen financial history contains a duplicate plan identity"
            )
        if self.archive.sidecar_id in {item.sidecar_id for item in self.descriptions}:
            raise ValueError(
                "frozen financial history contains a duplicate plan identity"
            )
        self._validate_catalog_order_and_references()
        self._validate_event_topology_and_hashes()
        self._validate_protected_content()
        return self

    def _validate_catalog_order_and_references(self) -> None:
        if tuple(item.asset_code for item in self.assets) != tuple(
            sorted(item.asset_code for item in self.assets)
        ):
            raise ValueError("frozen financial history asset order is invalid")
        if tuple(item.account_id.bytes for item in self.accounts) != tuple(
            sorted(item.account_id.bytes for item in self.accounts)
        ):
            raise ValueError("frozen financial history account order is invalid")
        asset_codes = {item.asset_code for item in self.assets}
        if any(account.asset_code not in asset_codes for account in self.accounts):
            raise ValueError("frozen financial history account asset is invalid")
        category_ids = {item.category_id for item in self.categories}
        emitted: set[UUID] = set()
        for category in self.categories:
            if (
                category.parent_category_id is not None
                and category.parent_category_id not in emitted
            ):
                raise ValueError("frozen financial history category order is invalid")
            emitted.add(category.category_id)
        if emitted != category_ids:
            raise ValueError("frozen financial history category order is invalid")
        if tuple(item.sidecar_id.bytes for item in self.descriptions) != tuple(
            sorted(item.sidecar_id.bytes for item in self.descriptions)
        ):
            raise ValueError("frozen financial history description order is invalid")
        card_accounts = tuple(
            account
            for account in self.accounts
            if account.account_subtype == "credit_card"
        )
        closing = tuple(
            account for account in self.accounts if account.close_after_import
        )
        if (
            len(card_accounts) != 5
            or len(closing) != 1
            or closing[0].account_subtype != "credit_card"
            or closing[0].expected_natural_units != 0
        ):
            raise ValueError(
                "frozen financial history card account contract is invalid"
            )

    def _validate_event_topology_and_hashes(self) -> None:
        event_types = tuple(type(item.payload) for item in self.events)
        if event_types != (
            (JournalTransactionPosted,) * 130
            + (JournalTransactionReversed,) * 8
            + (ReportingLinesAssigned,) * 38
        ):
            raise ValueError("frozen financial history event topology is invalid")
        if tuple(item.book_position for item in self.events) != tuple(range(1, 177)):
            raise ValueError("frozen financial history Book positions are invalid")
        command_ids = {item.command_id for item in self.events}
        actor_ids = {item.actor_subject_id for item in self.events}
        if (
            len(command_ids) != 1
            or actor_ids != {FROZEN_IMPORT_ACTOR_SUBJECT_ID}
            or any(item.correlation_id != item.command_id for item in self.events)
        ):
            raise ValueError(
                "frozen financial history event command binding is invalid"
            )

        descriptions = {item.sidecar_id for item in self.descriptions}
        account_ids = {item.account_id for item in self.accounts}
        account_assets = {item.account_id: item.asset_code for item in self.accounts}
        asset_codes = {item.asset_code for item in self.assets}
        category_versions = {
            (item.category_id, item.current_version_id) for item in self.categories
        }
        journal_by_transaction: dict[UUID, PlannedLedgerEvent] = {}
        event_by_id: dict[UUID, PlannedLedgerEvent] = {}
        posting_ids: set[UUID] = set()
        line_ids: set[UUID] = set()
        line_version_ids: set[UUID] = set()
        journal_description_refs: list[UUID] = []
        external_references: set[str] = set()
        raw_balances: dict[UUID, int] = {}
        debit_by_transaction: dict[UUID, dict[str, int]] = {}
        previous_hash = bytes(32)
        posting_count = 0
        reporting_line_count = 0
        for position, planned in enumerate(self.events, start=1):
            payload = planned.payload
            posted_transaction_id: UUID | None = None
            if planned.previous_hash != previous_hash.hex():
                raise ValueError("frozen financial history hash chain is invalid")
            stored_payload = PRODUCTION_EVENT_REGISTRY.dump_registered(payload)
            round_tripped_payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
                planned.event_type,
                planned.event_schema_version,
                stored_payload,
            )
            if (
                type(round_tripped_payload) is not type(payload)
                or round_tripped_payload != payload
                or PRODUCTION_EVENT_REGISTRY.dump_registered(round_tripped_payload)
                != stored_payload
            ):
                raise ValueError(
                    "frozen financial history event registry binding is invalid"
                )
            calculated = event_hash(
                EventHashEnvelope(
                    event_id=planned.event_id,
                    book_id=self.target_book_id,
                    book_position=position,
                    global_sequence=1,
                    recorded_at=datetime(1970, 1, 1, tzinfo=UTC),
                    stream_type=planned.stream_type,
                    stream_id=planned.stream_id,
                    stream_version=planned.stream_version,
                    event_type=planned.event_type,
                    event_schema_version=planned.event_schema_version,
                    command_id=planned.command_id,
                    actor_subject_id=planned.actor_subject_id,
                    correlation_id=planned.correlation_id,
                    causation_event_id=planned.causation_event_id,
                    effective_at=planned.effective_at,
                    previous_hash=previous_hash,
                ),
                stored_payload,
            )
            if planned.event_hash != calculated.hex():
                raise ValueError("frozen financial history event hash is invalid")
            previous_hash = calculated

            if type(payload) is JournalTransactionPosted:
                if (
                    planned.stream_type != "journal_transaction"
                    or planned.stream_id != payload.transaction_id
                    or planned.causation_event_id is not None
                    or payload.transaction_id in journal_by_transaction
                    or len(payload.external_references) != 1
                    or payload.external_references[0].provider_code != "v1_history"
                    or payload.external_references[0].kind
                    is not ExternalReferenceKind.PROVIDER_TRANSACTION
                    or _OPAQUE_V1_REFERENCE.fullmatch(
                        payload.external_references[0].reference
                    )
                    is None
                    or payload.external_references[0].reference in external_references
                ):
                    raise ValueError("frozen financial history posted event is invalid")
                external_references.add(payload.external_references[0].reference)
                journal_by_transaction[payload.transaction_id] = planned
                event_postings = payload.postings
                description_ref = payload.description_ref
                posted_transaction_id = payload.transaction_id
            elif type(payload) is JournalTransactionReversed:
                source = event_by_id.get(payload.original_event_id)
                if (
                    planned.stream_type != "journal_transaction"
                    or planned.stream_id != payload.reversal_transaction_id
                    or payload.reversal_transaction_id in journal_by_transaction
                    or source is None
                    or source.event_hash != payload.original_event_hash
                    or planned.causation_event_id != source.event_id
                    or type(source.payload)
                    not in (JournalTransactionPosted, JournalTransactionReversed)
                    or payload.reason_code is not ReversalReasonCode.IMPORT_CORRECTION
                ):
                    raise ValueError(
                        "frozen financial history reversal provenance is invalid"
                    )
                source_transaction_id = (
                    source.payload.transaction_id
                    if type(source.payload) is JournalTransactionPosted
                    else source.payload.reversal_transaction_id
                )
                source_postings = (
                    source.payload.postings
                    if type(source.payload) is JournalTransactionPosted
                    else source.payload.inverse_postings
                )
                if (
                    payload.reverses_transaction_id != source_transaction_id
                    or not _postings_are_exact_inverse(
                        source_postings, payload.inverse_postings
                    )
                ):
                    raise ValueError(
                        "frozen financial history reversal provenance is invalid"
                    )
                journal_by_transaction[payload.reversal_transaction_id] = planned
                event_postings = payload.inverse_postings
                description_ref = payload.description_ref
            else:
                source = journal_by_transaction.get(payload.transaction_id)
                if (
                    planned.stream_type != "reporting_lines"
                    or planned.stream_id != payload.transaction_id
                    or payload.classification_revision != 1
                    or source is None
                    or type(source.payload) is not JournalTransactionPosted
                    or planned.causation_event_id != source.event_id
                    or len(payload.lines) != 1
                ):
                    raise ValueError(
                        "frozen financial history reporting event is invalid"
                    )
                for line in payload.lines:
                    if (
                        line.line_id in line_ids
                        or line.line_version_id in line_version_ids
                        or line.asset_code not in asset_codes
                        or line.dimension is not ReportingDimension.CATEGORY
                        or line.dimension_id is None
                        or line.description_ref is not None
                        or (line.dimension_id, line.catalog_id) not in category_versions
                    ):
                        raise ValueError(
                            "frozen financial history reporting line is invalid"
                        )
                    line_ids.add(line.line_id)
                    line_version_ids.add(line.line_version_id)
                allocated_by_asset: dict[str, int] = {}
                for line in payload.lines:
                    allocated_by_asset[line.asset_code] = allocated_by_asset.get(
                        line.asset_code, 0
                    ) + int(line.units)
                source_debits = debit_by_transaction.get(payload.transaction_id, {})
                if any(
                    allocated > source_debits.get(asset_code, 0)
                    for asset_code, allocated in allocated_by_asset.items()
                ):
                    raise ValueError(
                        "frozen financial history reporting allocation is invalid"
                    )
                reporting_line_count += len(payload.lines)
                event_by_id[planned.event_id] = planned
                continue

            if description_ref is None or description_ref not in descriptions:
                raise ValueError(
                    "frozen financial history description reference is invalid"
                )
            journal_description_refs.append(description_ref)
            event_debits: dict[str, int] = {}
            event_credits: dict[str, int] = {}
            for posting in event_postings:
                if (
                    posting.posting_id in posting_ids
                    or posting.account_id not in account_ids
                    or posting.asset_code not in asset_codes
                    or account_assets.get(posting.account_id) != posting.asset_code
                ):
                    raise ValueError(
                        "frozen financial history posting reference is invalid"
                    )
                posting_ids.add(posting.posting_id)
                signed_units = int(posting.units)
                event_side_totals = (
                    event_debits if posting.side is PostingSide.DEBIT else event_credits
                )
                event_side_totals[posting.asset_code] = (
                    event_side_totals.get(posting.asset_code, 0) + signed_units
                )
                raw_balances[posting.account_id] = raw_balances.get(
                    posting.account_id, 0
                ) + (
                    signed_units if posting.side is PostingSide.DEBIT else -signed_units
                )
            if event_debits != event_credits:
                raise ValueError("frozen financial history journal balance is invalid")
            if posted_transaction_id is not None:
                debit_by_transaction[posted_transaction_id] = event_debits
            posting_count += len(event_postings)
            event_by_id[planned.event_id] = planned

        if (
            posting_count != 290
            or reporting_line_count != 38
            or len(external_references) != 130
            or len(journal_description_refs) != 138
            or set(journal_description_refs) != descriptions
            or len(journal_description_refs) != len(set(journal_description_refs))
            or self.expected_terminal_hash != previous_hash.hex()
        ):
            raise ValueError("frozen financial history terminal topology is invalid")
        if any(
            account.expected_natural_units
            != (
                -raw_balances.get(account.account_id, 0)
                if account.account_type in {"liability", "equity", "income"}
                else raw_balances.get(account.account_id, 0)
            )
            for account in self.accounts
        ):
            raise ValueError("frozen financial history account balance is invalid")

    def _validate_protected_content(self) -> None:
        correction_descriptions: set[UUID] = set()
        for content in self.descriptions:
            if (
                content.kind != "transaction_description"
                or content.record_counts is not None
            ):
                raise ValueError(
                    "frozen financial history description content is invalid"
                )
            try:
                parsed = TransactionDescription.model_validate_json(
                    content.canonical_plaintext, strict=True
                )
                if (
                    canonical_json_bytes(parsed.model_dump(mode="json"))
                    != content.canonical_plaintext
                ):
                    raise ValueError
                if content.canonical_plaintext == _CORRECTION_DESCRIPTION_BYTES:
                    correction_descriptions.add(content.sidecar_id)
                elif parsed.purpose == "reviewed_card_direction_correction":
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(
                    "frozen financial history description content is invalid"
                ) from None
        expected_correction_descriptions = {
            event.payload.description_ref
            for event in self.events[135:138]
            if type(event.payload) is JournalTransactionReversed
        }
        if (
            len(expected_correction_descriptions) != 3
            or None in expected_correction_descriptions
            or correction_descriptions != expected_correction_descriptions
        ):
            raise ValueError(
                "frozen financial history correction descriptions are invalid"
            )
        if self.archive.kind != "import_archive" or self.archive.record_counts is None:
            raise ValueError("frozen financial history archive content is invalid")
        records = _validate_archive_ndjson(self.archive.canonical_plaintext)
        expected_types = (
            ("archive_manifest",)
            + ("classification_audit",) * 43
            + ("investment_activity",) * 6
            + ("investment_valuation_summary",)
            + ("uncategorized_fx_reporting_fact",) * 5
            + ("institution_metadata",) * 117
            + ("counterparty_metadata",) * 2
            + ("omission_reason",) * 5
        )
        header = records[0] if records else {}
        expected_header_keys = {
            "record_type",
            "contract_version",
            "source_dump_hash",
            "source_manifest_hash",
            "card_review_hash",
            "source_revision",
            "source_counts",
            "table_hashes",
        }
        expected_omissions = tuple(
            {
                "record_type": "omission_reason",
                "reason_code": code,
                "source_count": count,
            }
            for code, count in _OMISSION_CODES_AND_COUNTS
        )
        if (
            tuple(record.get("record_type") for record in records) != expected_types
            or set(header) != expected_header_keys
            or header.get("contract_version") != 1
            or header.get("source_dump_hash") != self.source_dump_hash
            or header.get("source_manifest_hash") != self.manifest_hash
            or header.get("card_review_hash") != self.card_review_hash
            or header.get("source_revision") != _EXPECTED_SOURCE_REVISION
            or header.get("source_counts") != _EXPECTED_SOURCE_COUNTS
            or header.get("table_hashes") != _EXPECTED_SOURCE_TABLE_HASHES
            or records[50]
            != {
                "record_type": "investment_valuation_summary",
                "source_count": 0,
            }
            or records[175:180] != expected_omissions
            or self.archive.record_counts
            != ImportArchiveRecordCounts(
                classification_audit_records=43,
                investment_activities=6,
                investment_valuations=0,
                uncategorized_fx_reporting_facts=5,
                institution_metadata_records=117,
                counterparty_records=2,
                omission_records=5,
            )
        ):
            raise ValueError("frozen financial history archive content is invalid")
        _validate_archive_source_records(
            records[1:44],
            record_type="classification_audit",
            reference_field="source_ref",
            identity_field="classification_event_id",
        )
        _validate_archive_source_records(
            records[44:50],
            record_type="investment_activity",
            reference_field="source_ref",
            identity_field="event_id",
        )
        _validate_archive_source_records(
            records[51:56],
            record_type="uncategorized_fx_reporting_fact",
            reference_field="source_ref",
            identity_field="line_id",
        )
        _validate_archive_source_records(
            records[56:173],
            record_type="institution_metadata",
            reference_field="source_account_ref",
            identity_field=None,
        )
        _validate_archive_source_records(
            records[173:175],
            record_type="counterparty_metadata",
            reference_field="source_ref",
            identity_field="counterparty_id",
        )


def _validated_plan(plan: FrozenFinancialHistoryPlan) -> FrozenFinancialHistoryPlan:
    if type(plan) is not FrozenFinancialHistoryPlan:
        raise ValueError("frozen financial history plan type is invalid")
    return FrozenFinancialHistoryPlan.model_validate(
        plan.model_dump(mode="python", round_trip=True),
        strict=True,
    )


def canonical_plan_bytes(plan: FrozenFinancialHistoryPlan) -> bytes:
    validated = _validated_plan(plan)
    value = validated.model_dump(mode="json", round_trip=True)
    return canonical_json_bytes(value)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def parse_canonical_plan_bytes(raw: bytes) -> FrozenFinancialHistoryPlan:
    try:
        if type(raw) is not bytes or not raw:
            raise ValueError
        decoded = raw.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if type(parsed) is not dict:
            raise ValueError
        plan = FrozenFinancialHistoryPlan.model_validate_json(raw, strict=True)
        if canonical_plan_bytes(plan) != raw:
            raise ValueError
        return plan
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("canonical financial history plan is invalid") from None


def plan_sha256(plan: FrozenFinancialHistoryPlan) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def plan_summary(plan: FrozenFinancialHistoryPlan) -> dict[str, object]:
    validated = _validated_plan(plan)
    journal_events = tuple(
        event
        for event in validated.events
        if type(event.payload) in (JournalTransactionPosted, JournalTransactionReversed)
    )
    reporting_events = tuple(
        event
        for event in validated.events
        if type(event.payload) is ReportingLinesAssigned
    )
    return {
        "contract_version": validated.contract_version,
        "source_dump_hash": validated.source_dump_hash,
        "manifest_hash": validated.manifest_hash,
        "card_review_hash": validated.card_review_hash,
        "plan_hash": plan_sha256(validated),
        "expected_terminal_hash": validated.expected_terminal_hash,
        "counts": {
            "assets": len(validated.assets),
            "accounts": len(validated.accounts),
            "categories": len(validated.categories),
            "category_versions": len(validated.categories),
            "journal_transactions": len(journal_events),
            "postings": sum(
                len(event.payload.postings)
                if type(event.payload) is JournalTransactionPosted
                else len(event.payload.inverse_postings)
                for event in journal_events
            ),
            "reversals": sum(
                type(event.payload) is JournalTransactionReversed
                for event in journal_events
            ),
            "reporting_assignments": len(reporting_events),
            "reporting_lines": sum(
                len(event.payload.lines) for event in reporting_events
            ),
            "events": len(validated.events),
            "descriptions": len(validated.descriptions),
            "archives": 1,
            "quarantine": validated.quarantine_count,
        },
    }


__all__ = [
    "FROZEN_IMPORT_ACTOR_SUBJECT_ID",
    "ImportFrozenContract",
    "HexSha256",
    "FrozenFinancialHistoryPlan",
    "PlannedAccount",
    "PlannedAsset",
    "PlannedCategory",
    "PlannedCategoryVersion",
    "PlannedEventPayload",
    "PlannedLedgerEvent",
    "PlannedProtectedContent",
    "StrictUUID",
    "canonical_plan_bytes",
    "plan_sha256",
    "plan_summary",
    "parse_canonical_plan_bytes",
]
