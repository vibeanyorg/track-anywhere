from __future__ import annotations

import json
from argparse import Namespace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from track_anywhere.posting_semantics import (
    POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
)

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_system_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_system_command_path(args)
    if command_path is None:
        return None
    handler = SYSTEM_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_system_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "system":
        return None
    system_command = getattr(args, "system_command", None)
    if system_command == "status":
        return "system.status"
    if system_command == "posting-semantics":
        posting_semantics_command = getattr(args, "posting_semantics_command", None)
        if posting_semantics_command == "audit":
            return "system.posting_semantics.audit"
        if posting_semantics_command == "cutover-plan":
            return "system.posting_semantics.cutover_plan"
        if posting_semantics_command == "rewrite":
            return "system.posting_semantics.rewrite"
        if posting_semantics_command == "resolve":
            return "system.posting_semantics.resolve"
    return None


def request_system_status(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    status_query = with_query("/api/v1/system/status", {"include_counts": "true" if args.include_counts else None})
    return requester(config, "GET", status_query)


def request_posting_semantics_audit(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", _posting_semantics_path("/api/v1/system/posting-semantics-audit", args))


def request_posting_semantics_cutover_plan(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(config, "GET", _posting_semantics_path("/api/v1/system/posting-semantics-cutover-plan", args))


def request_posting_semantics_rewrite(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(
        config,
        "POST",
        _posting_semantics_path("/api/v1/system/posting-semantics-rewrite", args),
        {},
        key=command_idempotency_key(args, "posting-semantics-rewrite"),
    )


def request_posting_semantics_resolve(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    try:
        payload = {"decisions": _load_review_decisions(args)}
    except ValueError as exc:
        return 422, {
            "detail": str(exc),
            "error": {
                "code": "invalid_posting_semantics_review_decisions",
                "category": "validation",
                "message": str(exc),
                "retryable": False,
            },
        }
    return requester(
        config,
        "POST",
        _posting_semantics_path("/api/v1/system/posting-semantics-review-resolutions", args),
        payload,
        key=command_idempotency_key(args, "posting-semantics-resolve"),
    )


def _posting_semantics_path(path: str, args: Namespace) -> str:
    return with_query(path, {"book_id": getattr(args, "book_id", None)})


def _load_review_decisions(args: Namespace) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for raw_decision in getattr(args, "decision_json", ()) or ():
        _extend_review_decisions(decisions, _loads_review_decision(raw_decision, "decision-json"))
    decision_file = getattr(args, "decision_file", None)
    if decision_file:
        raw_file = Path(decision_file).read_text(encoding="utf-8")
        _extend_review_decisions(decisions, _loads_review_decision(raw_file, f"decision-file {decision_file}"))
    if not decisions:
        raise ValueError("Provide at least one --decision-json or --decision-file review decision.")
    return decisions


def _loads_review_decision(raw_value: str, source: str) -> Any:
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {source} JSON: {exc}") from exc


def _extend_review_decisions(decisions: list[dict[str, Any]], parsed: Any) -> None:
    if isinstance(parsed, dict) and isinstance(parsed.get("decisions"), list):
        unexpected = set(parsed) - {"decisions"}
        if unexpected:
            raise ValueError(f"Review decision envelope has unsupported fields: {', '.join(sorted(unexpected))}.")
        for item in parsed["decisions"]:
            _append_review_decision(decisions, item)
        return
    if isinstance(parsed, list):
        for item in parsed:
            _append_review_decision(decisions, item)
        return
    _append_review_decision(decisions, parsed)


def _append_review_decision(decisions: list[dict[str, Any]], item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("Each posting semantics review decision must be a JSON object.")
    allowed = set(POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS)
    unsupported = set(item) - allowed
    if unsupported:
        forbidden = unsupported & set(POSTING_SEMANTICS_REVIEW_DECISION_FORBIDDEN_FIELDS)
        if forbidden:
            raise ValueError(
                "Posting semantics review decisions must not include raw, derived, or read-only recommendation fields: "
                f"{', '.join(sorted(forbidden))}."
            )
        raise ValueError(
            "Posting semantics review decisions contain unsupported fields: "
            f"{', '.join(sorted(unsupported))}."
        )
    record_ref = item.get("record_ref")
    transaction_id = item.get("transaction_id")
    if record_ref is not None and (not isinstance(record_ref, str) or not record_ref):
        raise ValueError("record_ref must be a non-empty string when provided.")
    if transaction_id is not None and (not isinstance(transaction_id, str) or not transaction_id):
        raise ValueError("transaction_id must be a non-empty string when provided.")
    if not record_ref and not transaction_id:
        raise ValueError("Each posting semantics review decision requires record_ref or transaction_id.")
    if record_ref and transaction_id and record_ref != transaction_id:
        raise ValueError("record_ref and transaction_id must match when both are provided.")
    position = item.get("position")
    if type(position) is not int or position < 0:
        raise ValueError("Each posting semantics review decision requires position as a non-negative integer.")
    for field in ("account_id", "currency", "legacy_amount", "action"):
        value = item.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Each posting semantics review decision requires {field} as a non-empty string.")
    try:
        legacy_amount = Decimal(item["legacy_amount"])
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("legacy_amount must be a decimal string.") from exc
    if legacy_amount == Decimal("0"):
        raise ValueError("legacy_amount must not be zero.")
    if item["action"] not in {"confirm_as_outstanding_liability", "confirm_as_liability_reduction_or_overpayment"}:
        raise ValueError(
            "action must be confirm_as_outstanding_liability or "
            "confirm_as_liability_reduction_or_overpayment."
        )
    decisions.append(item)


SYSTEM_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "system.status": request_system_status,
    "system.posting_semantics.audit": request_posting_semantics_audit,
    "system.posting_semantics.cutover_plan": request_posting_semantics_cutover_plan,
    "system.posting_semantics.rewrite": request_posting_semantics_rewrite,
    "system.posting_semantics.resolve": request_posting_semantics_resolve,
}
