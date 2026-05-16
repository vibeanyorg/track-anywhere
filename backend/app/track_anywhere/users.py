from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .errors import NotFound, ValidationError


@dataclass
class AppUser:
    user_id: str
    username: str
    display_name: str
    version: int = 1


class UserDirectory:
    def __init__(self) -> None:
        self.users: dict[str, AppUser] = {}

    def create(self, *, username: str, display_name: str | None = None) -> AppUser:
        if any(user.username == username for user in self.users.values()):
            raise ValidationError(f"user already exists: {username}")
        user = AppUser(
            user_id=f"user_{uuid4().hex}",
            username=username,
            display_name=display_name or username,
        )
        self.users[user.user_id] = user
        return user

    def get(self, user_id: str) -> AppUser:
        try:
            return self.users[user_id]
        except KeyError as exc:
            raise NotFound(f"user not found: {user_id}") from exc

    def list(self) -> list[AppUser]:
        return sorted(self.users.values(), key=lambda user: user.username)
