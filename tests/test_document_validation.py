from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import sleep
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

import services.document_validation as validation
from services.document_validation import (
    DocumentValidationError,
    validate_document,
    validate_document_content,
)

EXPECTED_FILE_TYPES = {
    "pdf": {"validator": "pdf", "content_type": "application/pdf"},
    "txt": {"validator": "text", "content_type": "text/plain"},
    "md": {"validator": "text", "content_type": "text/markdown"},
    "markdown": {"validator": "text", "content_type": "text/markdown"},
}

EXPECTED_ERRORS = {
    "unsupported_file_type": {
        "status_code": 415,
        "code": "UPLOAD_UNSUPPORTED_FILE_TYPE",
        "message": "Unsupported file type. Please upload a PDF, TXT, or Markdown file.",
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
    "document_too_complex": {
        "status_code": 422,
        "code": "UPLOAD_DOCUMENT_TOO_COMPLEX",
        "message": "The uploaded document exceeds the configured processing limits.",
    },
    "course_document_limit": {
        "status_code": 409,
        "code": "UPLOAD_COURSE_DOCUMENT_LIMIT",
        "message": "The course document storage limit has been reached.",
    },
    "empty_file": {
        "status_code": 422,
        "code": "UPLOAD_EMPTY_FILE",
        "message": "The uploaded file is empty.",
    },
    "corrupted_pdf": {
        "status_code": 422,
        "code": "UPLOAD_CORRUPTED_PDF",
        "message": "The uploaded PDF is corrupted or invalid.",
    },
    "corrupted_text": {
        "status_code": 422,
        "code": "UPLOAD_CORRUPTED_TEXT",
        "message": "The uploaded text file is corrupted or contains binary data.",
    },
    "password_protected_pdf": {
        "status_code": 422,
        "code": "UPLOAD_PASSWORD_PROTECTED_PDF",
        "message": "Password-protected PDFs are not supported.",
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


def pdf_bytes(*, text: str | None = None, with_image: bool = False) -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    if text:
        page.insert_text((72, 72), text)
    if with_image:
        pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
        pixel.clear_with(255)
        page.insert_image(
            pymupdf.Rect(72, 72, 144, 144),
            stream=pixel.tobytes("png"),
        )
    content = pdf.tobytes()
    pdf.close()
    return content


def encrypted_pdf_bytes() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Protected course material")
    content = pdf.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    pdf.close()
    return content


def pdf_with_broken_later_page() -> bytes:
    pdf = pymupdf.open()
    first_page = pdf.new_page()
    first_page.insert_text((72, 72), "Valid first page")
    broken_page = pdf.new_page()
    broken_page.insert_text((72, 72), "Broken second page")
    pdf.xref_set_key(broken_page.xref, "Contents", "99999 0 R")
    content = pdf.tobytes()
    pdf.close()
    return content


def pdf_with_malformed_later_stream() -> bytes:
    pdf = pymupdf.open()
    first_page = pdf.new_page()
    first_page.insert_text((72, 72), "Valid first page")
    broken_page = pdf.new_page()
    broken_page.insert_text((72, 72), "Broken second page")
    content_xref = broken_page.get_contents()[0]
    pdf.update_stream(
        content_xref,
        b"not valid PDF drawing operators \xff\x00\x01",
        compress=False,
    )
    content = pdf.tobytes()
    pdf.close()
    return content


def pdf_with_dangling_xobject() -> bytes:
    pdf = pymupdf.open()
    first_page = pdf.new_page()
    first_page.insert_text((72, 72), "Valid first page")
    broken_page = pdf.new_page()
    broken_page.insert_text((72, 72), "Broken second page")
    _, resources_value = pdf.xref_get_key(broken_page.xref, "Resources")
    resources_xref = int(resources_value.split()[0])
    content_xref = broken_page.get_contents()[0]
    pdf.xref_set_key(resources_xref, "XObject", "<</Bad 99999 0 R>>")
    pdf.update_stream(content_xref, b"q /Bad Do Q", compress=False)
    content = pdf.tobytes()
    pdf.close()
    return content


def pdf_with_invalid_compressed_image_stream() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    pixel.clear_with(255)
    page.insert_image(
        pymupdf.Rect(72, 72, 144, 144),
        stream=pixel.tobytes("png"),
    )
    image_xref = page.get_images(full=True)[0][0]
    pdf.update_stream(image_xref, b"broken-zlib", compress=False)
    pdf.xref_set_key(image_xref, "Filter", "/FlateDecode")
    content = pdf.tobytes()
    pdf.close()
    return content


def assert_validation_error(
    filename: str,
    content: bytes,
    expected_key: str,
) -> None:
    upload = make_upload(filename, content)
    with pytest.raises(DocumentValidationError) as raised:
        validate_document(upload)
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
        '{"supported_file_types":{"rst":'
        '{"validator":"text","content_type":"text/plain"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(validation, "CONFIG_PATH", config_path)

    assert validation._load_supported_file_types() == {
        "rst": {"validator": "text", "content_type": "text/plain"}
    }


def test_configuration_rejects_unknown_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation,
        "FILE_TYPES",
        {"docx": {"validator": "word", "content_type": "application/docx"}},
    )

    with pytest.raises(RuntimeError, match="Unknown configured validators: word"):
        validation._validate_configured_handlers()


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


@pytest.mark.parametrize(
    ("filename", "content", "expected_type", "expected_mime"),
    [
        ("notes.txt", b"Course notes", "txt", "text/plain"),
        ("lesson.md", b"# Lesson\n\nContent", "md", "text/markdown"),
        (
            "lesson.markdown",
            b"# Lesson\n\nContent",
            "markdown",
            "text/markdown",
        ),
        ("NOTES.TXT", b"Uppercase extension", "txt", "text/plain"),
        (
            "pdf-header.txt",
            b"The PDF header is %PDF-1.7 and identifies the version.",
            "txt",
            "text/plain",
        ),
        ("executable-notes.txt", b"MZ is an executable header.", "txt", "text/plain"),
        ("image-notes.txt", b"GIF89a added animation support.", "txt", "text/plain"),
    ],
)
def test_supported_text_returns_trusted_metadata_and_resets_stream(
    filename: str,
    content: bytes,
    expected_type: str,
    expected_mime: str,
) -> None:
    upload = make_upload(filename, content, "text/html")
    upload.file.seek(len(content))

    metadata = validate_document(upload)

    assert metadata.original_file_name == filename
    assert metadata.file_type == expected_type
    assert metadata.mime_type == expected_mime
    assert metadata.file_size == len(content)
    assert upload.file.tell() == 0
    assert upload.file.read() == content


@pytest.mark.parametrize(
    "content",
    [
        b"UTF-8 course notes",
        "UTF-16 course notes".encode("utf-16"),
        "BOM-less UTF-16 LE notes".encode("utf-16-le"),
        "BOM-less UTF-16 BE notes".encode("utf-16-be"),
        "Caf\u00e9 course notes".encode("cp1252"),
    ],
    ids=["utf8", "utf16", "utf16-le", "utf16-be", "cp1252"],
)
def test_detected_text_encodings_are_accepted(content: bytes) -> None:
    metadata = validate_document(make_upload("encoded.txt", content))
    assert metadata.file_size == len(content)


def test_validation_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    maximum_active = 0
    lock = Lock()

    def slow_validator(_content: bytes) -> None:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        sleep(0.02)
        with lock:
            active -= 1

    monkeypatch.setattr(validation, "_VALIDATION_SEMAPHORE", BoundedSemaphore(1))
    monkeypatch.setitem(validation._CONTENT_VALIDATORS, "text", slow_validator)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: validate_document(
                    make_upload(f"notes-{index}.txt", b"content")
                ),
                range(4),
            )
        )

    assert maximum_active == 1


