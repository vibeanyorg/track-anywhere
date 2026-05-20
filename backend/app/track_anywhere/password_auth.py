from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .errors import PolicyDenied, ValidationError
from .storage_models import PasswordAccountRecord


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 390_000


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


class PasswordAccountStore:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory
        self._accounts: dict[str, PasswordAccount] = {}

    def create(self, *, email: str, password: str, display_name: str | None) -> PasswordAccount:
        normalized = normalize_email(email)
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                if session.get(PasswordAccountRecord, normalized) is not None:
                    raise ValidationError("email is already registered")
                account = PasswordAccount(
                    email=normalized,
                    display_name=display_name or normalized.split("@", 1)[0],
                    password_hash=_hash_password(password),
                    role="owner" if _password_account_count(session) == 0 else "viewer",
                )
                session.add(
                    PasswordAccountRecord(
                        email=account.email,
                        display_name=account.display_name,
                        password_hash=account.password_hash,
                        role=account.role,
                        version=1,
                    )
                )
                return account
        if normalized in self._accounts:
            raise ValidationError("email is already registered")
        account = PasswordAccount(
            email=normalized,
            display_name=display_name or normalized.split("@", 1)[0],
            password_hash=_hash_password(password),
            role="owner" if not self._accounts else "viewer",
        )
        self._accounts[normalized] = account
        return account

    def authenticate(self, *, email: str, password: str) -> PasswordAccount:
        if self._session_factory is not None:
            with self._session_factory() as session:
                row = session.get(PasswordAccountRecord, normalize_email(email))
                if row is None or not _verify_password(password, row.password_hash):
                    raise PolicyDenied("email or password is incorrect")
                return PasswordAccount(
                    email=row.email,
                    display_name=row.display_name,
                    password_hash=row.password_hash,
                    role=row.role,
                )
        account = self._accounts.get(normalize_email(email))
        if account is None or not _verify_password(password, account.password_hash):
            raise PolicyDenied("email or password is incorrect")
        return account


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


def _password_account_count(session: Session) -> int:
    return int(session.query(func.count(PasswordAccountRecord.email)).scalar() or 0)
