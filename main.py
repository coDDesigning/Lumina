from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.config import settings
from backend.app.request_size import (
    MULTIPART_OVERHEAD_BYTES,
    RequestSizeLimitMiddleware,
)
from routes import admin, auth, course, document, user

app = FastAPI(
    title="Lumina API",
    description="Lumina AI Study Platform Backend API",
    version="1.0.0",
)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_request_body_size=settings.max_request_size_bytes,
    max_upload_body_size=settings.max_upload_size_bytes + MULTIPART_OVERHEAD_BYTES,
)

app.include_router(auth.router)
app.include_router(course.router)
app.include_router(admin.router)
app.include_router(user.router)
app.include_router(document.router)
app.add_exception_handler(
    RequestValidationError,
    document.upload_request_validation_error,
)
app.add_exception_handler(StarletteHTTPException, document.upload_http_error)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Lumina API Core is running!"}
