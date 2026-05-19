from __future__ import annotations

from typing import Any, Callable

from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.urls import NoReverseMatch, reverse
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError as NinjaValidationError
from pydantic import ValidationError as PydanticValidationError

from track_anywhere.api_serialization import serialize
from track_anywhere.attachments import MAX_ATTACHMENT_BYTES
from track_anywhere.commands import (
    BalanceAdjustmentCommand,
    CaptureDraftCommand,
    ConfirmDraftCommand,
    CreateAccountCommand,
    CreateCategoryCommand,
    CreateFundCommand,
    CreateRecurringItemCommand,
    CreateUserCommand,
    FundAllocationCommand,
    FundSpendCommand,
    GenerateRecurringDraftsCommand,
    IssueCredentialCommand,
    RecordExpenseCommand,
    RecordIncomeCommand,
    RecordInvestmentEventCommand,
    RecordTransactionCommand,
    ReconciliationActionCommand,
    RejectDraftCommand,
    RevokeCredentialCommand,
    RevokeCredentialByIdCommand,
    ReverseTransactionCommand,
    SupersedeDraftCommand,
    UpdateAccountMetadataCommand,
    UpdateCreditCardProfileCommand,
    UpdateRecurringItemCommand,
)
from track_anywhere.domain_commands import (
    AddCategoryAliasCommand,
    CreateBookCommand,
    CreateBudgetCommand,
    CreateBudgetTargetCommand,
    MergeCategoryCommand,
    ReverseBookTransactionCommand,
    UpdateCategoryCommand,
)
from track_anywhere.errors import IdempotencyConflict, NotFound, PolicyDenied, SecurityPreconditionFailed, StaleVersion, TrackAnywhereError, ValidationError
from track_anywhere.platform_auth import (
    ApiKeySessionCommand,
    OAuthAuthorizeCommand,
    OAuthRegisterCommand,
    OAuthRevokeCommand,
    OAuthTokenCommand,
)
from track_anywhere.platform_auth_http import form_or_json_payload, identity_for_actor
from track_anywhere.password_auth import PasswordLoginCommand, PasswordSignupCommand
from track_anywhere.security import CredentialReference, validate_web_security

from .auth_bridge import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_browser_session_cookies,
    configured_allauth_providers,
    credential_for_browser_session,
    credential_for_django_user,
    ensure_browser_session_for_django_user,
    revoke_browser_session_for_request,
    set_browser_session_cookies,
)
from .runtime import ALLOWED_ORIGINS, auth_settings, browser_sessions, platform_key_exchange, service
from .password_auth import login_password_session, signup_password_session


COMMAND_ERRORS = (TrackAnywhereError, PydanticValidationError)

api = NinjaAPI(
    title="Track Anywhere API",
    version="0.1.0",
    urls_namespace="track_anywhere_django_api",
)


def command_payload(command) -> dict[str, Any]:
    return command.model_dump(mode="python")


def error_to_status(error: Exception) -> int:
    if isinstance(error, PolicyDenied):
        return 403
    if isinstance(error, SecurityPreconditionFailed):
        return 400
    if isinstance(error, IdempotencyConflict | StaleVersion):
        return 409
    if isinstance(error, NotFound):
        return 404
    return 422


def raise_command_error(error: Exception, operation: str) -> None:
    if isinstance(error, PolicyDenied):
        service.record_security_failure("security.policy_denied", {"operation": operation})
    elif isinstance(error, IdempotencyConflict):
        service.record_security_failure("command.idempotency_conflict", {"operation": operation})
    elif isinstance(error, StaleVersion):
        service.record_security_failure("command.stale_version", {"operation": operation})
    elif isinstance(error, PydanticValidationError):
        service.record_security_failure("command.validation_failed", {"operation": operation, "error_count": error.error_count()})
    raise HttpError(error_to_status(error), str(error))


def protected(request: HttpRequest) -> str | CredentialReference:
    session_guard(request)
    return token_from_request(request)


