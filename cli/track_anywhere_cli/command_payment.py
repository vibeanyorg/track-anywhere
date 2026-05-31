from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
import urllib.parse

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_payment_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_payment_command_path(args)
    if command_path is None:
        return None
    handler = PAYMENT_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_payment_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "payment":
        return None
    payment_command = getattr(args, "payment_command", None)
    if payment_command == "profile" and getattr(args, "profile_command", None) in {"create", "list", "status"}:
        return f"payment.profile.{args.profile_command}"
    if payment_command == "instrument" and getattr(args, "instrument_command", None) in {"create", "list", "show"}:
        return f"payment.instrument.{args.instrument_command}"
    return None


def request_create_payment_profile(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    profile_payload = {
        "slug": args.slug,
        "display_name": args.display_name,
        "kind": args.kind.replace("-", "_"),
        "instrument_account_id": args.instrument_account_id,
        "backing_account_id": args.backing_account_id,
        "settlement_mode": args.settlement_mode,
        "settlement_rate": args.settlement_rate,
    }
    return requester(config, "POST", "/api/v1/payment-profiles", profile_payload, key=command_idempotency_key(args, "payment-profile-create"))


def request_list_payment_profiles(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    profile_query = with_query("/api/v1/payment-profiles", {"status": args.status})
    return requester(config, "GET", profile_query)


def request_payment_profile_status(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    payment_profile_ref = urllib.parse.quote(args.payment)
    return requester(config, "GET", f"/api/v1/payment-profiles/{payment_profile_ref}/status")


def request_create_payment_instrument(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    instrument_payload = {
        "slug": args.slug,
        "display_name": args.display_name,
        "kind": args.kind.replace("-", "_"),
        "account_id": args.account_id,
    }
    if args.last4:
        instrument_payload["last4"] = args.last4
    return requester(config, "POST", "/api/v1/payment-instruments", instrument_payload, key=command_idempotency_key(args, "payment-instrument-create"))


def request_list_payment_instruments(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    instrument_query = with_query("/api/v1/payment-instruments", {"account_id": args.account_id, "status": args.status})
    return requester(config, "GET", instrument_query)


def request_show_payment_instrument(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    instrument_ref = urllib.parse.quote(args.instrument_ref)
    return requester(config, "GET", f"/api/v1/payment-instruments/{instrument_ref}")


PAYMENT_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "payment.profile.create": request_create_payment_profile,
    "payment.profile.list": request_list_payment_profiles,
    "payment.profile.status": request_payment_profile_status,
    "payment.instrument.create": request_create_payment_instrument,
    "payment.instrument.list": request_list_payment_instruments,
    "payment.instrument.show": request_show_payment_instrument,
}
