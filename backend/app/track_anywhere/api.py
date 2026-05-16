from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

try:
    from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import ValidationError as PydanticValidationError
except ModuleNotFoundError:  # pragma: no cover - lets core tests run before deps are installed
    FastAPI = None  # type: ignore[assignment]

from .errors import IdempotencyConflict, NotFound, PolicyDenied, SecurityPreconditionFailed, StaleVersion, TrackAnywhereError
from .security import BrowserSessionStore, DeploymentSecurityConfig, validate_web_security
from .service import FinanceService


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _deployment_config_from_env() -> DeploymentSecurityConfig:
    return DeploymentSecurityConfig(
        mode=os.getenv("TRACK_ANYWHERE_MODE", "local"),
        tls_enabled=_env_bool("TRACK_ANYWHERE_TLS"),
        key_provider_configured=_env_bool("TRACK_ANYWHERE_KEY_PROVIDER"),
        encrypted_volume_documented=_env_bool("TRACK_ANYWHERE_ENCRYPTED_VOLUME"),
        backup_encryption_documented=_env_bool("TRACK_ANYWHERE_BACKUP_DOC"),
        attachment_scanner_available=_env_bool(
            "TRACK_ANYWHERE_ATTACHMENT_SCANNER",
            default=os.getenv("TRACK_ANYWHERE_MODE", "local") == "local",
        ),
        debug_raw_payload=_env_bool("TRACK_ANYWHERE_DEBUG_RAW_PAYLOAD"),
        local_dev_no_scan=_env_bool("TRACK_ANYWHERE_LOCAL_DEV_NO_SCAN"),
    )


