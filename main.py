from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.readiness import ReadinessError, check_readiness
from backend.app.request_size import (
    MULTIPART_OVERHEAD_BYTES,
    RequestSizeLimitMiddleware,
)
from routes import (
    admin,
    ai_models,
    ai_tutor,
    auth,
    course,
    course_qa,
    course_settings,
    document,
    flashcard,
    generated_output,
    profile_knowledge,
    prompt_generator,
    quiz,
    study_guide,
    user,
)
from storage.base import Storage
from storage.dependencies import get_storage

app = FastAPI(
    title="Lumina API",
    description="Lumina AI Study Platform Backend API",
    version="1.0.0",
    debug=settings.app_debug,
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
app.include_router(admin.router)
app.include_router(user.router)
app.include_router(ai_models.router)
app.include_router(profile_knowledge.router)
app.include_router(document.router)
app.include_router(study_guide.router)
app.include_router(generated_output.router)
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
