from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..api_dependencies import AuthToken
from ..api_runtime import platform_key_exchange, service
from ..errors import PolicyDenied, ValidationError
from ..platform_auth import OAuthAuthorizeCommand, OAuthRegisterCommand, OAuthRevokeCommand, OAuthTokenCommand
from ..platform_auth_http import form_or_json_payload
from .common import protected


router = APIRouter(prefix="/oauth", tags=["oauth"])
TOKEN_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OAuthTokenCommand"}}},
    }
}
REVOKE_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OAuthRevokeCommand"}}},
    }
}


@router.get("/authorization-server")
def authorization_server_metadata(request: Request):
    return platform_key_exchange.authorization_server_metadata(_issuer_for(request))


@router.get("/protected-resource")
def protected_resource_metadata(request: Request):
    return platform_key_exchange.protected_resource_metadata(_issuer_for(request))


@router.get("/clients", dependencies=protected)
def list_clients(token: AuthToken):
    try:
        service.actor_from_token(token, "credential:write")
        return {"clients": platform_key_exchange.list_clients()}
    except PolicyDenied as exc:
        service.record_security_failure("oauth.client_list_denied", {"reason": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/register")
def register_client(payload: OAuthRegisterCommand):
    try:
        response = JSONResponse(platform_key_exchange.register_client(payload))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/authorize", dependencies=protected)
def authorize(payload: OAuthAuthorizeCommand, token: AuthToken):
    try:
        actor = service.actor_from_token(token)
        return platform_key_exchange.authorize(payload, actor)
    except PolicyDenied as exc:
        service.record_security_failure("oauth.authorize_denied", {"client_id": payload.client_id, "reason": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/token", openapi_extra=TOKEN_REQUEST_BODY)
async def token(request: Request):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        result = platform_key_exchange.exchange_code(OAuthTokenCommand.model_validate(payload), service)
    except PolicyDenied as exc:
        service.record_security_failure("oauth.token_denied", {"reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/revoke", openapi_extra=REVOKE_REQUEST_BODY)
async def revoke(request: Request):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        result = platform_key_exchange.revoke(OAuthRevokeCommand.model_validate(payload), service)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _issuer_for(request: Request) -> str:
    return str(request.base_url).rstrip("/")
