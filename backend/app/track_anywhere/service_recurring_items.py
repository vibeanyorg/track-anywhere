from __future__ import annotations

from .service_recurring_item_commands import RecurringItemCommandUseCases
from .service_recurring_item_queries import RecurringItemQueryUseCases
from .service_recurring_item_validation import RecurringItemValidationUseCases


class RecurringItemUseCases(
    RecurringItemCommandUseCases,
    RecurringItemQueryUseCases,
    RecurringItemValidationUseCases,
):
    pass