def token_from_request(request: HttpRequest) -> str | CredentialReference:
    api_key = request.headers.get("X-API-Key")
    if api_key and api_key.strip():
        return api_key.strip()

    authorization = request.headers.get("Authorization")
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HttpError(401, "invalid authorization header")
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            return token

    session_credential = credential_for_browser_session(request)
    if session_credential:
        return session_credential
    django_credential = credential_for_django_user(request)
    if django_credential:
        return django_credential
    raise HttpError(401, "missing bearer token or session")


def idempotency_key(request: HttpRequest) -> str:
    key = request.headers.get("X-Idempotency-Key")
    if not key:
        raise HttpError(400, "missing idempotency key")
    return key


def allowed_origin_for_request(origin: str | None, referer: str | None) -> str:
    if origin in ALLOWED_ORIGINS:
        return origin
    for allowed_origin in ALLOWED_ORIGINS:
        if referer and referer.startswith(allowed_origin):
            return allowed_origin
    return ALLOWED_ORIGINS[0]


def _issuer_for(request: HttpRequest) -> str:
    return request.build_absolute_uri("/").rstrip("/")


def session_guard(request: HttpRequest) -> None:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    session_id = request.COOKIES.get(SESSION_COOKIE)
    user = getattr(request, "user", None)
    has_django_session = bool(user is not None and user.is_authenticated)
    auth_mode = "session" if session_id or has_django_session else "bearer"
    is_mutating = request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    if is_mutating and auth_mode == "session":
        if not session_id or not browser_sessions.verify_csrf(session_id, request.headers.get("X-CSRF-Token")):
            service.record_security_failure("security.csrf_denied", {"path": request.path, "origin": origin})
            raise HttpError(400, "missing or invalid CSRF token")
    allowed_origin = allowed_origin_for_request(origin, referer)
    if is_mutating and auth_mode == "bearer":
        origin_ok = origin in ALLOWED_ORIGINS if origin else True
        referer_ok = any(referer and referer.startswith(item) for item in ALLOWED_ORIGINS)
        if not origin_ok or (referer and not referer_ok):
            service.record_security_failure("security.origin_denied", {"path": request.path, "origin": origin, "referer": referer})
            raise HttpError(400, "missing or invalid Origin/Referer")
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
        service.record_security_failure("security.origin_denied", {"path": request.path, "origin": origin, "referer": referer})
        raise HttpError(400, str(exc)) from exc


def run(operation: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return action()
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, operation)


@api.exception_handler(NinjaValidationError)
def validation_error_handler(request: HttpRequest, exc: NinjaValidationError):
    raw_errors = getattr(exc, "errors", [])
    if callable(raw_errors):
        raw_errors = raw_errors()
    detail = [
        {"type": error.get("type"), "loc": error.get("loc"), "msg": error.get("msg")}
        for error in raw_errors
        if isinstance(error, dict)
    ]
    service.record_security_failure(
        "command.validation_failed",
        {"path": request.path, "method": request.method, "error_count": len(detail)},
    )
    return api.create_response(request, {"detail": detail}, status=422)


@api.get("/health")
def health(request: HttpRequest):
    return {"status": "ok", "api_version": "v1"}


@api.post("/auth/dev-token")
def issue_local_dev_token(request: HttpRequest):
    if service.config.mode != "local":
        raise HttpError(403, "dev token is only available in local mode")
    actor = service.actor_from_token(service.owner_token)
    return {
        "token": service.owner_token,
        "actor": {
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "scopes": sorted(actor.scopes),
        },
    }


@api.post("/session/dev-local")
def create_local_session(request: HttpRequest):
    session_id, csrf_token = browser_sessions.issue(
        credential_token=service.owner_token,
        identity={"provider": "local", "subject": "owner", "email": None, "name": "Local Owner"},
    )
    secure_cookie = service.config.mode != "local"
    response = JsonResponse(
        {
            "csrf_token": csrf_token,
            "cookie": {"http_only": True, "secure": secure_cookie, "same_site": "strict"},
        }
    )
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=secure_cookie, samesite="Strict")
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, secure=secure_cookie, samesite="Strict")
    return response


