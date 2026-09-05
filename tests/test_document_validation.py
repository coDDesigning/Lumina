from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

import services.document_validation as validation
from services.document_validation import (
    DocumentValidationError,
    validate_basic_upload,
)

EXPECTED_FILE_TYPES = {
    "pdf": {"validator": "pdf", "content_type": "application/pdf"},
    "txt": {"validator": "text", "content_type": "text/plain"},
    "md": {"validator": "text", "content_type": "text/markdown"},
    "markdown": {"validator": "text", "content_type": "text/markdown"},
    "png": {"validator": "image", "content_type": "image/png"},
    "jpg": {"validator": "image", "content_type": "image/jpeg"},
    "jpeg": {"validator": "image", "content_type": "image/jpeg"},
}

EXPECTED_ERRORS = {
    "unsupported_file_type": {
        "status_code": 415,
        "code": "UPLOAD_UNSUPPORTED_FILE_TYPE",
        "message": (
            "Unsupported file type. Please upload a PDF, TXT, Markdown, "
            "or image (PNG or JPEG) file."
        ),
    },
    "invalid_file_name": {
        "status_code": 422,
        "code": "UPLOAD_INVALID_FILE_NAME",
        "message": "The uploaded file name is too long.",
    },
    "file_too_large": {
        "status_code": 413,
        "code": "UPLOAD_FILE_TOO_LARGE",
        "message": "The uploaded file exceeds the configured size limit.",
    },
    "course_document_limit": {
        "status_code": 409,
        "code": "UPLOAD_COURSE_DOCUMENT_LIMIT",
        "message": "The course document storage limit has been reached.",
    },
    "document_deletion_in_progress": {
        "status_code": 409,
        "code": "UPLOAD_DOCUMENT_DELETION_IN_PROGRESS",
        "message": "A matching document is being deleted. Please retry the upload.",
    },
    "empty_file": {
        "status_code": 422,
        "code": "UPLOAD_EMPTY_FILE",
        "message": "The uploaded file is empty.",
    },
    "document_required": {
        "status_code": 422,
        "code": "UPLOAD_DOCUMENT_REQUIRED",
        "message": "Please select a document to upload.",
    },
    "invalid_multipart": {
        "status_code": 400,
        "code": "UPLOAD_INVALID_MULTIPART",
        "message": "The upload request is invalid. Please attach a document file.",
    },
    "upload_failed": {
        "status_code": 500,
        "code": "UPLOAD_FAILED",
        "message": "There was an error uploading the document. Please try again.",
    },
}