@pytest.mark.parametrize("with_image", [False, True], ids=["text", "image-only"])
def test_valid_pdf_content_is_accepted(with_image: bool) -> None:
    content = pdf_bytes(with_image=True) if with_image else pdf_bytes(text="Course")

    metadata = validate_document(make_upload("course.pdf", content, "text/plain"))

    assert metadata.file_type == "pdf"
    assert metadata.mime_type == "application/pdf"
    assert metadata.file_size == len(content)


def test_pdf_page_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = pymupdf.open()
    for page_number in range(2):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Page {page_number}")
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=1,
            max_pdf_page_pixels=40_000_000,
            max_pdf_total_pixels=100_000_000,
            max_pdf_content_stream_bytes=5 * 1024 * 1024,
            max_pdf_drawing_operations=100_000,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_pdf_render_pixel_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    content = pdf_bytes(text="Course")
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=1,
            max_pdf_total_pixels=100_000_000,
            max_pdf_content_stream_bytes=5 * 1024 * 1024,
            max_pdf_drawing_operations=100_000,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_pdf_aggregate_pixel_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = pymupdf.open()
    for page_number in range(2):
        page = pdf.new_page(width=100, height=100)
        page.insert_text((10, 10), f"Page {page_number}")
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=20_000,
            max_pdf_total_pixels=15_000,
            max_pdf_content_stream_bytes=5 * 1024 * 1024,
            max_pdf_drawing_operations=100_000,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_pdf_decoded_content_stream_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 10), "Course")
    content_xref = page.get_contents()[0]
    pdf.update_stream(content_xref, b"0 0 m 1 1 l S\n" * 200, compress=True)
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=40_000_000,
            max_pdf_total_pixels=100_000_000,
            max_pdf_content_stream_bytes=100,
            max_pdf_drawing_operations=100_000,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_pdf_drawing_operation_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 10), "Course")
    content_xref = page.get_contents()[0]
    pdf.update_stream(content_xref, b"0 0 m 1 1 l S\n" * 10, compress=True)
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=40_000_000,
            max_pdf_total_pixels=100_000_000,
            max_pdf_content_stream_bytes=5 * 1024 * 1024,
            max_pdf_drawing_operations=1,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_nested_form_stream_is_included_in_content_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    form_xref = pdf.get_new_xref()
    pdf.update_object(
        form_xref,
        "<</Type/XObject/Subtype/Form/BBox[0 0 100 100]/Resources<<>>>>",
    )
    pdf.update_stream(form_xref, b"0 0 m 1 1 l S\n" * 200, compress=True)
    page_content_xref = pdf.get_new_xref()
    pdf.update_object(page_content_xref, "<</Length 0>>")
    pdf.update_stream(page_content_xref, b"q /Fm Do Q", compress=False)
    page.set_contents(page_content_xref)
    pdf.xref_set_key(
        page.xref,
        "Resources",
        f"<</XObject<</Fm {form_xref} 0 R>>>>",
    )
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=40_000_000,
            max_pdf_total_pixels=100_000_000,
            max_pdf_content_stream_bytes=100,
            max_pdf_drawing_operations=100_000,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


