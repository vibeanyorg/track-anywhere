from __future__ import annotations


def add_recurring_parser(sub) -> None:
    recurring = sub.add_parser("recurring")
    recurring_sub = recurring.add_subparsers(dest="recurring_command", required=True)
    recurring_create = recurring_sub.add_parser("create")
    _add_item_fields(recurring_create)
    recurring_create.add_argument("--idempotency-key")
    recurring_create.add_argument("--json", action="store_true")

    recurring_list = recurring_sub.add_parser("list")
    recurring_list.add_argument("--status")
    recurring_list.add_argument("--kind", choices=("paid", "reminder_only"))
    recurring_list.add_argument("--json", action="store_true")

    recurring_show = recurring_sub.add_parser("show")
    recurring_show.add_argument("recurring_id")
    recurring_show.add_argument("--json", action="store_true")

    recurring_update = recurring_sub.add_parser("update")
    recurring_update.add_argument("recurring_id")
    recurring_update.add_argument("--status", choices=("active", "paused", "cancelled"))
    recurring_update.add_argument("--remind", dest="reminder_days", type=int, action="append")
    recurring_update.add_argument("--idempotency-key")
    recurring_update.add_argument("--json", action="store_true")

    recurring_reminders = recurring_sub.add_parser("reminders")
    recurring_reminders.add_argument("--as-of")
    recurring_reminders.add_argument("--window-days", type=int, default=0)
    recurring_reminders.add_argument("--json", action="store_true")

    recurring_draft_due = recurring_sub.add_parser("draft-due")
    recurring_draft_due.add_argument("--as-of")
    recurring_draft_due.add_argument("--idempotency-key")
    recurring_draft_due.add_argument("--json", action="store_true")


def _add_item_fields(parser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--kind", choices=("paid", "reminder_only"), required=True)
    parser.add_argument("--amount")
    parser.add_argument("--currency")
    parser.add_argument("--provider")
    parser.add_argument("--reference")
    recurrence = parser.add_mutually_exclusive_group(required=True)
    recurrence.add_argument("--monthly-day", type=int)
    recurrence.add_argument("--yearly-date", help="MM-DD")
    parser.add_argument("--anchor-date", required=True)
    parser.add_argument("--remind", dest="reminder_days", type=int, action="append", required=True)
    parser.add_argument("--source-account-id")
    parser.add_argument("--category-id")
