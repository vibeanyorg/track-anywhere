from __future__ import annotations

from .service_recurring_drafts import RecurringDraftUseCases
from .service_recurring_items import RecurringItemUseCases
from .service_recurring_reminders import RecurringReminderUseCases


class RecurringUseCases(
    RecurringItemUseCases,
    RecurringReminderUseCases,
    RecurringDraftUseCases,
):
    pass