def _allowed_origins() -> tuple[str, ...]:
    raw = os.getenv("TRACK_ANYWHERE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    return origins or ("http://localhost:3000",)


service = FinanceService(_deployment_config_from_env())
browser_sessions = BrowserSessionStore()
ALLOWED_ORIGINS = _allowed_origins()


def _allowed_origin_for_request(origin: str | None, referer: str | None) -> str:
    if origin in ALLOWED_ORIGINS:
        return origin
    for allowed_origin in ALLOWED_ORIGINS:
        if referer and referer.startswith(allowed_origin):
            return allowed_origin
    return ALLOWED_ORIGINS[0]


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _error_to_status(error: Exception) -> int:
    if isinstance(error, PolicyDenied):
        return 403
    if isinstance(error, SecurityPreconditionFailed):
        return 400
    if isinstance(error, IdempotencyConflict | StaleVersion):
        return 409
    if isinstance(error, NotFound):
        return 404
    return 422


def _raise_command_error(error: Exception, operation: str) -> None:
    if isinstance(error, PolicyDenied):
        service.record_security_failure("security.policy_denied", {"operation": operation})
    elif isinstance(error, IdempotencyConflict):
        service.record_security_failure("command.idempotency_conflict", {"operation": operation})
    elif isinstance(error, StaleVersion):
        service.record_security_failure("command.stale_version", {"operation": operation})
    elif isinstance(error, PydanticValidationError):
        service.record_security_failure("command.validation_failed", {"operation": operation, "error_count": error.error_count()})
    raise HTTPException(status_code=_error_to_status(error), detail=str(error))


if FastAPI is not None:
    app = FastAPI(title="Track Anywhere API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Idempotency-Key", "X-CSRF-Token"],
    )

    async def token_from_header(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        return authorization.removeprefix("Bearer ").strip()

    async def idempotency_key(x_idempotency_key: str | None = Header(default=None)) -> str:
        if not x_idempotency_key:
            raise HTTPException(status_code=400, detail="missing idempotency key")
        return x_idempotency_key

    async def session_guard(
        request: Request,
        x_csrf_token: str | None = Header(default=None),
        origin: str | None = Header(default=None),
        referer: str | None = Header(default=None),
    ) -> None:
        session_id = request.cookies.get("ta_session")
        auth_mode = "session" if session_id else "bearer"
        if session_id and not browser_sessions.verify_csrf(session_id, x_csrf_token):
            service.record_security_failure("security.csrf_denied", {"path": request.url.path, "origin": origin})
            raise HTTPException(status_code=400, detail="missing or invalid CSRF token")
        allowed_origin = _allowed_origin_for_request(origin, referer)
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and auth_mode == "bearer":
            origin_ok = origin in ALLOWED_ORIGINS if origin else True
            referer_ok = any(referer and referer.startswith(item) for item in ALLOWED_ORIGINS)
            if not origin_ok or (referer and not referer_ok):
                service.record_security_failure("security.origin_denied", {"path": request.url.path, "origin": origin, "referer": referer})
                raise HTTPException(status_code=400, detail="missing or invalid Origin/Referer")
        try:
            validate_web_security(
                method=request.method,
                auth_mode=auth_mode,
                csrf_token="verified" if auth_mode == "session" else None,
                expected_csrf_token="verified" if auth_mode == "session" else None,
                origin=origin,
                referer=referer,
                allowed_origin=allowed_origin,
            )
        except SecurityPreconditionFailed as exc:
            service.record_security_failure("security.origin_denied", {"path": request.url.path, "origin": origin, "referer": referer})
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "api_version": "v1"}

    @app.post("/api/v1/session/dev-local")
    async def create_local_session(response: Response):
        session_id, csrf_token = browser_sessions.issue()
        secure_cookie = service.config.mode != "local"
        response.set_cookie(
            "ta_session",
            session_id,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
        )
        return {"csrf_token": csrf_token, "cookie": {"http_only": True, "secure": secure_cookie, "same_site": "strict"}}

    @app.post("/api/v1/auth/dev-token")
    async def issue_local_dev_token():
        if service.config.mode != "local":
            raise HTTPException(status_code=403, detail="dev token is only available in local mode")
        actor = service.actor_from_token(service.owner_token)
        return {
            "token": service.owner_token,
            "actor": {
                "actor_id": actor.actor_id,
                "actor_type": actor.actor_type,
                "scopes": sorted(actor.scopes),
            },
        }

    @app.get("/api/v1/accounts", dependencies=[Depends(session_guard)])
    async def list_accounts(
        name: str | None = None,
        type: str | None = None,
        currency: str | None = None,
        institution_type: str | None = None,
        subtype: str | None = None,
        institution: str | None = None,
        token: str = Depends(token_from_header),
    ):
        try:
            return {
                "accounts": _serialize(
                    service.list_accounts(
                        token,
                        name=name,
                        type=type,
                        currency=currency,
                        institution_type=institution_type,
                        subtype=subtype,
                        institution=institution,
                    )
                )
            }
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "account.list")

    @app.post("/api/v1/accounts", dependencies=[Depends(session_guard)])
    async def create_account(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            account, replay = service.create_account(token, payload, idempotency_key=key)
            return {"account": _serialize(account), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "account.create")

    @app.get("/api/v1/accounts/{account_id}", dependencies=[Depends(session_guard)])
    async def get_account(account_id: str, token: str = Depends(token_from_header)):
        try:
            return {"account": _serialize(service.get_account(token, account_id))}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "account.get")

    @app.get("/api/v1/summary/accounts", dependencies=[Depends(session_guard)])
    async def account_summary(
        group_by: str = "subtype",
        currency: str | None = None,
        institution_type: str | None = None,
        include_system: bool = False,
        token: str = Depends(token_from_header),
    ):
        try:
            return service.account_summary(
                token,
                group_by=group_by,
                currency=currency,
                institution_type=institution_type,
                include_system=include_system,
            )
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "summary.accounts")

    @app.patch("/api/v1/accounts/{account_id}", dependencies=[Depends(session_guard)])
    async def update_account_metadata(
        account_id: str,
        payload: dict[str, Any],
        token: str = Depends(token_from_header),
        key: str = Depends(idempotency_key),
    ):
        try:
            account, replay = service.update_account_metadata(token, account_id, payload, idempotency_key=key)
            return {"account": _serialize(account), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "account.metadata.update")

    @app.get("/api/v1/users", dependencies=[Depends(session_guard)])
    async def list_users(token: str = Depends(token_from_header)):
        try:
            return {"users": _serialize(service.list_users(token))}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "user.list")

    @app.post("/api/v1/users", dependencies=[Depends(session_guard)])
    async def create_user(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            user, replay = service.create_user(token, payload, idempotency_key=key)
            return {"user": _serialize(user), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "user.create")

    @app.post("/api/v1/drafts/capture", dependencies=[Depends(session_guard)])
    async def capture_draft(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            draft, replay = service.capture_draft(token, payload, idempotency_key=key)
            return {"draft": _serialize(draft), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "draft.capture")

    @app.get("/api/v1/ledger/transactions", dependencies=[Depends(session_guard)])
    async def list_transactions(
        account_id: str | None = None,
        limit: int = 20,
        token: str = Depends(token_from_header),
    ):
        try:
            return {"transactions": _serialize(service.list_transactions(token, account_id=account_id, limit=limit))}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "ledger.transaction.list")

    @app.post("/api/v1/ledger/transactions", dependencies=[Depends(session_guard)])
    async def record_transaction(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            transaction, replay = service.record_transaction(token, payload, idempotency_key=key)
            return {"transaction": _serialize(transaction), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "ledger.transaction.record")

    @app.get("/api/v1/ledger/transactions/{transaction_id}", dependencies=[Depends(session_guard)])
    async def get_transaction(transaction_id: str, token: str = Depends(token_from_header)):
        try:
            return {"transaction": _serialize(service.get_transaction(token, transaction_id))}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "ledger.transaction.get")

    @app.post("/api/v1/ledger/adjustments", dependencies=[Depends(session_guard)])
    async def adjust_balance(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            transaction, replay = service.adjust_balance(token, payload, idempotency_key=key)
            return {"transaction": _serialize(transaction), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "ledger.balance.adjust")

    @app.post("/api/v1/investments/events", dependencies=[Depends(session_guard)])
    async def record_investment_event(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            event, replay = service.record_investment_event(token, payload, idempotency_key=key)
            return {"event": _serialize(event), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "investment.event.record")

    @app.get("/api/v1/investments/accounts/{account_id}/performance", dependencies=[Depends(session_guard)])
    async def investment_performance(account_id: str, as_of: str | None = None, token: str = Depends(token_from_header)):
        try:
            return service.investment_performance(token, account_id, as_of=as_of)
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "investment.performance")

    @app.post("/api/v1/drafts/confirm", dependencies=[Depends(session_guard)])
    async def confirm_draft(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            transaction, replay = service.confirm_draft(token, payload, idempotency_key=key)
            return {"transaction": _serialize(transaction), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "draft.confirm")

    @app.post("/api/v1/drafts/reject", dependencies=[Depends(session_guard)])
    async def reject_draft(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            draft, replay = service.reject_draft(token, payload, idempotency_key=key)
            return {"draft": _serialize(draft), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "draft.reject")

    @app.post("/api/v1/drafts/supersede", dependencies=[Depends(session_guard)])
    async def supersede_draft(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            draft, replay = service.supersede_draft(token, payload, idempotency_key=key)
            return {"draft": _serialize(draft), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "draft.supersede")

    @app.post("/api/v1/funds", dependencies=[Depends(session_guard)])
    async def create_fund(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            fund, replay = service.create_fund(token, payload, idempotency_key=key)
            return {"fund": _serialize(fund), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "fund.create")

    @app.post("/api/v1/funds/allocate", dependencies=[Depends(session_guard)])
    async def allocate_fund(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            result, replay = service.allocate_fund(token, payload, idempotency_key=key)
            return {"result": _serialize(result), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "fund.allocate")

    @app.post("/api/v1/funds/spend", dependencies=[Depends(session_guard)])
    async def spend_fund(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            result, replay = service.spend_fund(token, payload, idempotency_key=key)
            return {"result": _serialize(result), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "fund.spend")

    @app.post("/api/v1/ledger/reverse", dependencies=[Depends(session_guard)])
    async def reverse_transaction(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            transaction, replay = service.reverse_transaction(token, payload, idempotency_key=key)
            return {"transaction": _serialize(transaction), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "ledger.reverse")

    @app.post("/api/v1/attachments", dependencies=[Depends(session_guard)])
    async def upload_attachment(
        file: UploadFile = File(...),
        token: str = Depends(token_from_header),
        key: str = Depends(idempotency_key),
    ):
        try:
            result, replay = service.upload_attachment(
                token,
                filename=file.filename or "attachment",
                mime_type=file.content_type or "application/octet-stream",
                content=await file.read(),
                idempotency_key=key,
            )
            return {"result": _serialize(result), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "attachment.upload")

    @app.post("/api/v1/credentials/agent", dependencies=[Depends(session_guard)])
    async def issue_agent_credential(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            result, replay = service.issue_agent_credential_command(token, payload, idempotency_key=key)
            return {"credential": _serialize(result), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "credential.issue")

    @app.post("/api/v1/credentials/revoke", dependencies=[Depends(session_guard)])
    async def revoke_credential(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            result, replay = service.revoke_credential_command(token, payload, idempotency_key=key)
            return {"credential": _serialize(result), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "credential.revoke")

    @app.post("/api/v1/reconciliation/actions", dependencies=[Depends(session_guard)])
    async def record_reconciliation(payload: dict[str, Any], token: str = Depends(token_from_header), key: str = Depends(idempotency_key)):
        try:
            action, replay = service.record_reconciliation_action(token, payload, idempotency_key=key)
            return {"action": _serialize(action), "idempotent_replay": replay}
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "reconciliation.record")

    @app.get("/api/v1/query/accounts/{account_id}/balance", dependencies=[Depends(session_guard)])
    async def account_balance(account_id: str, include_drafts: bool = False, token: str = Depends(token_from_header)):
        try:
            return service.account_balance(token, account_id, include_drafts=include_drafts)
        except (TrackAnywhereError, PydanticValidationError) as exc:
            _raise_command_error(exc, "account.balance")

else:
    app = None
