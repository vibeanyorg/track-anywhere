from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from uuid import UUID

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    PlannedCategory,
    PlannedCategoryVersion,
    PlannedProtectedContent,
)
from track_anywhere.application.imports.event_compiler import (
    FROZEN_IMPORT_ACTOR_SUBJECT_ID,
    compile_current_v2_events,
)
from track_anywhere.application.privacy.protected_content import (
    TransactionDescription,
)
from track_anywhere.application.privacy.service import ImportArchiveRecordCounts
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


def fixture_id(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _description(sidecar_id: UUID, *, purpose: str) -> PlannedProtectedContent:
    plaintext = canonical_json_bytes(
        TransactionDescription(
            purpose=purpose,
            transaction_memo=None,
            line_memos=(),
        ).model_dump(mode="json")
    )
    return PlannedProtectedContent(
        sidecar_id=sidecar_id,
        kind="transaction_description",
        canonical_plaintext=plaintext,
        record_counts=None,
    )


def _archive() -> PlannedProtectedContent:
    record_counts = ImportArchiveRecordCounts(
        classification_audit_records=43,
        investment_activities=6,
        investment_valuations=0,
        uncategorized_fx_reporting_facts=5,
        institution_metadata_records=117,
        counterparty_records=2,
        omission_records=5,
    )
    table_hashes = {
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
    rows: list[dict[str, object]] = [
        {
            "record_type": "archive_manifest",
            "contract_version": 1,
            "source_dump_hash": "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e",
            "source_manifest_hash": "f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f",
            "card_review_hash": "237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430",
            "source_revision": "0019_posting_constraints",
            "source_counts": {
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
            },
            "table_hashes": table_hashes,
        }
    ]

    def source_records(
        record_type: str, count: int, identity_field: str
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for index in range(count):
            identity = f"fixture-{index:03d}"
            source = {identity_field: identity, "fixture_index": index}
            reference = (
                "sha256:"
                + hashlib.sha256(
                    b"track-anywhere:frozen-v1:opaque-reference:v1\0"
                    + record_type.encode("ascii")
                    + b"\0"
                    + identity.encode("utf-8")
                ).hexdigest()
            )
            result.append(
                {
                    "record_type": record_type,
                    "source_ref": reference,
                    "source_row_hash": hashlib.sha256(
                        canonical_json_bytes(source)
                    ).hexdigest(),
                    "source": source,
                }
            )
        return sorted(result, key=lambda record: str(record["source_ref"]))

    rows.extend(source_records("classification_audit", 43, "classification_event_id"))
    rows.extend(source_records("investment_activity", 6, "event_id"))
    rows.append({"record_type": "investment_valuation_summary", "source_count": 0})
    rows.extend(source_records("uncategorized_fx_reporting_fact", 5, "line_id"))
    for index in range(117):
        archived_metadata = {
            "source_account_ref": f"sha256:{index:064x}",
            "institution_type": None,
            "institution": {"fixture_index": index},
        }
        rows.append(
            {
                "record_type": "institution_metadata",
                "source_row_hash": f"{index + 1:064x}",
                "archived_metadata_hash": hashlib.sha256(
                    b"track-anywhere:frozen-v1:institution-metadata:v1\0"
                    + canonical_json_bytes(archived_metadata)
                ).hexdigest(),
                **archived_metadata,
            }
        )
    rows.extend(source_records("counterparty_metadata", 2, "counterparty_id"))
    rows.extend(
        {
            "record_type": "omission_reason",
            "reason_code": code,
            "source_count": count,
        }
        for code, count in (
            ("classification_audit_not_current_event", 43),
            ("investment_activity_lacks_lot_semantics", 6),
            ("uncategorized_fx_lacks_category", 5),
            ("institution_metadata_no_v2_catalog", 117),
            ("counterparty_metadata_no_v2_catalog", 2),
        )
    )
    return PlannedProtectedContent(
        sidecar_id=fixture_id(4000),
        kind="import_archive",
        canonical_plaintext=b"\n".join(canonical_json_bytes(row) for row in rows)
        + b"\n",
        record_counts=record_counts,
    )


def build_valid_fixture_plan(
    *,
    target_book_id: UUID | None = None,
    command_id: UUID | None = None,
) -> FrozenFinancialHistoryPlan:
    assets = tuple(
        PlannedAsset(
            asset_code="USDT" if index == 19 else f"T{index:02d}",
            kind="crypto" if index == 19 else "synthetic",
            ledger_scale=8 if index == 19 else 2,
            input_scale=6 if index == 19 else 2,
            display_scale=6 if index == 19 else 2,
            current_name=(
                "fixture-usdt" if index == 19 else f"fixture-asset-{index:02d}"
            ),
            status="active",
        )
        for index in range(20)
    )
    accounts = tuple(
        PlannedAccount(
            account_id=fixture_id(1000 + index),
            asset_code="USDT" if index >= 119 else "T00",
            account_type="liability" if index < 5 else "asset",
            account_subtype="credit_card" if index < 5 else None,
            system_role=None,
            current_name=f"fixture-account-{index:03d}",
            status="active",
            expected_natural_units=0,
            close_after_import=index == 0,
        )
        for index in range(121)
    )
    categories = tuple(
        PlannedCategory(
            category_id=fixture_id(2000 + index),
            parent_category_id=None,
            current_name=f"fixture-category-{index:02d}",
            current_version_id=fixture_id(2100 + index),
            status="active",
            version=PlannedCategoryVersion(
                category_version_id=fixture_id(2100 + index),
                parent_category_id=None,
                name=f"fixture-category-{index:02d}",
                status="active",
                change_reason_code="fixture_import",
            ),
        )
        for index in range(37)
    )
    descriptions = tuple(
        _description(
            fixture_id(3000 + index),
            purpose=(
                "reviewed_card_direction_correction"
                if 135 <= index < 138
                else "fixture-purpose"
            ),
        )
        for index in range(138)
    )
    target_book_id = target_book_id or fixture_id(1)
    command_id = command_id or fixture_id(2)
    effective_at = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)

    pending_posted: list[PendingEvent] = []
    next_posting_id = 10_000
    for index in range(130):
        posting_count = 3 if 8 <= index < 22 else 2
        if index == 40:
            posting_shape = (
                (PostingSide.DEBIT, "12345678"),
                (PostingSide.CREDIT, "12345678"),
            )
        elif posting_count == 3:
            posting_shape = (
                (PostingSide.DEBIT, "2"),
                (PostingSide.CREDIT, "1"),
                (PostingSide.CREDIT, "1"),
            )
        else:
            posting_shape = (
                (PostingSide.DEBIT, "1"),
                (PostingSide.CREDIT, "1"),
            )
        postings = tuple(
            JournalPostingFact(
                posting_id=fixture_id(next_posting_id + position),
                position=position,
                account_id=(
                    accounts[0].account_id
                    if index == 0
                    else accounts[index - 7].account_id
                    if 8 <= index < 12 and position == 1
                    else accounts[119 + position].account_id
                    if index == 40
                    else accounts[5 + ((index + position) % 114)].account_id
                ),
                asset_code="USDT" if index == 40 else "T00",
                side=side,
                units=units,
            )
            for position, (side, units) in enumerate(posting_shape)
        )
        next_posting_id += posting_count
        transaction_id = fixture_id(6000 + index)
        pending_posted.append(
            PendingEvent(
                event_id=fixture_id(5000 + index),
                stream_type="journal_transaction",
                stream_id=transaction_id,
                payload=JournalTransactionPosted(
                    transaction_id=transaction_id,
                    kind=TransactionKind.STANDARD,
                    postings=postings,
                    description_ref=descriptions[index].sidecar_id,
                    external_references=(
                        FinancialExternalReference(
                            provider_code="v1_history",
                            kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                            reference=f"sha256:{index:064x}",
                        ),
                    ),
                ),
                command_id=command_id,
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                correlation_id=command_id,
                causation_event_id=None,
                effective_at=effective_at,
            )
        )

    posted_plan = compile_current_v2_events(
        target_book_id=target_book_id,
        events=tuple(pending_posted),
    )
    pending_reversals: list[PendingEvent] = []
    for index in range(8):
        source_pending = pending_posted[index]
        source_payload = source_pending.payload
        assert type(source_payload) is JournalTransactionPosted
        inverse = tuple(
            JournalPostingFact(
                posting_id=fixture_id(next_posting_id + position),
                position=posting.position,
                account_id=posting.account_id,
                asset_code=posting.asset_code,
                side=(
                    PostingSide.CREDIT
                    if posting.side is PostingSide.DEBIT
                    else PostingSide.DEBIT
                ),
                units=posting.units,
            )
            for position, posting in enumerate(source_payload.postings)
        )
        next_posting_id += len(inverse)
        reversal_transaction_id = fixture_id(6130 + index)
        pending_reversals.append(
            PendingEvent(
                event_id=fixture_id(5130 + index),
                stream_type="journal_transaction",
                stream_id=reversal_transaction_id,
                payload=JournalTransactionReversed(
                    reversal_transaction_id=reversal_transaction_id,
                    reverses_transaction_id=source_payload.transaction_id,
                    original_event_id=source_pending.event_id,
                    original_event_hash=posted_plan[index].event_hash,
                    reason_code=ReversalReasonCode.IMPORT_CORRECTION,
                    inverse_postings=inverse,
                    description_ref=descriptions[130 + index].sidecar_id,
                ),
                command_id=command_id,
                actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
                correlation_id=command_id,
                causation_event_id=source_pending.event_id,
                effective_at=effective_at,
            )
        )

    pending_reporting = tuple(
        PendingEvent(
            event_id=fixture_id(5200 + index),
            stream_type="reporting_lines",
            stream_id=fixture_id(6000 + index),
            payload=ReportingLinesAssigned(
                transaction_id=fixture_id(6000 + index),
                classification_revision=1,
                lines=(
                    ReportingLine(
                        line_id=fixture_id(7000 + index),
                        line_version_id=fixture_id(7100 + index),
                        catalog_id=categories[
                            index % len(categories)
                        ].current_version_id,
                        position=0,
                        asset_code="T00",
                        units="1",
                        line_kind=ReportingLineKind.EXPENSE,
                        dimension=ReportingDimension.CATEGORY,
                        dimension_id=categories[index % len(categories)].category_id,
                        description_ref=None,
                    ),
                ),
            ),
            command_id=command_id,
            actor_subject_id=FROZEN_IMPORT_ACTOR_SUBJECT_ID,
            correlation_id=command_id,
            causation_event_id=fixture_id(5000 + index),
            effective_at=effective_at,
        )
        for index in range(38)
    )
    events = compile_current_v2_events(
        target_book_id=target_book_id,
        events=tuple(pending_posted) + tuple(pending_reversals) + pending_reporting,
    )
    raw_balances: dict[UUID, int] = {}
    for event in events:
        payload = event.payload
        postings = (
            payload.postings
            if type(payload) is JournalTransactionPosted
            else payload.inverse_postings
            if type(payload) is JournalTransactionReversed
            else ()
        )
        for posting in postings:
            raw_balances[posting.account_id] = raw_balances.get(
                posting.account_id, 0
            ) + (
                int(posting.units)
                if posting.side is PostingSide.DEBIT
                else -int(posting.units)
            )
    accounts = tuple(
        account.model_copy(
            update={
                "expected_natural_units": (
                    -raw_balances.get(account.account_id, 0)
                    if account.account_type in {"liability", "equity", "income"}
                    else raw_balances.get(account.account_id, 0)
                )
            }
        )
        for account in accounts
    )
    return FrozenFinancialHistoryPlan(
        contract_version=1,
        target_book_id=target_book_id,
        source_dump_hash="a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e",
        manifest_hash="f1cb565c646dc759b203efad3a5584492f3c824a16abb728bbce83150413597f",
        card_review_hash="237f964a990018eb7fc91b9e45ba001ebb319456577f14263e3a444dc4d54430",
        assets=assets,
        accounts=accounts,
        categories=categories,
        descriptions=descriptions,
        archive=_archive(),
        events=events,
        expected_terminal_hash=events[-1].event_hash,
        quarantine_count=0,
    )


__all__ = ["build_valid_fixture_plan", "fixture_id"]
