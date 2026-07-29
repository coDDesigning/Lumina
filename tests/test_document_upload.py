import hashlib
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest
from fastapi.testclient import TestClient

from routes import document as document_route

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


def _pdf_bytes(*, text: str | None = None, with_image: bool = False) -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()

    if text:
        page.insert_text((72, 72), text)
    if with_image:
        pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
        pixel.clear_with(255)
        page.insert_image(pymupdf.Rect(72, 72, 144, 144), stream=pixel.tobytes("png"))

    content = pdf.tobytes()
    pdf.close()
    return content


def _encrypted_pdf_bytes() -> bytes:
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


def _pdf_with_broken_later_page() -> bytes:
    pdf = pymupdf.open()
    first_page = pdf.new_page()
    first_page.insert_text((72, 72), "Valid first page")
    broken_page = pdf.new_page()
    broken_page.insert_text((72, 72), "Broken second page")
    pdf.xref_set_key(broken_page.xref, "Contents", "99999 0 R")
    content = pdf.tobytes()
    pdf.close()
    return content


def _pdf_with_malformed_later_stream() -> bytes:
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


def _pdf_with_dangling_xobject() -> bytes:
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


def _pdf_with_invalid_compressed_image_stream() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    pixel.clear_with(255)
    page.insert_image(pymupdf.Rect(72, 72, 144, 144), stream=pixel.tobytes("png"))
    image_xref = page.get_images(full=True)[0][0]
    pdf.update_stream(image_xref, b"broken-zlib", compress=False)
    pdf.xref_set_key(image_xref, "Filter", "/FlateDecode")
    content = pdf.tobytes()
    pdf.close()
    return content


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    monkeypatch.setattr(document_route, "UPLOAD_DIRECTORY", tmp_path)
    with TestClient(document_route.app) as client:
        yield client, tmp_path


def _upload(client: TestClient, filename: str, content: bytes, content_type: str):
    return client.post(
        "/upload-doc",
        files={"document": (filename, content, content_type)},
    )


def _assert_error(response, error_key: str) -> None:
    error = EXPECTED_ERRORS[error_key]
    assert response.status_code == error["status_code"]
    assert response.json() == {
        "success": False,
        "message": error["message"],
        "data": {"code": error["code"]},
    }


def test_configuration_matches_supported_contract():
    assert document_route.FILE_TYPES == EXPECTED_FILE_TYPES
    for key, definition in EXPECTED_ERRORS.items():
        assert document_route.UPLOAD_ERRORS[key] == definition


