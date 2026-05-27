from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..api_dependencies import AuthToken
from ..api_runtime import platform_key_exchange, service
from ..errors import PolicyDenied, ValidationError
from ..platform_auth import (
    DEVICE_GRANT_TYPE,
    OAuthAuthorizeCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthDeviceTokenCommand,
    OAuthRegisterCommand,
    OAuthRevokeCommand,
    OAuthTokenCommand,
    OAuthTokenError,
)
from ..platform_auth_metadata import (
    authorization_server_metadata as build_authorization_server_metadata,
    protected_resource_metadata as build_protected_resource_metadata,
)
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
    return build_authorization_server_metadata(_issuer_for(request))


@router.get("/protected-resource")
def protected_resource_metadata(request: Request):
    return build_protected_resource_metadata(_issuer_for(request))


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
        return service.authorize_platform_oauth(platform_key_exchange, payload, actor)
    except PolicyDenied as exc:
        service.record_security_failure("oauth.authorize_denied", {"client_id": payload.client_id, "reason": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/token", openapi_extra=TOKEN_REQUEST_BODY)
async def token(request: Request):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        if payload.get("grant_type") == DEVICE_GRANT_TYPE:
            result = service.exchange_platform_device_code(platform_key_exchange, OAuthDeviceTokenCommand.model_validate(payload))
        else:
            result = service.exchange_platform_code(platform_key_exchange, OAuthTokenCommand.model_validate(payload))
    except OAuthTokenError as exc:
        service.record_security_failure("oauth.token_denied", {"reason": exc.error})
        response = JSONResponse({"error": exc.error, "error_description": exc.description, **exc.extra}, status_code=400)
        response.headers["Cache-Control"] = "no-store"
        return response
    except PolicyDenied as exc:
        service.record_security_failure("oauth.token_denied", {"reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/device/authorize")
def device_authorize(payload: OAuthDeviceAuthorizeCommand, request: Request):
    try:
        response = JSONResponse(service.create_platform_device_authorization(platform_key_exchange, payload, _issuer_for(request)))
    except (PolicyDenied, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/revoke", openapi_extra=REVOKE_REQUEST_BODY)
async def revoke(request: Request):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        result = service.revoke_platform_token(platform_key_exchange, OAuthRevokeCommand.model_validate(payload))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _issuer_for(request: Request) -> str:
    return str(request.base_url).rstrip("/")
