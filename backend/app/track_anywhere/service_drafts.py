from __future__ import annotations

from .service_draft_capture import DraftCaptureUseCases
from .service_draft_confirmation import DraftConfirmationUseCases
from .service_draft_lifecycle import DraftLifecycleUseCases
from .service_draft_store import DraftStoreUseCases


class DraftUseCases(
    DraftCaptureUseCases,
    DraftConfirmationUseCases,
    DraftLifecycleUseCases,
    DraftStoreUseCases,
):
    pass
