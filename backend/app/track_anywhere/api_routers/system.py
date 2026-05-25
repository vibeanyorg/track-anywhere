from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from ..api_dependencies import AuthToken
from ..api_sessions import set_browser_session_cookies
from ..api_runtime import auth_cookie_secure, browser_sessions, service
from ..db_migrations import ALEMBIC_DIR, ALEMBIC_INI
from .common import protected


router = APIRouter()

STATUS_COUNT_TABLES = (
    "ledger_books",
    "book_members",
    "accounts",
    "assets",
    "categories",
    "category_versions",
    "transaction_lines",
    "transactions",
    "postings",
    "audit_events",
    "idempotency_receipts",
)


@router.get("/health")
def health():
    return {"status": "ok", "api_version": "v1"}


@router.get("/ready")
def ready():
    try:
        state = _database_readiness()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "checks": {"database": "error"},
                "detail": type(exc).__name__,
            },
        )
    expected_revision = _current_alembic_head()
    checks = {
        "database": "ok",
        "migrations": "ok" if state["alembic_revision"] == expected_revision else "error",
    }
    status_code = 200 if all(value == "ok" for value in checks.values()) else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if status_code == 200 else "error",
            "api_version": "v1",
            "database": state["database"],
            "schema": state["schema"],
            "alembic_revision": state["alembic_revision"],
            "expected_revision": expected_revision,
            "checks": checks,
        },
    )


@router.get("/system/status", dependencies=protected)
def system_status(token: AuthToken, include_counts: bool = False):
    actor = service.actor_from_token(token, required_scope="ledger:read")
    state = _database_readiness()
    expected_revision = _current_alembic_head()
    checks = {
        "database": "ok",
        "migrations": "ok" if state["alembic_revision"] == expected_revision else "error",
    }
    payload = {
        "status": "ok" if all(value == "ok" for value in checks.values()) else "error",
        "api_version": "v1",
        "database": state["database"],
        "schema": state["schema"],
        "alembic_revision": state["alembic_revision"],
        "expected_revision": expected_revision,
        "checks": checks,
        "actor": {
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "scopes": sorted(actor.scopes),
        },
    }
    if include_counts:
        payload["counts"] = _table_counts()
    return payload


@router.post("/session/dev-local")
def create_local_session(response: Response):
    if service.config.mode != "local":
        raise HTTPException(status_code=403, detail="dev session is only available in local mode")
    session_id, csrf_token = browser_sessions.issue(
        credential_token=service.owner_token,
        identity={"provider": "local", "subject": "owner", "email": None, "name": "Local Owner"},
    )
    secure_cookie = auth_cookie_secure()
    set_browser_session_cookies(response, session_id=session_id, csrf_token=csrf_token, secure=secure_cookie)
    return {
        "csrf_token": csrf_token,
        "cookie": {"http_only": True, "secure": secure_cookie, "same_site": "strict"},
    }


def _database_readiness() -> dict[str, str | None]:
    with service.storage.engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            database = service.storage.engine.url.database or "sqlite"
            schema = None
        else:
            database = connection.execute(text("select current_database()")).scalar_one_or_none()
            schema = connection.execute(text("select current_schema()")).scalar_one_or_none()
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one_or_none()
        return {"database": database, "schema": schema, "alembic_revision": revision}


def _table_counts() -> dict[str, int]:
    with service.storage.engine.connect() as connection:
        return {
            table_name: int(connection.execute(text(f'select count(*) from "{table_name}"')).scalar_one())
            for table_name in STATUS_COUNT_TABLES
        }


def _current_alembic_head() -> str:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return str(ScriptDirectory.from_config(config).get_current_head())


@router.post("/auth/dev-token")
def issue_local_dev_token():
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
