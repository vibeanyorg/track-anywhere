from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..api_dependencies import AuthToken
from ..api_ports.oauth import OAuthService
from ..errors import PolicyDenied, ValidationError
from ..platform_auth import (
    OAuthAuthorizeCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthRegisterCommand,
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
def list_clients(token: AuthToken, service: OAuthService):
    try:
        return {"clients": service.list_platform_oauth_clients(token)}
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/register")
def register_client(payload: OAuthRegisterCommand, service: OAuthService):
    try:
        response = JSONResponse(service.register_platform_oauth_client(payload))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/authorize", dependencies=protected)
def authorize(payload: OAuthAuthorizeCommand, token: AuthToken, service: OAuthService):
    try:
        return service.authorize_platform_oauth_request(token, payload)
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/token", openapi_extra=TOKEN_REQUEST_BODY)
async def token(request: Request, service: OAuthService):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        result = service.exchange_platform_oauth_token_payload(payload)
    except OAuthTokenError as exc:
        response = JSONResponse({"error": exc.error, "error_description": exc.description, **exc.extra}, status_code=400)
        response.headers["Cache-Control"] = "no-store"
        return response
    except PolicyDenied as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/device/authorize")
def device_authorize(payload: OAuthDeviceAuthorizeCommand, request: Request, service: OAuthService):
    try:
        response = JSONResponse(service.create_platform_device_authorization_request(payload, _issuer_for(request)))
    except (PolicyDenied, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/revoke", openapi_extra=REVOKE_REQUEST_BODY)
async def revoke(request: Request, service: OAuthService):
    try:
        payload = form_or_json_payload(request.headers.get("content-type", ""), await request.body())
        result = service.revoke_platform_oauth_token_payload(payload)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _issuer_for(request: Request) -> str:
    return str(request.base_url).rstrip("/")
