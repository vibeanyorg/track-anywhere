from __future__ import annotations

import re


_ACCOUNT_TYPES = frozenset(
    {"asset", "liability", "equity", "income", "expense", "fund", "system"}
)
_ACCOUNT_SUBTYPE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def parse_external_reference(raw: str) -> dict[str, str]:
    parts = raw.split(":", 2)
    if len(parts) != 3 or any(not value for value in parts):
        raise ValueError("--external-reference must be PROVIDER_CODE:KIND:REFERENCE")
    provider_code, kind, reference = parts
    return {
        "provider_code": provider_code,
        "kind": kind,
        "reference": reference,
    }


def validate_account_semantics(
    account_type: str,
    account_subtype: str | None,
) -> None:
    if account_type not in _ACCOUNT_TYPES:
        allowed = ", ".join(sorted(_ACCOUNT_TYPES))
        raise ValueError(f"--type must be one of: {allowed}")
    if account_subtype is not None and (
        len(account_subtype) > 64 or _ACCOUNT_SUBTYPE.fullmatch(account_subtype) is None
    ):
        raise ValueError("--account-subtype must be a lowercase slug")
    if account_subtype == "credit_card" and account_type != "liability":
        raise ValueError("credit_card subtype requires --type liability")
