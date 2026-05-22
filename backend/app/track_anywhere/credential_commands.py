from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictCredentialCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["v1"] = "v1"


class IssueCredentialCommand(StrictCredentialCommand):
    scopes: list[str] = Field(min_length=1, max_length=12)
    ttl_minutes: int = Field(default=30, ge=1, le=24 * 60)


class IssueMachineCredentialCommand(StrictCredentialCommand):
    scopes: list[str] = Field(min_length=1, max_length=12)
    ttl_minutes: int = Field(default=30 * 24 * 60, ge=1, le=90 * 24 * 60)
    name: str = Field(default="Machine credential", min_length=1, max_length=120)
    description: str = Field(default="", max_length=240)
    credential_type: Literal["machine", "agent", "mcp", "ci", "integration"] = "machine"


class RevokeCredentialCommand(StrictCredentialCommand):
    target_token: str = Field(min_length=1, max_length=512)
    reason: str = Field(default="", max_length=240)


class RevokeCredentialByIdCommand(StrictCredentialCommand):
    reason: str = Field(default="", max_length=240)
