from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import PolicyDenied, RateLimitExceeded


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 390_000
DUMMY_PASSWORD_HASH = "pbkdf2_sha256$390000$track-anywhere-dummy-auth$ad91c24505d228015b415c01bae7f8ee71cf56bf23d29ac8ffe07ca1589c65aa"
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 60


class PasswordAuthCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordLoginCommand(PasswordAuthCommand):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("email must be a valid email address")
        return email


class PasswordSignupCommand(PasswordLoginCommand):
    display_name: str | None = Field(default=None, max_length=150)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.strip().split())
        return text or None


@dataclass(frozen=True)
class PasswordAccount:
    email: str
    display_name: str
    password_hash: str
    role: str


class PasswordAccountRepository(Protocol):
    def create(self, *, email: str, display_name: str, password_hash: str) -> PasswordAccount: ...

    def get(self, email: str) -> PasswordAccount | None: ...


class PasswordAccountStore:
    def __init__(self, repository: PasswordAccountRepository) -> None:
        self._repository = repository

    def create(self, *, email: str, password: str, display_name: str | None) -> PasswordAccount:
        normalized = normalize_email(email)
        return self._repository.create(
            email=normalized,
            display_name=display_name or normalized.split("@", 1)[0],
            password_hash=_hash_password(password),
        )

    def authenticate(self, *, email: str, password: str) -> PasswordAccount:
        account = self._repository.get(normalize_email(email))
        encoded = account.password_hash if account is not None else DUMMY_PASSWORD_HASH
        password_valid = _verify_password(password, encoded)
        if account is None or not password_valid:
            raise PolicyDenied("email or password is incorrect")
        return account


class PasswordLoginLimiter:
    def __init__(
        self,
        *,
        failure_limit: int = LOGIN_FAILURE_LIMIT,
        window_seconds: int = LOGIN_FAILURE_WINDOW_SECONDS,
    ) -> None:
        self.failure_limit = failure_limit
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = monotonic()
        with self._lock:
            failures = self._active_failures(key, now)
            if len(failures) < self.failure_limit:
                return
            retry_after = max(1, ceil(self.window_seconds - (now - failures[0])))
            raise RateLimitExceeded("too many password login attempts", retry_after=retry_after)

    def record_failure(self, key: str) -> None:
        now = monotonic()
        with self._lock:
            failures = self._active_failures(key, now)
            failures.append(now)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _active_failures(self, key: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt, digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return hmac.compare_digest(candidate, digest)