def test_direct_content_cannot_bypass_limit_with_image_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 10), "Course")
    content_xref = page.get_contents()[0]
    pdf.update_stream(content_xref, b"0 0 m 1 1 l S\n" * 200, compress=True)
    pdf.xref_set_key(content_xref, "Subtype", "/Image")
    pdf.xref_set_key(content_xref, "Width", "1")
    pdf.xref_set_key(content_xref, "Height", "1")
    content = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(
        validation,
        "settings",
        SimpleNamespace(
            max_upload_size_bytes=len(content),
            max_pdf_pages=500,
            max_pdf_page_pixels=20_000,
            max_pdf_total_pixels=20_000,
            max_pdf_content_stream_bytes=100,
            max_pdf_drawing_operations=1,
        ),
    )

    assert_validation_error("course.pdf", content, "document_too_complex")


@pytest.mark.parametrize(
    "filename",
    ["document.docx", "document", "document.pdf.exe", ".pdf"],
)
def test_unsupported_filenames_are_rejected(filename: str) -> None:
    assert_validation_error(filename, b"content", "unsupported_file_type")


def test_filename_longer_than_database_limit_is_rejected() -> None:
    assert_validation_error("a" * 252 + ".txt", b"content", "invalid_file_name")


def test_file_larger_than_configured_limit_is_rejected_and_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = make_upload("large.txt", b"123456789")
    monkeypatch.setattr(
        validation, "settings", SimpleNamespace(max_upload_size_bytes=8)
    )

    with pytest.raises(DocumentValidationError) as raised:
        validate_document(upload)

    assert raised.value.error_key == "file_too_large"
    assert upload.file.tell() == 0


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("empty.txt", b""),
        ("whitespace.txt", b" \r\n\t"),
        ("empty.md", " \n\t".encode("utf-16")),
        ("blank.pdf", None),
    ],
    ids=["zero-bytes", "blank-utf8", "blank-utf16", "blank-pdf"],
)
def test_empty_documents_are_rejected(
    filename: str,
    content: bytes | None,
) -> None:
    assert_validation_error(
        filename, content if content is not None else pdf_bytes(), "empty_file"
    )


