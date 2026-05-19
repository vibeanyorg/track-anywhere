from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .api_routes import router
from .api_runtime import ALLOWED_ORIGINS, _deployment_config_from_env, auth_settings, service


def create_app() -> FastAPI:
    app = FastAPI(title="Track Anywhere API", version="0.1.0")
    if auth_settings.session_secret:
        app.add_middleware(
            SessionMiddleware,
            secret_key=auth_settings.session_secret,
            same_site="strict",
            https_only=service.config.mode != "local",
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Idempotency-Key", "X-CSRF-Token"],
    )
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        service.record_security_failure(
            "command.validation_failed",
            {"path": request.url.path, "method": request.method, "error_count": len(exc.errors())},
        )
        detail = [
            {"type": error.get("type"), "loc": error.get("loc"), "msg": error.get("msg")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    return app


app = create_app()

__all__ = ["app", "create_app", "service", "_deployment_config_from_env"]