@api.get("/auth/session")
def current_session(request: HttpRequest):
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        browser_session = ensure_browser_session_for_django_user(request)
        identity = request.session.get("track_anywhere_identity") or {
            "provider": "django",
            "subject": str(request.user.pk),
            "email": request.user.email or None,
            "name": request.user.get_full_name() or request.user.get_username(),
        }
        response = {"authenticated": True, "identity": identity}
        if browser_session is not None:
            response["csrf_token"] = browser_session[1]
        return response
    session_id = request.COOKIES.get(SESSION_COOKIE)
    identity = browser_sessions.identity_for(session_id)
    credential = browser_sessions.credential_for(session_id)
    if identity is None or credential is None:
        return {"authenticated": False, "identity": None}
    try:
        service.actor_from_token(credential)
    except TrackAnywhereError:
        return {"authenticated": False, "identity": None}
    return {"authenticated": True, "identity": identity}


@api.post("/auth/logout")
def logout(request: HttpRequest):
    revoke_browser_session_for_request(request)
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        django_logout(request)
    response = JsonResponse({"authenticated": False})
    clear_browser_session_cookies(response)
    return response


@api.post("/auth/session/api-key")
def create_api_key_session(request: HttpRequest, payload: ApiKeySessionCommand):
    try:
        actor = service.actor_from_token(payload.api_key)
    except PolicyDenied as exc:
        service.record_security_failure("auth.api_key_denied", {"reason": str(exc)})
        raise HttpError(401, "API key is invalid or expired") from exc

    identity = identity_for_actor(actor, provider="api-key")
    session_id, csrf_token = browser_sessions.issue(
        credential_token=payload.api_key,
        identity=identity,
    )
    response = JsonResponse({"authenticated": True, "csrf_token": csrf_token, "identity": identity})
    set_browser_session_cookies(response, session_id=session_id, csrf_token=csrf_token, secure=service.config.mode != "local")
    return response


@api.post("/auth/password/signup")
def signup_with_password(request: HttpRequest, payload: PasswordSignupCommand):
    return signup_password_session(request, payload)


@api.post("/auth/password/login")
def login_with_password(request: HttpRequest, payload: PasswordLoginCommand):
    return login_password_session(request, payload)


@api.get("/auth/oauth/providers")
def list_oauth_providers(request: HttpRequest):
    providers = {provider.name: {"name": provider.name, "display_name": provider.display_name} for provider in auth_settings.providers}
    providers.update({provider["name"]: provider for provider in configured_allauth_providers()})
    return {"providers": [providers[name] for name in sorted(providers)]}


@api.get("/auth/oauth/{provider}/authorize")
def oauth_authorize(request: HttpRequest, provider: str):
    allauth_provider_names = {item["name"] for item in configured_allauth_providers()}
    if provider in allauth_provider_names:
        try:
            return HttpResponseRedirect(reverse(f"{provider}_login"))
        except NoReverseMatch as exc:
            raise HttpError(503, "OAuth provider is unavailable in allauth") from exc
    if not any(item.name == provider for item in auth_settings.providers):
        raise HttpError(404, "OAuth provider is not configured")
    raise HttpError(503, "OAuth provider is unavailable in the Django sidecar")


@api.get("/auth/oauth/{provider}/callback")
def oauth_callback(request: HttpRequest, provider: str):
    allauth_provider_names = {item["name"] for item in configured_allauth_providers()}
    if provider in allauth_provider_names:
        callback_path = f"/accounts/{provider}/login/callback/"
        query = request.META.get("QUERY_STRING")
        return HttpResponseRedirect(f"{callback_path}?{query}" if query else callback_path)
    if not any(item.name == provider for item in auth_settings.providers):
        raise HttpError(404, "OAuth provider is not configured")
    raise HttpError(503, "OAuth provider is unavailable in the Django sidecar")


@api.get("/oauth/authorization-server")
def authorization_server_metadata(request: HttpRequest):
    return platform_key_exchange.authorization_server_metadata(_issuer_for(request))


@api.get("/oauth/protected-resource")
def protected_resource_metadata(request: HttpRequest):
    return platform_key_exchange.protected_resource_metadata(_issuer_for(request))


