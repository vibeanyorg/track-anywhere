from __future__ import annotations

import click

from track_anywhere.posting_semantics import (
    POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
    POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS,
)

from .click_common import common_args, output_options, pass_state, run_api


def register(root: click.Group) -> None:
    @root.group()
    def system():
        """Inspect the running API service."""

    @system.command("status")
    @click.option("--include-counts", is_flag=True)
    @output_options
    @pass_state
    def system_status(state, json_mode, no_color, include_counts):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="status",
            include_counts=include_counts,
        )
        return run_api(args, state=state, command_path="system.status")

    @system.group("posting-semantics")
    def posting_semantics():
        """Audit and migrate posting semantics."""

    @posting_semantics.command("audit")
    @click.option("--book-id", default="book_default", show_default=True)
    @output_options
    @pass_state
    def posting_semantics_audit(state, json_mode, no_color, book_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="posting-semantics",
            posting_semantics_command="audit",
            book_id=book_id,
        )
        return run_api(args, state=state, command_path="system.posting_semantics.audit")

    @posting_semantics.command("cutover-plan")
    @click.option("--book-id", default="book_default", show_default=True)
    @output_options
    @pass_state
    def posting_semantics_cutover_plan(state, json_mode, no_color, book_id):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="posting-semantics",
            posting_semantics_command="cutover-plan",
            book_id=book_id,
        )
        return run_api(args, state=state, command_path="system.posting_semantics.cutover_plan")

    @posting_semantics.command("rewrite")
    @click.option("--book-id", default="book_default", show_default=True)
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def posting_semantics_rewrite(state, json_mode, no_color, book_id, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="posting-semantics",
            posting_semantics_command="rewrite",
            book_id=book_id,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="system.posting_semantics.rewrite")

    @posting_semantics.command("resolve")
    @click.option("--book-id", default="book_default", show_default=True)
    @click.option(
        "--decision-json",
        multiple=True,
        help=(
            "Review decision JSON object, array, or {'decisions': [...]} envelope. "
            f"Allowed decision fields: {', '.join(POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS)}. "
            f"Do not pass {', '.join(POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS)} "
            f"or read-only recommendation fields such as "
            f"{', '.join(POSTING_SEMANTICS_REVIEW_RECOMMENDATION_READ_ONLY_FIELDS)}."
        ),
    )
    @click.option(
        "--decision-file",
        type=click.Path(dir_okay=False, exists=True),
        help="JSON file with one or more review decisions using the same safe fields as --decision-json.",
    )
    @click.option("--idempotency-key")
    @output_options
    @pass_state
    def posting_semantics_resolve(state, json_mode, no_color, book_id, decision_json, decision_file, idempotency_key):
        args = common_args(
            state,
            json_mode,
            no_color,
            command="system",
            system_command="posting-semantics",
            posting_semantics_command="resolve",
            book_id=book_id,
            decision_json=decision_json,
            decision_file=decision_file,
            idempotency_key=idempotency_key,
        )
        return run_api(args, state=state, command_path="system.posting_semantics.resolve")