def test_configuration_accepts_another_text_extension(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """{
          "supported_file_types": {
            "rst": {
              "validator": "text",
              "content_type": "text/plain"
            }
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(document_route, "CONFIG_PATH", config_path)

    assert document_route._load_supported_file_types() == {
        "rst": {"validator": "text", "content_type": "text/plain"}
    }


def test_configuration_rejects_unknown_validator(monkeypatch):
    monkeypatch.setattr(
        document_route,
        "FILE_TYPES",
        {"docx": {"validator": "word", "content_type": "application/docx"}},
    )

    with pytest.raises(RuntimeError, match="Unknown configured validators: word"):
        document_route._validate_configured_handlers()


def test_configuration_rejects_duplicate_json_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """{
          "supported_file_types": {},
          "supported_file_types": {}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(document_route, "CONFIG_PATH", config_path)

    with pytest.raises(RuntimeError, match="Duplicate JSON key"):
        document_route._load_supported_file_types()


def test_message_catalog_accepts_another_error(tmp_path, monkeypatch):
    messages_path = tmp_path / "messages.json"
    messages_path.write_text(
        """{
          "upload_errors": {
            "scan_pending": {
              "status_code": 409,
              "code": "UPLOAD_SCAN_PENDING",
              "message": "The document is waiting for OCR."
            }
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(document_route, "MESSAGES_PATH", messages_path)

    assert document_route._load_upload_errors() == {
        "scan_pending": {
            "status_code": 409,
            "code": "UPLOAD_SCAN_PENDING",
            "message": "The document is waiting for OCR.",
        }
    }


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("notes.txt", b"Course notes", "text/plain"),
        ("lesson.md", b"# Lesson\n\nContent", "text/markdown"),
        ("lesson.markdown", b"# Lesson\n\nContent", "text/markdown"),
        ("NOTES.TXT", b"Uppercase extension", "text/plain"),
        (
            "pdf-header.txt",
            b"The PDF header is %PDF-1.7 and identifies the version.",
            "text/plain",
        ),
        ("executable-notes.txt", b"MZ is an executable header.", "text/plain"),
        ("image-notes.txt", b"GIF89a added animation support.", "text/plain"),
    ],
)
def test_supported_text_file_is_saved(upload_client, filename, content, content_type):
    client, upload_directory = upload_client

    response = _upload(client, filename, content, content_type)

    expected_hash = hashlib.sha256(content).hexdigest()
    extension = filename.rsplit(".", 1)[1].lower()
    assert response.status_code == 200
    assert response.json() == {
        "filename": filename,
        "content_type": content_type,
        "hash": expected_hash,
    }
    assert (upload_directory / f"{expected_hash}.{extension}").read_bytes() == content


def test_text_pdf_is_saved(upload_client):
    client, upload_directory = upload_client
    content = _pdf_bytes(text="Course material")

    response = _upload(client, "course.pdf", content, "application/pdf")

    expected_hash = hashlib.sha256(content).hexdigest()
    assert response.status_code == 200
    assert (upload_directory / f"{expected_hash}.pdf").read_bytes() == content


def test_image_only_pdf_is_saved_for_later_ocr(upload_client):
    client, upload_directory = upload_client
    content = _pdf_bytes(with_image=True)

    response = _upload(client, "scan.pdf", content, "application/pdf")

    expected_hash = hashlib.sha256(content).hexdigest()
    assert response.status_code == 200
    assert (upload_directory / f"{expected_hash}.pdf").read_bytes() == content


@pytest.mark.parametrize(
    "filename",
    ["document.docx", "document", "document.pdf.exe", ".pdf"],
)
def test_unsupported_filename_is_rejected_without_writing(upload_client, filename):
    client, upload_directory = upload_client

    response = _upload(client, filename, b"content", "application/octet-stream")

    _assert_error(response, "unsupported_file_type")
    assert list(upload_directory.iterdir()) == []


def test_missing_document_uses_error_response_contract(upload_client):
    client, upload_directory = upload_client

    response = client.post("/upload-doc")

    _assert_error(response, "document_required")
    assert list(upload_directory.iterdir()) == []


def test_malformed_multipart_uses_error_response_contract(upload_client):
    client, upload_directory = upload_client

    response = client.post(
        "/upload-doc",
        content=b"malformed multipart body",
        headers={"Content-Type": "multipart/form-data"},
    )

    _assert_error(response, "invalid_multipart")
    assert list(upload_directory.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("empty.txt", b""),
        ("whitespace.txt", b" \r\n\t"),
        ("empty.md", " \n\t".encode("utf-16")),
    ],
)
def test_empty_text_file_is_rejected_without_writing(upload_client, filename, content):
    client, upload_directory = upload_client

    response = _upload(client, filename, content, "text/plain")

    _assert_error(response, "empty_file")
    assert list(upload_directory.iterdir()) == []


def test_blank_pdf_is_rejected_without_writing(upload_client):
    client, upload_directory = upload_client

    response = _upload(client, "blank.pdf", _pdf_bytes(), "application/pdf")

    _assert_error(response, "empty_file")
    assert list(upload_directory.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"not a PDF",
        b"%PDF-1.7\ntruncated",
        bytes(range(256)),
        _pdf_with_broken_later_page(),
        _pdf_with_malformed_later_stream(),
        _pdf_with_dangling_xobject(),
        _pdf_with_invalid_compressed_image_stream(),
    ],
)
def test_corrupted_pdf_is_rejected_without_writing(upload_client, content):
    client, upload_directory = upload_client

    response = _upload(client, "broken.pdf", content, "application/pdf")

    _assert_error(response, "corrupted_pdf")
    assert list(upload_directory.iterdir()) == []


def test_password_protected_pdf_is_rejected_without_writing(upload_client):
    client, upload_directory = upload_client

    response = _upload(
        client,
        "protected.pdf",
        _encrypted_pdf_bytes(),
        "application/pdf",
    )

    _assert_error(response, "password_protected_pdf")
    assert list(upload_directory.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        bytes(range(256)),
        b"\x89PNG\r\n\x1a\nnot-text",
        _pdf_bytes(text="Renamed PDF"),
        b"X" + _pdf_bytes(text="Prefixed and renamed PDF"),
        b"X" * 1020 + _pdf_bytes(text="Boundary-prefixed renamed PDF"),
        b"X" * 1025 + _pdf_bytes(text="Long-prefixed renamed PDF"),
        _pdf_bytes(text="Altered-header renamed PDF").replace(b"%PDF-", b"%PDE-", 1),
    ],
)
def test_binary_text_file_is_rejected_without_writing(upload_client, content):
    client, upload_directory = upload_client

    response = _upload(client, "binary.txt", content, "text/plain")

    _assert_error(response, "corrupted_text")
    assert list(upload_directory.iterdir()) == []


@pytest.mark.parametrize("filename", ["binary.txt", "binary.md", "binary.markdown"])
def test_text_with_binary_tail_is_rejected_without_writing(upload_client, filename):
    client, upload_directory = upload_client
    content = b"A" * 100_000 + b"\x00" * 1_000

    response = _upload(client, filename, content, "text/plain")

    _assert_error(response, "corrupted_text")
    assert list(upload_directory.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"UTF-8 course notes",
        "UTF-16 course notes".encode("utf-16"),
        "BOM-less UTF-16 LE notes".encode("utf-16-le"),
        "BOM-less UTF-16 BE notes".encode("utf-16-be"),
        "Caf\u00e9 course notes".encode("cp1252"),
    ],
)
def test_detected_text_encoding_is_accepted(upload_client, content):
    client, upload_directory = upload_client

    response = _upload(client, "encoded.txt", content, "text/plain")

    expected_hash = hashlib.sha256(content).hexdigest()
    assert response.status_code == 200
    assert (upload_directory / f"{expected_hash}.txt").read_bytes() == content


def test_storage_error_is_generic(upload_client, monkeypatch):
    client, upload_directory = upload_client
    blocked_path = upload_directory / "not-a-directory"
    blocked_path.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(document_route, "UPLOAD_DIRECTORY", blocked_path)

    response = _upload(client, "notes.txt", b"Course notes", "text/plain")

    _assert_error(response, "upload_failed")


def test_atomic_storage_failure_preserves_existing_file(upload_client, monkeypatch):
    client, upload_directory = upload_client
    content = b"Course notes"
    expected_hash = hashlib.sha256(content).hexdigest()
    storage_path = upload_directory / f"{expected_hash}.txt"
    storage_path.write_bytes(b"existing content")

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(document_route.os, "replace", fail_replace)

    response = _upload(client, "notes.txt", content, "text/plain")

    _assert_error(response, "upload_failed")
    assert storage_path.read_bytes() == b"existing content"
    assert list(upload_directory.glob("*.tmp")) == []


def test_validated_content_type_does_not_trust_client_metadata(upload_client):
    client, _upload_directory = upload_client

    response = _upload(client, "notes.txt", b"Course notes", "text/html")

    assert response.status_code == 200
    assert response.json()["content_type"] == "text/plain"


def test_pdf_warning_buffer_is_isolated_across_threads():
    valid_pdf = _pdf_bytes(text="Valid course material")
    corrupt_pdf = _pdf_with_invalid_compressed_image_stream()

    def validation_result(content: bytes) -> str:
        try:
            document_route.validate_document_content("pdf", content)
        except document_route.DocumentValidationError as exc:
            return exc.error_key
        return "valid"

    payloads = [valid_pdf, corrupt_pdf] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(validation_result, payloads))

    assert results == ["valid", "corrupted_pdf"] * 8


def test_openapi_documents_upload_responses():
    upload_operation = document_route.app.openapi()["paths"]["/upload-doc"]["post"]

    assert {"200", "400", "415", "422", "500"} <= set(upload_operation["responses"])