@api.get("/oauth/clients")
def list_oauth_clients(request: HttpRequest):
    try:
        token = protected(request)
        service.actor_from_token(token, "credential:write")
        return {"clients": platform_key_exchange.list_clients()}
    except PolicyDenied as exc:
        service.record_security_failure("oauth.client_list_denied", {"reason": str(exc)})
        raise HttpError(403, str(exc)) from exc


@api.post("/oauth/register")
def register_oauth_client(request: HttpRequest, payload: OAuthRegisterCommand):
    try:
        response = JsonResponse(platform_key_exchange.register_client(payload))
    except ValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return response


@api.post("/oauth/authorize")
def authorize_oauth_client(request: HttpRequest, payload: OAuthAuthorizeCommand):
    try:
        token = protected(request)
        actor = service.actor_from_token(token)
        return platform_key_exchange.authorize(payload, actor)
    except PolicyDenied as exc:
        service.record_security_failure("oauth.authorize_denied", {"client_id": payload.client_id, "reason": str(exc)})
        raise HttpError(403, str(exc)) from exc
    except ValidationError as exc:
        raise HttpError(400, str(exc)) from exc


@api.post("/oauth/token")
def exchange_oauth_token(request: HttpRequest):
    try:
        payload = form_or_json_payload(request.headers.get("Content-Type", ""), request.body)
        result = platform_key_exchange.exchange_code(OAuthTokenCommand.model_validate(payload), service)
    except PolicyDenied as exc:
        service.record_security_failure("oauth.token_denied", {"reason": str(exc)})
        raise HttpError(400, str(exc)) from exc
    except (PydanticValidationError, ValidationError, ValueError) as exc:
        raise HttpError(400, str(exc)) from exc
    response = JsonResponse(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@api.post("/oauth/revoke")
def revoke_oauth_token(request: HttpRequest):
    try:
        payload = form_or_json_payload(request.headers.get("Content-Type", ""), request.body)
        result = platform_key_exchange.revoke(OAuthRevokeCommand.model_validate(payload), service)
    except (PydanticValidationError, ValidationError, ValueError) as exc:
        raise HttpError(400, str(exc)) from exc
    response = JsonResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@api.get("/accounts")
def list_accounts(
    request: HttpRequest,
    name: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    institution_type: str | None = None,
    subtype: str | None = None,
    institution: str | None = None,
):
    token = protected(request)
    return run(
        "account.list",
        lambda: {
            "accounts": serialize(
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
        },
    )


@api.post("/accounts")
def create_account(request: HttpRequest, payload: CreateAccountCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "account.create",
        lambda: (lambda account, replay: {"account": serialize(account), "idempotent_replay": replay})(
            *service.create_account(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/accounts/{account_id}")
def get_account(request: HttpRequest, account_id: str):
    token = protected(request)
    return run("account.get", lambda: {"account": serialize(service.get_account(token, account_id))})


@api.patch("/accounts/{account_id}")
def update_account_metadata(request: HttpRequest, account_id: str, payload: UpdateAccountMetadataCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "account.metadata.update",
        lambda: (lambda account, replay: {"account": serialize(account), "idempotent_replay": replay})(
            *service.update_account_metadata(token, account_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/summary/accounts")
def account_summary(
    request: HttpRequest,
    group_by: str = "subtype",
    currency: str | None = None,
    institution_type: str | None = None,
    include_system: bool = False,
):
    token = protected(request)
    return run(
        "summary.accounts",
        lambda: service.account_summary(token, group_by=group_by, currency=currency, institution_type=institution_type, include_system=include_system),
    )


@api.get("/summary/categories")
def category_summary(request: HttpRequest, kind: str | None = None, currency: str | None = None):
    token = protected(request)
    return run("summary.categories", lambda: service.category_summary(token, kind=kind, currency=currency))


@api.get("/users")
def list_users(request: HttpRequest):
    token = protected(request)
    return run("user.list", lambda: {"users": serialize(service.list_users(token))})


@api.post("/users")
def create_user(request: HttpRequest, payload: CreateUserCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "user.create",
        lambda: (lambda user, replay: {"user": serialize(user), "idempotent_replay": replay})(
            *service.create_user(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/categories")
def list_categories(request: HttpRequest, kind: str | None = None, primary: str | None = None, secondary: str | None = None):
    token = protected(request)
    return run("category.list", lambda: {"categories": serialize(service.list_categories(token, kind=kind, primary=primary, secondary=secondary))})


@api.post("/categories")
def create_category(request: HttpRequest, payload: CreateCategoryCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "category.create",
        lambda: (lambda category, replay: {"category": serialize(category), "idempotent_replay": replay})(
            *service.create_category(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/categories/{category_id}")
def get_category(request: HttpRequest, category_id: str):
    token = protected(request)
    return run("category.get", lambda: {"category": serialize(service.get_category(token, category_id))})


@api.get("/credit-cards")
def list_credit_cards(request: HttpRequest):
    token = protected(request)
    return run("credit_card.list", lambda: {"credit_cards": serialize(service.list_credit_cards(token))})


@api.get("/credit-cards/{account_id}")
def get_credit_card(request: HttpRequest, account_id: str):
    token = protected(request)
    return run("credit_card.get", lambda: {"credit_card": serialize(service.get_credit_card(token, account_id))})


@api.patch("/credit-cards/{account_id}")
def update_credit_card_profile(request: HttpRequest, account_id: str, payload: UpdateCreditCardProfileCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "credit_card.profile.update",
        lambda: (lambda credit_card, replay: {"credit_card": serialize(credit_card), "idempotent_replay": replay})(
            *service.update_credit_card_profile(token, account_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/drafts/capture")
def capture_draft(request: HttpRequest, payload: CaptureDraftCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "draft.capture",
        lambda: (lambda draft, replay: {"draft": serialize(draft), "idempotent_replay": replay})(
            *service.capture_draft(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/drafts/confirm")
def confirm_draft(request: HttpRequest, payload: ConfirmDraftCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "draft.confirm",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.confirm_draft(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/drafts/reject")
def reject_draft(request: HttpRequest, payload: RejectDraftCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "draft.reject",
        lambda: (lambda draft, replay: {"draft": serialize(draft), "idempotent_replay": replay})(
            *service.reject_draft(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/drafts/supersede")
def supersede_draft(request: HttpRequest, payload: SupersedeDraftCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "draft.supersede",
        lambda: (lambda draft, replay: {"draft": serialize(draft), "idempotent_replay": replay})(
            *service.supersede_draft(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/ledger/transactions")
def list_transactions(request: HttpRequest, account_id: str | None = None, category_id: str | None = None, limit: int = 20):
    token = protected(request)
    return run(
        "ledger.transaction.list",
        lambda: {"transactions": serialize(service.list_transactions(token, account_id=account_id, category_id=category_id, limit=limit))},
    )


@api.post("/ledger/transactions")
def record_transaction(request: HttpRequest, payload: RecordTransactionCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "ledger.transaction.record",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.record_transaction(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/ledger/transactions/{transaction_id}")
def get_transaction(request: HttpRequest, transaction_id: str):
    token = protected(request)
    return run("ledger.transaction.get", lambda: {"transaction": serialize(service.get_transaction(token, transaction_id))})


@api.post("/expenses")
def record_expense(request: HttpRequest, payload: RecordExpenseCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "expense.record",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.record_expense(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/incomes")
def record_income(request: HttpRequest, payload: RecordIncomeCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "income.record",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.record_income(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/ledger/adjustments")
def adjust_balance(request: HttpRequest, payload: BalanceAdjustmentCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "ledger.balance.adjust",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.adjust_balance(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/ledger/reverse")
def reverse_transaction(request: HttpRequest, payload: ReverseTransactionCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "ledger.reverse",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.reverse_transaction(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/query/accounts/{account_id}/balance")
def account_balance(request: HttpRequest, account_id: str, include_drafts: bool = False):
    token = protected(request)
    return run("account.balance", lambda: service.account_balance(token, account_id, include_drafts=include_drafts))


@api.get("/books")
def list_books(request: HttpRequest):
    token = protected(request)
    return run("book.list", lambda: {"books": serialize(service.list_books(token))})


@api.post("/books")
def create_book(request: HttpRequest, payload: CreateBookCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.create",
        lambda: (lambda book, replay: {"book": serialize(book), "idempotent_replay": replay})(
            *service.create_book(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}")
def get_book(request: HttpRequest, book_id: str):
    token = protected(request)
    return run("book.get", lambda: {"book": serialize(service.get_book(token, book_id))})


@api.get("/books/{book_id}/accounts")
def list_book_accounts(request: HttpRequest, book_id: str):
    token = protected(request)
    return run("book.account.list", lambda: {"accounts": serialize(service.list_book_accounts(token, book_id))})


@api.post("/books/{book_id}/accounts")
def create_book_account(request: HttpRequest, book_id: str, payload: CreateAccountCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.account.create",
        lambda: (lambda account, replay: {"account": serialize(account), "idempotent_replay": replay})(
            *service.create_book_account(token, book_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}/transactions")
def list_book_transactions(request: HttpRequest, book_id: str, limit: int = 20):
    token = protected(request)
    return run("book.transaction.list", lambda: {"transactions": serialize(service.list_book_transactions(token, book_id, limit=limit))})


@api.post("/books/{book_id}/transactions")
def record_book_transaction(request: HttpRequest, book_id: str, payload: RecordTransactionCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.transaction.record",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.record_book_transaction(token, book_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/books/{book_id}/transactions/{transaction_id}/reverse")
def reverse_book_transaction(request: HttpRequest, book_id: str, transaction_id: str, payload: ReverseBookTransactionCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.transaction.reverse",
        lambda: (lambda transaction, replay: {"transaction": serialize(transaction), "idempotent_replay": replay})(
            *service.reverse_book_transaction(token, book_id, {"transaction_id": transaction_id, **command_payload(payload)}, idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}/categories")
def list_book_categories(request: HttpRequest, book_id: str, kind: str | None = None):
    token = protected(request)
    return run("book.category.list", lambda: {"categories": serialize(service.list_book_categories(token, book_id, kind=kind))})


@api.post("/books/{book_id}/categories")
def create_book_category(request: HttpRequest, book_id: str, payload: CreateCategoryCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.category.create",
        lambda: (lambda category, replay: {"category": serialize(category), "idempotent_replay": replay})(
            *service.create_book_category(token, book_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.patch("/books/{book_id}/categories/{category_id}")
def update_book_category(request: HttpRequest, book_id: str, category_id: str, payload: UpdateCategoryCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.category.update",
        lambda: (lambda category, replay: {"category": serialize(category), "idempotent_replay": replay})(
            *service.update_book_category(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/books/{book_id}/categories/{category_id}/aliases")
def add_book_category_alias(request: HttpRequest, book_id: str, category_id: str, payload: AddCategoryAliasCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.category.alias.add",
        lambda: (lambda alias, replay: {"alias": serialize(alias), "idempotent_replay": replay})(
            *service.add_book_category_alias(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/books/{book_id}/categories/{category_id}/merge")
def merge_book_category(request: HttpRequest, book_id: str, category_id: str, payload: MergeCategoryCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.category.merge",
        lambda: (lambda category, replay: {"category": serialize(category), "idempotent_replay": replay})(
            *service.merge_book_category(token, book_id, category_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}/classification-events")
def list_book_classification_events(request: HttpRequest, book_id: str):
    token = protected(request)
    return run("book.classification_event.list", lambda: {"events": serialize(service.list_book_classification_events(token, book_id))})


@api.get("/books/{book_id}/budgets")
def list_book_budgets(request: HttpRequest, book_id: str):
    token = protected(request)
    return run("book.budget.list", lambda: {"budgets": serialize(service.list_budgets(token, book_id))})


@api.post("/books/{book_id}/budgets")
def create_book_budget(request: HttpRequest, book_id: str, payload: CreateBudgetCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.budget.create",
        lambda: (lambda budget, replay: {"budget": serialize(budget), "idempotent_replay": replay})(
            *service.create_budget(token, book_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/books/{book_id}/budgets/{budget_id}/targets")
def add_book_budget_target(request: HttpRequest, book_id: str, budget_id: str, payload: CreateBudgetTargetCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.budget.target.add",
        lambda: (lambda target, replay: {"budget_target": serialize(target), "idempotent_replay": replay})(
            *service.add_budget_target(token, book_id, budget_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}/budgets/{budget_id}/targets")
def list_book_budget_targets(request: HttpRequest, book_id: str, budget_id: str):
    token = protected(request)
    return run("book.budget.target.list", lambda: {"budget_targets": serialize(service.list_budget_targets(token, book_id, budget_id))})


@api.get("/books/{book_id}/budgets/{budget_id}/execution")
def get_book_budget_execution(request: HttpRequest, book_id: str, budget_id: str):
    token = protected(request)
    return run("book.budget.execution", lambda: service.budget_execution_report(token, book_id, budget_id))


@api.get("/books/{book_id}/reports/spending")
def book_spending_report(request: HttpRequest, book_id: str, group_by: str = "category_parent", currency: str | None = None):
    token = protected(request)
    return run("book.report.spending", lambda: service.spending_report(token, book_id, group_by=group_by, currency=currency))


@api.post("/recurring/items")
def create_recurring_item(request: HttpRequest, payload: CreateRecurringItemCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "recurring.item.create",
        lambda: (lambda item, replay: {"recurring_item": serialize(item), "idempotent_replay": replay})(
            *service.create_recurring_item(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/recurring/items")
def list_recurring_items(request: HttpRequest, status: str | None = None, kind: str | None = None):
    token = protected(request)
    return run("recurring.item.list", lambda: {"recurring_items": serialize(service.list_recurring_items(token, status=status, kind=kind))})


@api.get("/recurring/items/{recurring_id}")
def get_recurring_item(request: HttpRequest, recurring_id: str):
    token = protected(request)
    return run("recurring.item.get", lambda: {"recurring_item": serialize(service.get_recurring_item(token, recurring_id))})


@api.patch("/recurring/items/{recurring_id}")
def update_recurring_item(request: HttpRequest, recurring_id: str, payload: UpdateRecurringItemCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "recurring.item.update",
        lambda: (lambda item, replay: {"recurring_item": serialize(item), "idempotent_replay": replay})(
            *service.update_recurring_item(token, recurring_id, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/recurring/reminders")
def recurring_reminders(request: HttpRequest, as_of: str | None = None, window_days: int = 0):
    token = protected(request)
    return run("recurring.reminders.check", lambda: service.check_recurring_reminders(token, as_of=as_of, window_days=window_days))


@api.post("/recurring/drafts")
def generate_recurring_drafts(request: HttpRequest, payload: GenerateRecurringDraftsCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "recurring.draft.generate",
        lambda: (lambda result, replay: {"result": serialize(result), "idempotent_replay": replay})(
            *service.generate_recurring_drafts(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/books/{book_id}/recurring/items")
def create_book_recurring_item(request: HttpRequest, book_id: str, payload: CreateRecurringItemCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.recurring.item.create",
        lambda: (lambda item, replay: {"recurring_item": serialize(item), "idempotent_replay": replay})(
            *service.create_recurring_item(token, {**command_payload(payload), "book_id": book_id}, idempotency_key=key)
        ),
    )


@api.get("/books/{book_id}/recurring/items")
def list_book_recurring_items(request: HttpRequest, book_id: str, status: str | None = None, kind: str | None = None):
    token = protected(request)
    return run(
        "book.recurring.item.list",
        lambda: {"recurring_items": serialize(service.list_recurring_items(token, status=status, kind=kind, book_id=book_id))},
    )


@api.get("/books/{book_id}/recurring/reminders")
def book_recurring_reminders(request: HttpRequest, book_id: str, as_of: str | None = None, window_days: int = 0):
    token = protected(request)
    return run(
        "book.recurring.reminders.check",
        lambda: service.check_recurring_reminders(token, as_of=as_of, window_days=window_days, book_id=book_id),
    )


@api.post("/books/{book_id}/recurring/drafts")
def generate_book_recurring_drafts(request: HttpRequest, book_id: str, payload: GenerateRecurringDraftsCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "book.recurring.draft.generate",
        lambda: (lambda result, replay: {"result": serialize(result), "idempotent_replay": replay})(
            *service.generate_recurring_drafts(token, command_payload(payload), idempotency_key=key, book_id=book_id)
        ),
    )


@api.post("/investments/events")
def record_investment_event(request: HttpRequest, payload: RecordInvestmentEventCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "investment.event.record",
        lambda: (lambda event, replay: {"event": serialize(event), "idempotent_replay": replay})(
            *service.record_investment_event(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/investments/accounts/{account_id}/performance")
def investment_performance(request: HttpRequest, account_id: str, as_of: str | None = None):
    token = protected(request)
    return run("investment.performance", lambda: service.investment_performance(token, account_id, as_of=as_of))


@api.post("/funds")
def create_fund(request: HttpRequest, payload: CreateFundCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "fund.create",
        lambda: (lambda fund, replay: {"fund": serialize(fund), "idempotent_replay": replay})(
            *service.create_fund(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/funds/allocate")
def allocate_fund(request: HttpRequest, payload: FundAllocationCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "fund.allocate",
        lambda: (lambda result, replay: {"result": serialize(result), "idempotent_replay": replay})(
            *service.allocate_fund(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/funds/spend")
def spend_fund(request: HttpRequest, payload: FundSpendCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "fund.spend",
        lambda: (lambda result, replay: {"result": serialize(result), "idempotent_replay": replay})(
            *service.spend_fund(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/attachments")
def upload_attachment(request: HttpRequest):
    token = protected(request)
    key = idempotency_key(request)
    uploaded = request.FILES.get("file")
    if uploaded is None:
        raise_command_error(ValidationError("missing attachment file"), "attachment.upload")
    content = bytearray()
    for chunk in uploaded.chunks():
        content.extend(chunk)
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise_command_error(ValidationError("attachment exceeds size limit"), "attachment.upload")
    return run(
        "attachment.upload",
        lambda: (lambda result, replay: {"result": serialize(result), "idempotent_replay": replay})(
            *service.upload_attachment(
                token,
                filename=uploaded.name or "attachment",
                mime_type=getattr(uploaded, "content_type", None) or "application/octet-stream",
                content=bytes(content),
                idempotency_key=key,
            )
        ),
    )


@api.post("/reconciliation/actions")
def record_reconciliation(request: HttpRequest, payload: ReconciliationActionCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "reconciliation.record",
        lambda: (lambda action, replay: {"action": serialize(action), "idempotent_replay": replay})(
            *service.record_reconciliation_action(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/credentials/agent")
def issue_agent_credential(request: HttpRequest, payload: IssueCredentialCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "credential.issue",
        lambda: (lambda result, replay: {"credential": serialize(result), "idempotent_replay": replay})(
            *service.issue_agent_credential_command(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.get("/credentials")
def list_credentials(request: HttpRequest):
    token = protected(request)
    return run("credential.list", lambda: {"credentials": service.list_agent_credentials(token)})


@api.post("/credentials/revoke")
def revoke_credential(request: HttpRequest, payload: RevokeCredentialCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "credential.revoke",
        lambda: (lambda result, replay: {"credential": serialize(result), "idempotent_replay": replay})(
            *service.revoke_credential_command(token, command_payload(payload), idempotency_key=key)
        ),
    )


@api.post("/credentials/{credential_id}/revoke")
def revoke_credential_by_id(request: HttpRequest, credential_id: str, payload: RevokeCredentialByIdCommand):
    token = protected(request)
    key = idempotency_key(request)
    return run(
        "credential.revoke_by_id",
        lambda: (lambda result, replay: {"credential": serialize(result), "idempotent_replay": replay})(
            *service.revoke_credential_by_id_command(token, credential_id, command_payload(payload), idempotency_key=key)
        ),
    )


__all__ = ["api", "service"]
