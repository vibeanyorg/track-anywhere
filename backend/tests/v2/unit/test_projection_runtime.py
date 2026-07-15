from __future__ import annotations

from uuid import UUID

from track_anywhere.infrastructure.projections.runtime import ProjectionRuntime
from track_anywhere.infrastructure.projections.worker import ProjectionRunResult


BOOK_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
BOOK_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _Coordinator:
    def __init__(self, lock_acquired: bool) -> None:
        self._scalar_results = iter((lock_acquired, True))
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def scalar(self, _statement: object) -> bool:
        return next(self._scalar_results)


class _Worker:
    def __init__(self, *, failing_book: UUID | None = None) -> None:
        self.failing_book = failing_book
        self.calls: list[UUID] = []

    def run_once(self, book_id: UUID) -> ProjectionRunResult:
        self.calls.append(book_id)
        if book_id == self.failing_book:
            raise RuntimeError("projection failed")
        return ProjectionRunResult(processed_events=3, last_book_position=3)


def test_cycle_uses_a_cluster_wide_leader_lock_and_processes_pending_books() -> None:
    coordinator = _Coordinator(True)
    worker = _Worker()
    runtime = ProjectionRuntime(
        lambda: coordinator,
        worker=worker,
        pending_book_ids=lambda _session: (BOOK_A, BOOK_B),
        poll_seconds=0.1,
    )

    processed = runtime.run_cycle()

    assert processed == 6
    assert worker.calls == [BOOK_A, BOOK_B]
    assert coordinator.closed is True


def test_cycle_is_idle_when_another_replica_holds_the_leader_lock() -> None:
    coordinator = _Coordinator(False)
    worker = _Worker()
    loader_called = False

    def pending_book_ids(_session: object) -> tuple[UUID, ...]:
        nonlocal loader_called
        loader_called = True
        return (BOOK_A,)

    runtime = ProjectionRuntime(
        lambda: coordinator,
        worker=worker,
        pending_book_ids=pending_book_ids,
        poll_seconds=0.1,
    )

    assert runtime.run_cycle() == 0
    assert loader_called is False
    assert worker.calls == []


def test_one_broken_book_does_not_starve_other_books() -> None:
    worker = _Worker(failing_book=BOOK_A)
    runtime = ProjectionRuntime(
        lambda: _Coordinator(True),
        worker=worker,
        pending_book_ids=lambda _session: (BOOK_A, BOOK_B),
        poll_seconds=0.1,
    )

    processed = runtime.run_cycle()

    assert processed == 3
    assert worker.calls == [BOOK_A, BOOK_B]


def test_poll_interval_is_bounded() -> None:
    for value in (0, -1, 301, float("inf"), float("nan")):
        try:
            ProjectionRuntime(lambda: _Coordinator(True), poll_seconds=value)
        except ValueError as error:
            assert "poll_seconds" in str(error)
        else:
            raise AssertionError(f"poll interval {value!r} was accepted")
