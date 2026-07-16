from __future__ import annotations


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_auth_throttle_limits_each_subject_and_refills() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    clock = _Clock()
    throttle = InMemoryAuthThrottle(
        client_capacity=10,
        client_refill_per_second=1,
        subject_capacity=2,
        subject_refill_per_second=0.1,
        clock=clock,
    )

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    assert throttle.check("198.51.100.10", "login:owner@example.test") == 10

    clock.advance(10)

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None


def test_rotating_subjects_exhaust_only_the_originating_client() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    throttle = InMemoryAuthThrottle(
        client_capacity=2,
        client_refill_per_second=0.2,
        subject_capacity=10,
        subject_refill_per_second=1,
    )

    assert throttle.check("198.51.100.10", "login:first@example.test") is None
    assert throttle.check("198.51.100.10", "login:second@example.test") is None
    assert throttle.check("198.51.100.10", "login:third@example.test") == 5
    assert throttle.check("203.0.113.20", "login:fourth@example.test") is None


def test_same_subject_is_limited_across_clients() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    throttle = InMemoryAuthThrottle(
        client_capacity=10,
        client_refill_per_second=1,
        subject_capacity=2,
        subject_refill_per_second=0.1,
    )

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    assert throttle.check("203.0.113.20", "login:owner@example.test") is None
    assert throttle.check("192.0.2.30", "login:owner@example.test") == 10


def test_blocked_composite_check_does_not_consume_the_other_budget() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    throttle = InMemoryAuthThrottle(
        client_capacity=2,
        client_refill_per_second=0.1,
        subject_capacity=1,
        subject_refill_per_second=0.1,
    )

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    assert throttle.check("198.51.100.10", "login:owner@example.test") == 10

    throttle.reset("login:owner@example.test")

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    throttle.reset("login:owner@example.test")
    assert throttle.check("198.51.100.10", "login:owner@example.test") == 10


def test_reset_clears_only_the_subject_budget() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    throttle = InMemoryAuthThrottle(
        client_capacity=1,
        client_refill_per_second=0.1,
        subject_capacity=1,
        subject_refill_per_second=0.1,
    )

    assert throttle.check("198.51.100.10", "login:owner@example.test") is None
    throttle.reset("login:owner@example.test")

    assert throttle.check("198.51.100.10", "login:owner@example.test") == 10


def test_subject_lru_eviction_cannot_reset_active_client_abuse_budget() -> None:
    from track_anywhere.auth.throttle import InMemoryAuthThrottle

    throttle = InMemoryAuthThrottle(
        client_capacity=2,
        client_refill_per_second=0.1,
        subject_capacity=10,
        subject_refill_per_second=1,
        max_subjects=1,
    )

    assert throttle.check("198.51.100.10", "login:first@example.test") is None
    assert throttle.check("198.51.100.10", "login:second@example.test") is None
    assert throttle.check("198.51.100.10", "login:third@example.test") == 10