@pytest.mark.parametrize(
    "content_factory",
    [
        lambda: b"not a PDF",
        lambda: b"%PDF-1.7\ntruncated",
        lambda: bytes(range(256)),
        pdf_with_broken_later_page,
        pdf_with_malformed_later_stream,
        pdf_with_dangling_xobject,
        pdf_with_invalid_compressed_image_stream,
    ],
    ids=[
        "plain-text",
        "truncated",
        "binary",
        "broken-later-page",
        "malformed-stream",
        "dangling-xobject",
        "invalid-image-stream",
    ],
)
def test_corrupted_pdfs_are_rejected(content_factory: Callable[[], bytes]) -> None:
    assert_validation_error("broken.pdf", content_factory(), "corrupted_pdf")


def test_password_protected_pdf_is_rejected() -> None:
    assert_validation_error(
        "protected.pdf",
        encrypted_pdf_bytes(),
        "password_protected_pdf",
    )


@pytest.mark.parametrize(
    "content_factory",
    [
        lambda: bytes(range(256)),
        lambda: b"\x89PNG\r\n\x1a\nnot-text",
        lambda: pdf_bytes(text="Renamed PDF"),
        lambda: b"X" + pdf_bytes(text="Prefixed PDF"),
        lambda: b"X" * 1020 + pdf_bytes(text="Boundary PDF"),
        lambda: b"X" * 1025 + pdf_bytes(text="Long prefix PDF"),
        lambda: pdf_bytes(text="Altered header").replace(b"%PDF-", b"%PDE-", 1),
    ],
    ids=[
        "all-byte-values",
        "png",
        "renamed-pdf",
        "prefixed-pdf",
        "boundary-prefixed-pdf",
        "long-prefixed-pdf",
        "altered-pdf-header",
    ],
)
def test_binary_content_disguised_as_text_is_rejected(
    content_factory: Callable[[], bytes],
) -> None:
    assert_validation_error("binary.txt", content_factory(), "corrupted_text")


@pytest.mark.parametrize("filename", ["binary.txt", "binary.md", "binary.markdown"])
def test_text_with_a_binary_tail_is_rejected(filename: str) -> None:
    assert_validation_error(
        filename,
        b"A" * 100_000 + b"\x00" * 1_000,
        "corrupted_text",
    )


def test_pdf_warning_buffer_is_isolated_across_threads() -> None:
    valid_pdf = pdf_bytes(text="Valid course material")
    corrupt_pdf = pdf_with_invalid_compressed_image_stream()

    def validation_result(content: bytes) -> str:
        try:
            validate_document_content("pdf", content)
        except DocumentValidationError as exc:
            return exc.error_key
        return "valid"

    payloads = [valid_pdf, corrupt_pdf] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(validation_result, payloads))

    assert results == ["valid", "corrupted_pdf"] * 8
