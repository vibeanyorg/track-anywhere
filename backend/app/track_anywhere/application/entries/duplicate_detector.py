from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .contracts import PreparedEntryStatus


class DuplicateEvidenceKind(StrEnum):
    EXTERNAL_REFERENCE = "external_reference"
    SOURCE_FINGERPRINT = "source_fingerprint"
    SOFT_MATCH = "soft_match"


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    transaction_id: UUID
    evidence_kind: DuplicateEvidenceKind
    summary: str

    def __post_init__(self) -> None:
        if type(self.summary) is not str or not self.summary.strip():
            raise ValueError("duplicate candidate summary must be nonblank")


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    status: PreparedEntryStatus
    candidates: tuple[DuplicateCandidate, ...]

    def __post_init__(self) -> None:
        if self.status is PreparedEntryStatus.READY and self.candidates:
            raise ValueError("a ready duplicate decision cannot contain candidates")
        if self.status is PreparedEntryStatus.DUPLICATE_SUSPECTED and not self.candidates:
            raise ValueError("a duplicate-suspected decision requires candidates")
        if self.status not in {
            PreparedEntryStatus.READY,
            PreparedEntryStatus.DUPLICATE_SUSPECTED,
        }:
            raise ValueError("duplicate decision status is invalid")


def decide_duplicate(
    candidates: tuple[DuplicateCandidate, ...],
) -> DuplicateDecision:
    if type(candidates) is not tuple or any(
        type(candidate) is not DuplicateCandidate for candidate in candidates
    ):
        raise TypeError("duplicate candidates must be an immutable typed tuple")
    if not candidates:
        return DuplicateDecision(
            status=PreparedEntryStatus.READY,
            candidates=(),
        )
    priority = {
        DuplicateEvidenceKind.EXTERNAL_REFERENCE: 0,
        DuplicateEvidenceKind.SOURCE_FINGERPRINT: 1,
        DuplicateEvidenceKind.SOFT_MATCH: 2,
    }
    ordered = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                priority[candidate.evidence_kind],
                str(candidate.transaction_id),
            ),
        )
    )
    return DuplicateDecision(
        status=PreparedEntryStatus.DUPLICATE_SUSPECTED,
        candidates=ordered,
    )


__all__ = [
    "DuplicateCandidate",
    "DuplicateDecision",
    "DuplicateEvidenceKind",
    "decide_duplicate",
]
