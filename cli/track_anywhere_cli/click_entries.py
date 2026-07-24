from __future__ import annotations

from argparse import Namespace
from typing import Any

import click

from .click_common import (
    ClickState,
    common_args,
    output_options,
    pass_state,
)
from .command_entries import (
    new_request_id,
    request_commit_entry,
    request_prepare_entry,
)
from .config import CliConfig, resolve_token_with_diagnostics
from .exit_codes import EXIT_AUTH, EXIT_EXTERNAL_DEPENDENCY
from .output import CliDiagnostic
from .renderers import emit_outcome
from .runtime import build_outcome


DENOMINATIONS = ("asset_unit", "minor_unit")


def register(root: click.Group) -> None:
    root.add_command(_expense_command(), "expense")
    root.add_command(_income_command(), "income")
    root.add_command(_transfer_command(), "transfer")
    root.add_command(_card_payment_command(), "card-pay")
    root.add_command(_refund_command(), "refund")
    root.add_command(_reconcile_command(), "reconcile")


def _expense_command() -> click.Command:
    @click.command()
    @click.argument("amount")
    @click.option(
        "--from",
        "source_account",
        required=True,
        help="Paying account name or id:<UUID>; this is not a category.",
    )
    @click.option("--from-last4", "source_last4")
    @click.option("--from-subtype", "source_subtype")
    @click.option(
        "--category",
        required=True,
        help="Expense category path or name, for example 食品/外卖.",
    )
    @_common_entry_options
    def expense(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit a categorized everyday expense."""
        return _invoke("expense", values, state=state)

    return expense


def _income_command() -> click.Command:
    @click.command()
    @click.argument("amount")
    @click.option("--to", "destination_account", required=True)
    @click.option("--to-last4", "destination_last4")
    @click.option("--to-subtype", "destination_subtype")
    @click.option(
        "--category",
        required=True,
        help="Income category path or name; this is not an account.",
    )
    @_common_entry_options
    def income(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit categorized everyday income."""
        return _invoke("income", values, state=state)

    return income


def _transfer_command() -> click.Command:
    @click.command()
    @click.argument("amount")
    @click.option("--from", "source_account", required=True)
    @click.option("--from-last4", "source_last4")
    @click.option("--from-subtype", "source_subtype")
    @click.option("--to", "destination_account", required=True)
    @click.option("--to-last4", "destination_last4")
    @click.option("--to-subtype", "destination_subtype")
    @_common_entry_options
    def transfer(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit a transfer between accounts."""
        return _invoke("transfer", values, state=state)

    return transfer


def _card_payment_command() -> click.Command:
    @click.command()
    @click.argument("amount")
    @click.option("--from", "funding_account", required=True)
    @click.option("--from-last4", "funding_last4")
    @click.option("--from-subtype", "funding_subtype", default="debit_card")
    @click.option("--card", "card_account", required=True)
    @click.option("--card-last4")
    @click.option("--card-subtype", default="credit_card")
    @_common_entry_options
    def card_payment(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit a credit-card payment."""
        return _invoke(
            "credit_card_payment",
            values,
            state=state,
            command_path="card-pay",
        )

    return card_payment


def _refund_command() -> click.Command:
    @click.command()
    @click.argument("amount", required=False)
    @click.option("--original", "original_transaction_id", required=True)
    @_common_entry_options
    def refund(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit a full or partial refund."""
        return _invoke("refund", values, state=state)

    return refund


def _reconcile_command() -> click.Command:
    @click.command()
    @click.argument("account")
    @click.option("--actual", "actual_balance", required=True)
    @click.option("--account-last4")
    @click.option("--account-subtype")
    @_common_entry_options
    def reconcile(state: ClickState, **values: Any) -> int:
        """Prepare and optionally commit a balance reconciliation."""
        return _invoke(
            "adjustment",
            values,
            state=state,
            command_path="reconcile",
        )

    return reconcile


def _common_entry_options(fn: Any) -> Any:
    fn = click.option(
        "--book-id",
        envvar="TRACK_ANYWHERE_BOOK_ID",
        help=(
            "Book UUID. Defaults to TRACK_ANYWHERE_BOOK_ID or the only "
            "accessible active Book."
        ),
    )(fn)
    fn = click.option("--asset-code", default="CNY", show_default=True)(fn)
    fn = click.option(
        "--denomination",
        type=click.Choice(DENOMINATIONS),
        default="asset_unit",
        show_default=True,
        help="Amount unit. Bare amounts default to the asset's main unit.",
    )(fn)
    fn = click.option(
        "--source-text",
        help="Original amount text for audit; defaults to the exact amount argument.",
    )(fn)
    fn = click.option("--at", "occurred_at", default="now", show_default=True)(fn)
    fn = click.option("--merchant")(fn)
    fn = click.option("--channel")(fn)
    fn = click.option("--note")(fn)
    fn = click.option(
        "--external-reference",
        metavar="PROVIDER:KIND:REFERENCE",
    )(fn)
    fn = click.option(
        "--dry-run",
        is_flag=True,
        help="Prepare and display the preview without committing.",
    )(fn)
    fn = click.option(
        "--yes",
        is_flag=True,
        help="Commit only a ready preview with no warnings.",
    )(fn)
    fn = output_options(fn)
    fn = pass_state(fn)
    return fn


def _invoke(
    entry_kind: str,
    values: dict[str, Any],
    *,
    state: ClickState,
    command_path: str | None = None,
) -> int:
    json_mode = values.pop("json_mode")
    no_color = values.pop("no_color")
    args = common_args(
        state,
        json_mode,
        no_color,
        entry_kind=entry_kind,
        **values,
    )
    return run_entry_workflow(
        args,
        state=state,
        command_path=command_path or entry_kind,
    )


def run_entry_workflow(
    args: Namespace,
    *,
    state: ClickState,
    command_path: str,
) -> int:
    output_json = bool(args.json)
    output_no_color = bool(args.no_color)
    config, auth_diagnostics, auth_error = _resolve_config(args, state)
    if auth_error is not None:
        outcome = build_outcome(
            command_path,
            401,
            {"detail": auth_error},
            exit_code=EXIT_AUTH,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    if args.book_id is None:
        try:
            selection_status, selection = _resolve_book_id(
                args,
                config=config,
                requester=state.requester,
            )
        except Exception as error:
            return _emit_request_failure(
                error,
                command_path=command_path,
                json_mode=output_json,
                no_color=output_no_color,
            )
        if selection_status >= 400:
            outcome = build_outcome(
                command_path,
                selection_status,
                selection,
                diagnostics=auth_diagnostics,
            )
            emit_outcome(
                outcome,
                json_mode=output_json,
                no_color=output_no_color,
            )
            return outcome.exit_code
        args.book_id = selection["book_id"]

    try:
        prepare_status, prepared = request_prepare_entry(
            args,
            config,
            state.requester,
        )
    except Exception as error:
        return _emit_request_failure(
            error,
            command_path=command_path,
            json_mode=output_json,
            no_color=output_no_color,
        )

    if prepare_status >= 400:
        outcome = build_outcome(
            command_path,
            prepare_status,
            prepared,
            diagnostics=auth_diagnostics,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    if not isinstance(prepared, dict):
        return _emit_invalid_response(
            command_path,
            prepared,
            json_mode=output_json,
            no_color=output_no_color,
        )

    should_commit = _commit_decision(args, prepared)
    if should_commit is None:
        outcome = build_outcome(
            command_path,
            prepare_status,
            prepared,
            diagnostics=[
                *auth_diagnostics,
                CliDiagnostic(
                    level="warning",
                    code="automatic_commit_blocked",
                    category="usage",
                    message=(
                        "--yes commits only when status is ready and warnings are empty."
                    ),
                    retryable=False,
                ),
            ],
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    if should_commit is False:
        outcome = build_outcome(
            command_path,
            prepare_status,
            prepared,
            diagnostics=auth_diagnostics,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    try:
        intent_id = _required_response_text(prepared, "intent_id")
        commit_token = _required_response_text(prepared, "commit_token")
        request_id = new_request_id()
        commit_status, committed = request_commit_entry(
            book_id=args.book_id,
            intent_id=intent_id,
            commit_token=commit_token,
            request_id=request_id,
            config=config,
            requester=state.requester,
        )
    except ValueError:
        return _emit_invalid_response(
            command_path,
            prepared,
            json_mode=output_json,
            no_color=output_no_color,
        )
    except Exception as error:
        return _emit_request_failure(
            error,
            command_path=command_path,
            json_mode=output_json,
            no_color=output_no_color,
        )

    outcome = build_outcome(
        command_path,
        commit_status,
        committed,
        diagnostics=auth_diagnostics,
    )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


def _commit_decision(args: Namespace, prepared: dict[str, Any]) -> bool | None:
    if not args.json and not args.agent_mode:
        _display_preview(prepared)

    if args.dry_run:
        return False

    ready = prepared.get("status") == "ready"
    warnings = prepared.get("warnings")
    warning_free = isinstance(warnings, list) and not warnings
    if args.yes:
        return True if ready and warning_free else None

    if not ready:
        return False
    if args.json or args.agent_mode or args.no_input:
        return False

    return click.confirm("Commit this entry?", default=False, err=True)


def _display_preview(prepared: dict[str, Any]) -> None:
    preview = prepared.get("preview")
    summary = preview.get("summary") if isinstance(preview, dict) else None
    click.echo(f"Preview: {summary or 'Unavailable'}", err=True)
    accounts = preview.get("accounts") if isinstance(preview, dict) else None
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            role = account.get("role")
            name = account.get("display_name")
            if isinstance(role, str) and isinstance(name, str):
                click.echo(f"Account ({role}): {name}", err=True)
    categories = (
        preview.get("category_paths") if isinstance(preview, dict) else None
    )
    if isinstance(categories, list):
        for path in categories:
            if isinstance(path, list) and all(
                isinstance(part, str) for part in path
            ):
                click.echo(f"Category: {'/'.join(path)}", err=True)
    warnings = prepared.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, dict):
                click.echo(
                    f"Warning: {warning.get('message', warning.get('code', 'warning'))}",
                    err=True,
                )


def _resolve_book_id(
    args: Namespace,
    *,
    config: CliConfig,
    requester,
) -> tuple[int, dict[str, Any]]:
    status, payload = requester(config, "GET", "/api/v2/books", None, None)
    if status >= 400:
        return status, payload
    items = payload.get("items") if isinstance(payload, dict) else None
    active = (
        [
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("book_id"), str)
            and item.get("book_id")
            and item.get("write_state") == "active"
        ]
        if isinstance(items, list)
        else []
    )
    if len(active) == 1:
        return 200, {"book_id": active[0]["book_id"]}
    choices = [
        {
            "book_id": item["book_id"],
            "current_name": item.get("current_name"),
        }
        for item in active
    ]
    if not active or args.json or args.agent_mode or args.no_input:
        return 422, {
            "detail": (
                "No active Book is available."
                if not active
                else "Multiple active Books are available; specify --book-id."
            ),
            "error": {
                "code": "book_selection_required",
                "category": "usage",
                "message": (
                    "No active Book is available."
                    if not active
                    else "Multiple active Books are available; specify --book-id."
                ),
                "retryable": False,
                "detail": {"choices": choices},
                "remediation": [
                    {
                        "description": "Choose a Book for this command.",
                        "command": [
                            "ta",
                            "<entry-command>",
                            "--book-id",
                            "<book-uuid>",
                        ],
                    }
                ],
            },
        }
    click.echo("Available Books:", err=True)
    for position, item in enumerate(active, start=1):
        click.echo(
            f"  {position}. {item.get('current_name') or 'Unnamed'} "
            f"({item['book_id']})",
            err=True,
        )
    selected = click.prompt(
        "Choose a Book",
        type=click.IntRange(1, len(active)),
        err=True,
    )
    return 200, {"book_id": active[selected - 1]["book_id"]}


def _resolve_config(
    args: Namespace,
    state: ClickState,
) -> tuple[CliConfig, list[CliDiagnostic], str | None]:
    try:
        resolution = resolve_token_with_diagnostics(args)
        from .oauth_login import refresh_token_resolution

        resolution = refresh_token_resolution(
            resolution,
            requester=state.requester,
        )
    except RuntimeError as error:
        return CliConfig(base_url=args.base_url), [], str(error)
    return (
        CliConfig(
            base_url=args.base_url,
            token=(
                resolution.credential.secret
                if resolution.credential is not None
                and resolution.credential.kind == "oauth"
                else None
            ),
            api_key=(
                resolution.credential.secret
                if resolution.credential is not None
                and resolution.credential.kind == "api_key"
                else None
            ),
            resource=getattr(args, "resource", None),
            insecure_automation=args.insecure_automation,
        ),
        resolution.diagnostics,
        None,
    )


def _required_response_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"prepare response is missing {field}")
    return value


def _emit_invalid_response(
    command_path: str,
    data: Any,
    *,
    json_mode: bool,
    no_color: bool,
) -> int:
    outcome = build_outcome(
        command_path,
        502,
        {
            "detail": "Everyday entry API returned an invalid response.",
            "error": {
                "code": "invalid_entry_response",
                "category": "external_dependency",
                "message": "Everyday entry API returned an invalid response.",
                "retryable": True,
                "detail": data,
            },
        },
    )
    emit_outcome(outcome, json_mode=json_mode, no_color=no_color)
    return outcome.exit_code


def _emit_request_failure(
    error: Exception,
    *,
    command_path: str,
    json_mode: bool,
    no_color: bool,
) -> int:
    outcome = build_outcome(
        command_path,
        503,
        {
            "detail": f"API command failed: {error}",
            "error": {
                "code": "api_request_failed",
                "category": "external_dependency",
                "message": f"API command failed: {error}",
                "retryable": True,
            },
        },
        exit_code=EXIT_EXTERNAL_DEPENDENCY,
    )
    emit_outcome(outcome, json_mode=json_mode, no_color=no_color)
    return outcome.exit_code


__all__ = ["register", "run_entry_workflow"]
