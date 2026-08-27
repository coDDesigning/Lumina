import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.observability import (
    bind_request_id,
    configure_logging,
    normalize_request_id,
    reset_request_id,
)
from backend.app.readiness import ReadinessError, check_readiness
from backend.app.request_size import (
    MULTIPART_OVERHEAD_BYTES,
    RequestSizeLimitMiddleware,
)
from routes import (
    activity,
    admin,
    ai_models,
    ai_tutor,
    auth,
    conversation,
    course,
    course_qa,
    course_settings,
    document,
    exam_roadmap,
    flashcard,
    generated_output,
    profile_knowledge,
    progress,
    prompt_generator,
    quiz,
    study_guide,
    user,
)
from storage.base import Storage
from storage.dependencies import get_storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(service="api", environment=settings.app_env)
    yield


app = FastAPI(
    title="Lumina API",
    description="Lumina AI Study Platform Backend API",
    version="1.0.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_request_body_size=settings.max_request_size_bytes,
    max_upload_body_size=settings.max_upload_size_bytes + MULTIPART_OVERHEAD_BYTES,
    max_concurrent_uploads=settings.max_concurrent_document_validations,
    upload_request_timeout_seconds=settings.upload_request_timeout_seconds,
)
app.include_router(auth.router)
app.include_router(course.router)
app.include_router(course_settings.router)
app.include_router(progress.router)
app.include_router(activity.router)
app.include_router(admin.router)
app.include_router(user.router)
app.include_router(ai_models.router)
app.include_router(profile_knowledge.router)
app.include_router(document.router)
app.include_router(study_guide.router)
app.include_router(exam_roadmap.router)
app.include_router(generated_output.router)
app.include_router(conversation.router)
app.include_router(quiz.router)
app.include_router(flashcard.router)
app.include_router(prompt_generator.router)
app.include_router(ai_tutor.router)
app.include_router(course_qa.router)
app.add_exception_handler(
    RequestValidationError,
    document.upload_request_validation_error,
)
app.add_exception_handler(StarletteHTTPException, document.upload_http_error)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("X-Request-ID"))
    token = bind_request_id(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(
            "HTTP request failed",
            extra={
                "event": "http_request_failed",
                "exception_type": type(exc).__name__,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": 500,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        raise
    else:
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http_request_completed",
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return response
    finally:
        reset_request_id(token)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Lumina API Core is running!"}


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


if settings.cors_allowed_origins:
    app.middleware_stack = CORSMiddleware(
        app.build_middleware_stack(),
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
        allow_headers=("Authorization", "Content-Type"),
        expose_headers=("X-Error-Code",),
    )
