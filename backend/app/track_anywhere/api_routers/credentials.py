from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..commands import IssueCredentialCommand, RevokeCredentialCommand
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.post("/credentials/agent", dependencies=protected)
def issue_agent_credential(payload: IssueCredentialCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.issue_agent_credential_command(token, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.issue")


@router.post("/credentials/revoke", dependencies=protected)
def revoke_credential(payload: RevokeCredentialCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.revoke_credential_command(token, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.revoke")