def make_upload(
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def assert_validation_error(
    filename: str,
    content: bytes,
    expected_key: str,
) -> None:
    upload = make_upload(filename, content)
    with pytest.raises(DocumentValidationError) as raised:
        validate_basic_upload(upload)
    assert raised.value.error_key == expected_key
    assert upload.file.tell() == 0


def test_configuration_matches_supported_contract() -> None:
    assert validation.FILE_TYPES == EXPECTED_FILE_TYPES
    assert validation.UPLOAD_ERRORS == EXPECTED_ERRORS


def test_configuration_accepts_another_text_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"supported_file_types":{'
        '"rst":{"validator":"text","content_type":"text/plain"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(validation, "CONFIG_PATH", config_path)

    assert validation._load_supported_file_types() == {
        "rst": {"validator": "text", "content_type": "text/plain"}
    }


def test_configuration_rejects_duplicate_json_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"supported_file_types":{},"supported_file_types":{}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(validation, "CONFIG_PATH", config_path)

    with pytest.raises(RuntimeError, match="Duplicate JSON key"):
        validation._load_supported_file_types()


def test_message_catalog_accepts_another_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_path = tmp_path / "messages.json"
    messages_path.write_text(
        '{"upload_errors":{"scan_pending":{"status_code":409,'
        '"code":"UPLOAD_SCAN_PENDING","message":"Waiting for OCR."}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(validation, "MESSAGES_PATH", messages_path)

    assert validation._load_upload_errors() == {
        "scan_pending": {
            "status_code": 409,
            "code": "UPLOAD_SCAN_PENDING",
            "message": "Waiting for OCR.",
        }
    }


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
PDF_MAGIC = b"%PDF-"


@pytest.mark.parametrize(
    ("filename", "content", "expected_type", "expected_mime"),
    [
        # text has no reliable signature and is never content-sniffed
        ("notes.txt", b"Course notes", "txt", "text/plain"),
        ("lesson.md", b"# Lesson\n\nContent", "md", "text/markdown"),
        (
            "lesson.markdown",
            b"# Lesson\n\nContent",
            "markdown",
            "text/markdown",
        ),
        ("NOTES.TXT", b"Uppercase extension", "txt", "text/plain"),
        ("binary.txt", bytes(range(256)), "txt", "text/plain"),
        ("blank.md", b" \r\n\t", "md", "text/markdown"),
        ("page.txt", b"<html><body>still text</body></html>", "txt", "text/plain"),
        # pdf / image: leading bytes must match the declared extension
        (
            "course.pdf",
            PDF_MAGIC + b"1.7\n%\xe2\xe3\xcf\xd3\n",
            "pdf",
            "application/pdf",
        ),
        ("diagram.png", PNG_MAGIC + b"\x00\x00\x00\rIHDR", "png", "image/png"),
        ("photo.jpg", JPEG_MAGIC + b"\xe0\x00\x10JFIF", "jpg", "image/jpeg"),
        ("SCAN.JPEG", JPEG_MAGIC + b"\xdb\x00C\x00", "jpeg", "image/jpeg"),
    ],
)
def test_basic_validation_returns_metadata_for_consistent_or_unsniffable_content(
    filename: str,
    content: bytes,
    expected_type: str,
    expected_mime: str,
) -> None:
    upload = make_upload(filename, content, "text/html")
    upload.file.seek(len(content))

    metadata = validate_basic_upload(upload)

    assert metadata.original_file_name == filename
    assert metadata.file_type == expected_type
    assert metadata.mime_type == expected_mime
    assert metadata.file_size == len(content)
    assert upload.file.tell() == 0
    assert upload.file.read() == content


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("innocent.pdf", b"<html><body><script>alert(1)</script></body></html>"),
        ("report.pdf", b"PK\x03\x04 this is really a zip"),
        ("slides.pdf", PNG_MAGIC + b"a raster, not a pdf"),
        ("diagram.png", b"not actually a PNG"),
        ("photo.jpg", b"GIF89a wrong raster format"),
    ],
)
def test_content_that_contradicts_the_declared_type_is_rejected(
    filename: str,
    content: bytes,
) -> None:
    """BUG-043: an extension/byte mismatch is a 415 at upload, not a worker
    failure discovered minutes later."""
    assert_validation_error(filename, content, "unsupported_file_type")


@pytest.mark.parametrize(
    "filename",
    ["document.docx", "document", "document.pdf.exe", ".pdf"],
)
def test_unsupported_filenames_are_rejected_and_reset(filename: str) -> None:
    assert_validation_error(filename, b"content", "unsupported_file_type")


def test_filename_longer_than_database_limit_is_rejected() -> None:
    assert_validation_error("a" * 252 + ".txt", b"content", "invalid_file_name")


def test_filename_with_nul_is_rejected() -> None:
    assert_validation_error("unsafe\x00.txt", b"content", "invalid_file_name")


def test_exact_zero_byte_file_is_rejected_but_whitespace_is_not() -> None:
    assert_validation_error("empty.txt", b"", "empty_file")

    metadata = validate_basic_upload(make_upload("whitespace.txt", b" \r\n\t"))

    assert metadata.file_size == 4


def test_bounded_size_read_rejects_one_byte_over_limit_and_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingStream(BytesIO):
        def __init__(self, content: bytes) -> None:
            super().__init__(content)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    stream = RecordingStream(b"123456789more bytes are never read")
    upload = UploadFile(stream, filename="large.txt")
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(max_upload_size_bytes=8),
    )

    with pytest.raises(DocumentValidationError) as raised:
        validate_basic_upload(upload)

    assert raised.value.error_key == "file_too_large"
    assert stream.read_sizes == [9]
    assert stream.tell() == 0


def test_file_at_configured_limit_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(max_upload_size_bytes=8),
    )

    metadata = validate_basic_upload(make_upload("limit.txt", b"12345678"))

    assert metadata.file_size == 8


def test_read_failure_is_safe_and_resets_stream() -> None:
    class UnreadableStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("private stream failure")

    stream = UnreadableStream(b"content")
    upload = UploadFile(stream, filename="notes.txt")

    with pytest.raises(DocumentValidationError) as raised:
        validate_basic_upload(upload)

    assert raised.value.error_key == "upload_failed"
    assert stream.tell() == 0


def test_final_stream_reset_failure_is_safe() -> None:
    class ResetFailingStream(BytesIO):
        def __init__(self, content: bytes) -> None:
            super().__init__(content)
            self.seek_calls = 0

        def seek(self, offset: int, whence: int = 0) -> int:
            self.seek_calls += 1
            if self.seek_calls > 1:
                raise OSError("private reset failure")
            return super().seek(offset, whence)

    upload = UploadFile(ResetFailingStream(b"content"), filename="notes.txt")

    with pytest.raises(DocumentValidationError) as raised:
        validate_basic_upload(upload)

    assert raised.value.error_key == "upload_failed"


def test_request_time_catalog_contains_only_reachable_keys() -> None:
    """Every key in UPLOAD_ERRORS must be referenced by reachable code.

    Keys are reachable if they appear as a string argument to
    DocumentValidationError() in document_validation.py, or as a string
    argument to _error_response() in routes/document.py.
    """
    import ast

    validation_src = Path(validation.__file__).read_text(encoding="utf-8")
    route_path = (
        Path(validation.__file__).resolve().parents[1] / "routes" / "document.py"
    )
    route_src = route_path.read_text(encoding="utf-8")

    referenced_keys: set[str] = set()

    for source in (validation_src, route_src):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("DocumentValidationError", "_error_response"):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            referenced_keys.add(arg.value)

    catalog_keys = set(validation.UPLOAD_ERRORS.keys())
    unreachable = catalog_keys - referenced_keys
    assert not unreachable, (
        f"Upload error keys not referenced by any reachable code path: {unreachable}"
    )
