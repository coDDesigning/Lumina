import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import get_route_path

from backend.app.config import Settings, settings
from backend.app.database import get_db
from backend.app.observability import (
    bind_request_id,
    bind_operation_context,
    configure_logging,
    normalize_request_id,
    reset_request_id,
    reset_operation_context,
)
from backend.app.readiness import ReadinessError, check_readiness
from backend.app.request_size import (
    MULTIPART_OVERHEAD_BYTES,
    RequestSizeLimitMiddleware,
)
from backend.app.security_headers import SecurityHeadersMiddleware
from backend.app.spa import (
    API_PREFIX,
    ROUTE_KIND_STATE_KEY,
    Scope,
    SPA_SHELL_PATH,
    STATIC_FILE_PATH,
    SinglePageApplication,
    UNMATCHED_API_PATH,
    UNMATCHED_PATH,
)
from routes import (
    activity,
    admin,
    ads,
    ai_models,
    ai_tutor,
    auth,
    client_error,
    conversation,
    course,
    course_qa,
    course_settings,
    document,
    exam_mode,
    exam_roadmap,
    flashcard,
    generated_output,
    generation_job,
    profile_document,
    profile_knowledge,
    progress,
    prompt_generator,
    quiz,
    reverse_quiz,
    study_guide,
    user,
)
from storage.base import Storage
from storage.dependencies import get_storage
from utils.deps import AUTH_STATE_KEY, USER_ID_KEY

logger = logging.getLogger(__name__)


def check_admin_bootstrap_security(app_settings: Settings | None = None) -> None:
    """Warn operators loudly if first-user-becomes-admin is enabled without token proof."""
    current = app_settings if app_settings is not None else settings
    if current.is_self_hosted and not current.requires_protected_admin_bootstrap:
        banner = (
            "===============================================================================\n"
            "SECURITY WARNING: UNPROTECTED ADMINISTRATOR BOOTSTRAP ACTIVE\n"
            "Self-hosted instance is running without protected administrator bootstrap.\n"
            "The first account to register will automatically become an administrator without\n"
            "token verification. To secure bootstrap, configure BOOTSTRAP_ADMIN_EMAIL and\n"
            "BOOTSTRAP_ADMIN_TOKEN, or set APP_ENV=production.\n"
            "==============================================================================="
        )
        logger.warning(
            "Unprotected administrator bootstrap is active: first registered user will "
            "automatically become an administrator without token verification. To secure bootstrap, "
            "configure BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_TOKEN or set APP_ENV=production.\n%s",
            banner,
            extra={
                "event": "unprotected_admin_bootstrap_warning",
            },
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(
        service="api",
        environment=settings.app_env,
        persistence_path=(
            settings.operational_log_path
            if settings.operational_log_persistence_enabled
            else None
        ),
        retention_days=settings.operational_log_retention_days,
        max_records=settings.operational_log_max_records,
    )
    check_admin_bootstrap_security(settings)
    # Every configured vendor joins the fallback chain, so an operator must be
    # able to see which ones an outage would bill without guessing.
    logger.info(
        "AI vendors available",
        extra={
            "event": "ai_vendors_available",
            "ai_available_vendors": ",".join(settings.ai_available_vendors),
            "ai_default_model": settings.ai_default_model,
            "ai_vision_model": settings.ai_vision_model,
        },
    )
    yield


app = FastAPI(
    title="Lumina API",
    description="Lumina AI Study Platform Backend API",
    version="1.0.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)
# Innermost, so it wraps the router and compresses both the JSON the API
# returns and the bundle the interface loads. The thresholds are the ones the
# reverse proxy this replaced used. Streaming event responses are excluded by
# the middleware itself; this application serves none.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_request_body_size=settings.max_request_size_bytes,
    max_upload_body_size=settings.max_upload_size_bytes + MULTIPART_OVERHEAD_BYTES,
    max_concurrent_uploads=settings.max_concurrent_document_validations,
    upload_request_timeout_seconds=settings.upload_request_timeout_seconds,
)
if settings.security_headers_enabled:
    # Added after the size limiter so it wraps it, and therefore covers the
    # responses that limiter returns before a route is ever reached.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.hsts_enabled,
        hsts_max_age_seconds=settings.hsts_max_age_seconds,
        settings=settings,
    )
app.include_router(auth.router)
app.include_router(course.router)
app.include_router(course_settings.router)
app.include_router(progress.router)
app.include_router(activity.router)
app.include_router(client_error.router)
app.include_router(admin.router)
app.include_router(user.router)
app.include_router(ai_models.router)
app.include_router(profile_knowledge.router)
app.include_router(profile_document.router)
app.include_router(document.router)
app.include_router(study_guide.router)
app.include_router(exam_roadmap.router)
app.include_router(generated_output.router)
app.include_router(generation_job.router)
app.include_router(exam_mode.router)
app.include_router(conversation.router)
app.include_router(quiz.router)
app.include_router(reverse_quiz.router)
app.include_router(flashcard.router)
app.include_router(prompt_generator.router)
app.include_router(ai_tutor.router)
app.include_router(course_qa.router)
app.include_router(ads.router)
app.add_exception_handler(
    RequestValidationError,
    document.upload_request_validation_error,
)
app.add_exception_handler(StarletteHTTPException, document.upload_http_error)


MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
UNLOGGED_PATHS = frozenset(
    {"/health/live", "/health/ready", "/ads.txt", SPA_SHELL_PATH, STATIC_FILE_PATH}
)
SLOW_REQUEST_THRESHOLD_MS = 2000.0
DEFAULT_ERROR_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
}


def _should_log_request(
    path: str, method: str, status_code: int, duration_ms: float
) -> bool:
    if status_code >= 500:
        return True
    if path in UNLOGGED_PATHS:
        return False
    if status_code >= 400 or duration_ms >= SLOW_REQUEST_THRESHOLD_MS:
        return True
    return method in MUTATION_METHODS


def _request_error_code(response: Response) -> str | None:
    header = response.headers.get("X-Error-Code")
    if header:
        return header
    default = DEFAULT_ERROR_CODES.get(response.status_code)
    if default is not None:
        return default
    return "server_error" if response.status_code >= 500 else None


def _request_scope_fields(scope: Scope) -> dict[str, object]:
    state = scope.get("state") or {}
    path_params = scope.get("path_params") or {}
    fields: dict[str, object] = {"auth_state": state.get(AUTH_STATE_KEY, "anonymous")}

    user_id = state.get(USER_ID_KEY)
    if isinstance(user_id, int):
        fields["user_id"] = user_id

    course_id = path_params.get("course_id")
    if isinstance(course_id, str) and course_id.isdigit():
        fields["course_id"] = int(course_id)
    elif isinstance(course_id, int):
        fields["course_id"] = course_id

    document_id = path_params.get("document_id")
    if isinstance(document_id, str) and document_id:
        fields["document_id"] = document_id

    return fields


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("X-Request-ID"))
    request_token = bind_request_id(request_id)
    operation_token = bind_operation_context(operation_id=f"api:{uuid4().hex}")
    started = time.perf_counter()

    def route_template() -> str:
        scope = request.scope
        route = scope.get("route")
        path = getattr(route, "path", None)
        if isinstance(path, str) and path.startswith("/"):
            return path
        kind = (scope.get("state") or {}).get(ROUTE_KIND_STATE_KEY)
        if isinstance(kind, str):
            return kind
        route_path = get_route_path(scope)
        if route_path == API_PREFIX or route_path.startswith(f"{API_PREFIX}/"):
            return UNMATCHED_API_PATH
        return UNMATCHED_PATH

    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(
            "HTTP request failed",
            extra={
                "event": "http_request_failed",
                "exception_type": type(exc).__name__,
                "http_method": request.method,
                "http_path": route_template(),
                "http_status": 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                **_request_scope_fields(request.scope),
            },
            exc_info=exc,
        )
        raise
    else:
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        path = route_template()
        if response.status_code >= 500:
            event, log = "http_request_failed", logger.error
        elif response.status_code == 429:
            event, log = "http_request_rate_limited", logger.warning
        elif response.status_code in {401, 403}:
            event, log = "http_authorization_denied", logger.warning
        elif response.status_code == 404:
            event, log = "http_not_found", logger.info
        elif response.status_code >= 400:
            event, log = "http_validation_rejected", logger.info
        elif duration_ms >= SLOW_REQUEST_THRESHOLD_MS:
            event, log = "http_request_slow", logger.warning
        else:
            event, log = "http_request_completed", logger.info
        if not _should_log_request(
            path, request.method, response.status_code, duration_ms
        ):
            return response
        content_length = response.headers.get("content-length")
        extra: dict[str, object] = {
            "event": event,
            "http_method": request.method,
            "http_path": path,
            "http_status": response.status_code,
            "error_code": _request_error_code(response),
            "duration_ms": duration_ms,
            **_request_scope_fields(request.scope),
        }
        if content_length is not None and content_length.isdigit():
            extra["response_bytes"] = int(content_length)
        log("HTTP request completed", extra=extra)
        return response
    finally:
        reset_operation_context(operation_token)
        reset_request_id(request_token)


@app.get("/ads.txt", response_class=PlainTextResponse)
def ads_txt() -> PlainTextResponse:
    return ads.get_ads_txt()


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get(
    "/health/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Not ready"}},
)
def health_ready(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, str]:
    try:
        check_readiness(db, storage)
    except ReadinessError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}
    return {"status": "ready"}


if settings.web_root is not None:
    # Installed as the router's fallback rather than mounted at "/", so every
    # API route, the documentation and the health probes are matched first and
    # keep FastAPI's own trailing-slash redirect and wrong-method handling.
    # See backend/app/spa.py.
    app.router.default = SinglePageApplication(settings.web_root, app.router.default)


if settings.cors_allowed_origins:
    app.middleware_stack = CORSMiddleware(
        app.build_middleware_stack(),
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        allow_headers=("Authorization", "Content-Type"),
        expose_headers=(
            "Retry-After",
            "X-Error-Code",
            "X-Export-Record-Limit",
            "X-Export-Truncated",
            "X-Request-ID",
        ),
    )
