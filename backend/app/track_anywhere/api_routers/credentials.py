from __future__ import annotations

from fastapi import APIRouter

from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_runtime import service
from ..api_serialization import serialize
from ..credential_commands import IssueCredentialCommand, IssueMachineCredentialCommand, RevokeCredentialByIdCommand, RevokeCredentialCommand
from .common import COMMAND_ERRORS, command_payload, protected


router = APIRouter()


@router.get("/credentials", dependencies=protected)
def list_credentials(token: AuthToken):
    try:
        return {"credentials": service.list_agent_credentials(token)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.list", recorder=service)


@router.get("/credentials/machine", dependencies=protected)
def list_machine_credentials(token: AuthToken):
    try:
        return {"credentials": service.list_agent_credentials(token)}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.machine.list", recorder=service)


@router.post("/credentials/agent", dependencies=protected)
def issue_agent_credential(payload: IssueCredentialCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.issue_agent_credential_command(token, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.issue", recorder=service)


@router.post("/credentials/machine", dependencies=protected)
def issue_machine_credential(payload: IssueMachineCredentialCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.issue_machine_credential_command(token, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.machine.issue", recorder=service)


@router.post("/credentials/revoke", dependencies=protected)
def revoke_credential(payload: RevokeCredentialCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.revoke_credential_command(token, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.revoke", recorder=service)


@router.post("/credentials/{credential_id}/revoke", dependencies=protected)
def revoke_credential_by_id(credential_id: str, payload: RevokeCredentialByIdCommand, token: AuthToken, key: IdempotencyKey):
    try:
        result, replay = service.revoke_credential_by_id_command(token, credential_id, command_payload(payload), idempotency_key=key)
        return {"credential": serialize(result), "idempotent_replay": replay}
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "credential.revoke_by_id", recorder=service)
