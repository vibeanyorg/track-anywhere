from __future__ import annotations

import inspect

from track_anywhere.storage import OrmStorage


def test_storage_change_writers_accept_single_explicit_change_set():
    writer_names = [
        "save_startup_maintenance",
        "save_idempotency",
        "save_credential_change",
        "save_audit_change",
        "save_authorization_grant_change",
        "save_device_grant_change",
        "save_catalog_change",
        "save_ledger_change",
        "save_reclassification_change",
        "save_user_change",
        "save_book_change",
        "save_auth_login_change",
        "save_draft_change",
        "save_recurring_change",
        "save_finance_change",
        "save_investment_change",
        "save_credit_card_profile_change",
        "save_payment_profile_change",
        "save_attachment_change",
    ]
    offenders = []
    for name in writer_names:
        signature = inspect.signature(getattr(OrmStorage, name))
        parameters = [parameter for parameter in signature.parameters.values() if parameter.name != "self"]
        if len(parameters) != 1 or parameters[0].name != "changes":
            offenders.append(f"{name}{signature}")

    assert offenders == []
