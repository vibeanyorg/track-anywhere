from __future__ import annotations

from hashlib import sha256
from uuid import UUID

import pytest

from track_anywhere.application.repairs import (
    canonical_expense_clearing_account_id,
    repair_category,
    repair_command_id,
    replacement_transaction_id,
    reversal_transaction_id,
)
from track_anywhere.offline.repair_misclassified_expenses import (
    RepairRunnerFailure,
    _parse_arguments,
    _parse_plan,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


BOOK = UUID("11111111-1111-4111-8111-111111111111")
ORIGINAL = UUID("22222222-2222-4222-8222-222222222222")
WRONG = UUID("33333333-3333-4333-8333-333333333333")
CATEGORY = UUID("44444444-4444-4444-8444-444444444444")


def _payload() -> dict[str, object]:
    return {
        "actor_subject_id": "human:test",
        "book_id": str(BOOK),
        "close_account_ids": [str(WRONG)],
        "create_category_paths": [["通讯"], ["通讯", "手机话费"]],
        "provision_all_active_internal_accounts": True,
        "repairs": [
            {
                "category_id": str(CATEGORY),
                "original_transaction_id": str(ORIGINAL),
                "wrong_expense_account_id": str(WRONG),
            }
        ],
        "version": 1,
    }


def test_plan_is_hash_locked_and_apply_requires_double_confirmation() -> None:
    payload = _payload()
    raw = canonical_json_bytes(payload)
    digest = sha256(raw).hexdigest()

    plan = _parse_plan(raw, digest)
    assert plan.book_id == BOOK
    assert plan.provision_all_active_internal_accounts is True
    assert _parse_arguments(("--plan-sha256", digest, "--stdin")) == (
        digest,
        False,
    )
    assert _parse_arguments(
        (
            "--plan-sha256",
            digest,
            "--stdin",
            "--apply",
            "--confirm-plan-sha256",
            digest,
        )
    ) == (digest, True)

    with pytest.raises(RepairRunnerFailure) as changed:
        _parse_plan(raw.replace(b"human:test", b"human:else"), digest)
    assert changed.value.code == "plan_contract_mismatch"

    with pytest.raises(RepairRunnerFailure) as unconfirmed:
        _parse_arguments(
            (
                "--plan-sha256",
                digest,
                "--stdin",
                "--apply",
                "--confirm-plan-sha256",
                "0" * 64,
            )
        )
    assert unconfirmed.value.code == "plan_confirmation_mismatch"


def test_repair_and_catalog_identifiers_are_stable_and_separated() -> None:
    identifiers = {
        repair_command_id(BOOK, ORIGINAL),
        reversal_transaction_id(BOOK, ORIGINAL),
        replacement_transaction_id(BOOK, ORIGINAL),
        canonical_expense_clearing_account_id(BOOK, "CNY"),
        repair_category(BOOK, ("通讯",)).category_id,
        repair_category(BOOK, ("通讯", "手机话费")).category_id,
    }
    assert len(identifiers) == 6
    assert identifiers == {
        repair_command_id(BOOK, ORIGINAL),
        reversal_transaction_id(BOOK, ORIGINAL),
        replacement_transaction_id(BOOK, ORIGINAL),
        canonical_expense_clearing_account_id(BOOK, "CNY"),
        repair_category(BOOK, ("通讯",)).category_id,
        repair_category(BOOK, ("通讯", "手机话费")).category_id,
    }
