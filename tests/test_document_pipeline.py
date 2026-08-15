from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import shutil
from threading import BoundedSemaphore, Lock
from time import sleep
import unicodedata

import pymupdf
import pytest

import services.document_pipeline as pipeline
from services.document_pipeline import (
    DocumentProcessingError,
    ExtractedDocument,
    ExtractionMethod,
    OCRStatus,
    OCRUnavailableError,
    PageVisualAnalysisStatus,
    PipelineOptions,
    PipelineStage,
    ProcessingErrorCode,
    TemporaryVisualServiceError,
    TesseractOCRProvider,
    VisualAnalysisError,
    VisualAnalysisStatus,
    VisualDescription,
    VisualSource,
    VisualType,
    extract_raw_document,
    process_document,
)


def pipeline_options(**overrides: object) -> PipelineOptions:
    values: dict[str, object] = {
        "ocr_enabled": False,
        "chunk_target_characters": 1_200,
        "chunk_overlap_characters": 120,
    }
    values.update(overrides)
    return PipelineOptions(**values)


def pdf_bytes(
    *page_texts: str | None,
    image_pages: set[int] | None = None,
    width: float = 595,
    height: float = 842,
) -> bytes:
    pdf = pymupdf.open()
    for page_number, text in enumerate(page_texts, start=1):
        page = pdf.new_page(width=width, height=height)
        if text:
            page.insert_text((36, 36), text)
        if image_pages and page_number in image_pages:
            pixel = pymupdf.Pixmap(
                pymupdf.csRGB,
                pymupdf.IRect(0, 0, 2, 2),
                False,
            )
            pixel.clear_with(255)
            page.insert_image(
                pymupdf.Rect(36, 50, 108, 122),
                stream=pixel.tobytes("png"),
            )
    content = pdf.tobytes()
    pdf.close()
    return content


def insert_test_image(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    *,
    shade: int = 255,
) -> None:
    pixel = pymupdf.Pixmap(
        pymupdf.csRGB,
        pymupdf.IRect(0, 0, 4, 4),
        False,
    )
    pixel.clear_with(shade)
    page.insert_image(rect, stream=pixel.tobytes("png"))


def insert_test_drawing(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    page.draw_rect(rect, color=(0, 0, 0), width=2)
    page.draw_line(
        (rect.x0, (rect.y0 + rect.y1) / 2),
        (rect.x1, (rect.y0 + rect.y1) / 2),
        color=(0, 0, 0),
        width=2,
    )
    page.draw_line(
        ((rect.x0 + rect.x1) / 2, rect.y0),
        ((rect.x0 + rect.x1) / 2, rect.y1),
        color=(0, 0, 0),
        width=2,
    )


def scanned_pdf_bytes(text: str) -> bytes:
    source = pymupdf.open()
    source_page = source.new_page(width=800, height=250)
    source_page.insert_text((50, 140), text, fontsize=36)
    image = source_page.get_pixmap(dpi=200, alpha=False).tobytes("png")
    source.close()

    scanned = pymupdf.open()
    scanned_page = scanned.new_page(width=800, height=250)
    scanned_page.insert_image(scanned_page.rect, stream=image)
    content = scanned.tobytes()
    scanned.close()
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
    pixel = pymupdf.Pixmap(
        pymupdf.csRGB,
        pymupdf.IRect(0, 0, 2, 2),
        False,
    )
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


def assert_pipeline_error(
    file_type: str,
    content: bytes,
    expected_code: ProcessingErrorCode,
    expected_stage: PipelineStage,
    *,
    options: PipelineOptions | None = None,
    ocr_provider: object | None = None,
    image_provider: object | None = None,
) -> DocumentProcessingError:
    with pytest.raises(DocumentProcessingError) as raised:
        process_document(
            file_type,
            content,
            options=options or pipeline_options(),
            ocr_provider=ocr_provider,
            image_provider=image_provider,
        )
    error = raised.value
    assert error.code == expected_code
    assert error.stage == expected_stage
    assert error.failed_stage == expected_stage
    assert error.safe_message == str(error)
    assert error.safe_message
    return error


def test_pipeline_defaults_use_worker_processing_settings() -> None:
    options = PipelineOptions()

    assert options.ocr_language == pipeline.settings.ocr_language
    assert options.ocr_dpi == pipeline.settings.ocr_dpi
    assert options.ocr_min_text_characters == pipeline.settings.ocr_min_text_characters
    assert (
        options.chunk_target_characters
        == pipeline.settings.document_chunk_size_characters
    )
    assert (
        options.chunk_overlap_characters
        == pipeline.settings.document_chunk_overlap_characters
    )
    assert options.max_visuals_per_page == 10
    assert options.max_visuals_per_document == 500
    assert (
        options.max_extracted_characters == pipeline.settings.max_extracted_characters
    )
    assert options.max_document_chunks == pipeline.settings.max_document_chunks


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract is not installed in this runtime",
)
def test_real_tesseract_extracts_text_from_scanned_pdf() -> None:
    result = process_document(
        "pdf",
        scanned_pdf_bytes("LOCAL OCR SMOKE TEST"),
        options=pipeline_options(
            ocr_enabled=True,
            ocr_language="eng",
            ocr_dpi=300,
            ocr_min_text_characters=20,
        ),
    )

    recognized = " ".join(page.text for page in result.pages).upper()
    assert "LOCAL" in recognized
    assert "OCR" in recognized
    assert "SMOKE" in recognized


def test_digital_pdf_extracts_physical_pages_with_one_based_provenance() -> None:
    content = pdf_bytes(
        "First physical page has searchable course material.",
        "Second physical page has a separate worked example.",
    )
    stages: list[PipelineStage] = []

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(
            chunk_target_characters=32,
            chunk_overlap_characters=8,
        ),
        stage_callback=stages.append,
    )

    assert [page.page_number for page in result.pages] == [1, 2]
    assert "First physical page" in result.pages[0].text
    assert "Second physical page" in result.pages[1].text
    assert {chunk.page_number for chunk in result.chunks} == {1, 2}
    assert all(chunk.page_number <= chunk.end_page_number for chunk in result.chunks)
    assert stages == [
        PipelineStage.VALIDATING,
        PipelineStage.EXTRACTING_TEXT,
        PipelineStage.CLEANING_TEXT,
        PipelineStage.CHUNKING,
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"UTF-8 course notes", "UTF-8 course notes"),
        ("UTF-16 course notes".encode("utf-16"), "UTF-16 course notes"),
        (
            "BOM-less UTF-16 LE notes".encode("utf-16-le"),
            "BOM-less UTF-16 LE notes",
        ),
        (
            "BOM-less UTF-16 BE notes".encode("utf-16-be"),
            "BOM-less UTF-16 BE notes",
        ),
        ("Caf\u00e9 course notes".encode("cp1252"), "Caf\u00e9 course notes"),
    ],
    ids=["utf8", "utf16", "utf16-le", "utf16-be", "cp1252"],
)
def test_text_encodings_are_decoded_once_with_nullable_page_provenance(
    content: bytes,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_from_bytes = pipeline.from_bytes

    def recording_from_bytes(value: bytes, **options: object):
        nonlocal calls
        calls += 1
        return original_from_bytes(value, **options)

    monkeypatch.setattr(pipeline, "from_bytes", recording_from_bytes)

    result = process_document("txt", content, options=pipeline_options())

    assert calls == 1
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.content_index == 0
    assert page.raw_text == expected
    assert page.text == expected
    assert page.page_number is None
    assert page.raw_extraction_method == ExtractionMethod.DECODED
    assert page.extraction_method == ExtractionMethod.DECODED
    assert page.ocr_status == OCRStatus.NOT_REQUIRED
    assert page.visual_analysis_status == PageVisualAnalysisStatus.NOT_APPLICABLE
    assert page.visuals == ()
    assert all(chunk.page_number is None for chunk in result.chunks)
    assert all(chunk.end_page_number is None for chunk in result.chunks)


def test_markdown_structure_and_conservative_cleaning_are_preserved() -> None:
    content = (
        b"# Heading  \r\n"
        b"\r\n"
        b"- first item\r\n"
        b"    - nested item  \r\n"
        b"\r\n"
        b"```python\r\n"
        b"def example():    \r\n"
        b"    return 1\r\n"
        b"```\r\n"
    )

    result = process_document("md", content, options=pipeline_options())

    assert result.pages[0].text == (
        "# Heading  \n"
        "\n"
        "- first item\n"
        "    - nested item  \n"
        "\n"
        "```python\n"
        "def example():    \n"
        "    return 1\n"
        "```"
    )
    assert result.chunks[0].text == result.pages[0].text


@pytest.mark.parametrize("file_type", ["md", "markdown"])
def test_raw_markdown_extraction_preserves_decoded_source_exactly(
    file_type: str,
) -> None:
    content = b"# Heading  \r\n\r\nParagraph with a hard break.  \r\n"

    extracted = extract_raw_document(
        file_type,
        content,
        options=pipeline_options(),
    )

    assert extracted.file_type == file_type
    assert extracted.contents[0].text == content.decode("utf-8")
    assert extracted.contents[0].page_number is None
    assert extracted.contents[0].extraction_method == ExtractionMethod.DECODED


def test_pdf_ocr_detection_requires_meaningful_visual_content_and_low_text() -> None:
    pdf = pymupdf.open()
    native_page = pdf.new_page(width=400, height=400)
    native_page.insert_text(
        (36, 36),
        "Searchable course content remains available and must be preserved.",
    )
    insert_test_image(native_page, pymupdf.Rect(40, 80, 180, 220))
    pdf.new_page(width=400, height=400)
    image_page = pdf.new_page(width=400, height=400)
    insert_test_image(image_page, pymupdf.Rect(40, 80, 180, 220), shade=200)
    drawing_page = pdf.new_page(width=400, height=400)
    insert_test_drawing(drawing_page, pymupdf.Rect(40, 80, 180, 220))
    content = pdf.tobytes()
    pdf.close()

    extracted: list[ExtractedDocument] = []
    result = process_document(
        "pdf",
        content,
        options=pipeline_options(
            ocr_enabled=False,
            ocr_min_text_characters=30,
        ),
        extraction_callback=extracted.append,
    )

    raw_pages = extracted[0].contents
    assert len(result.pages) == 4
    assert [page.page_number for page in raw_pages] == [1, 2, 3, 4]
    assert [page.has_images for page in raw_pages] == [True, False, True, False]
    assert [page.has_visual_content for page in raw_pages] == [True, False, True, True]
    assert [page.needs_ocr for page in raw_pages] == [False, False, True, True]
    assert raw_pages[2].visuals[0].source == VisualSource.IMAGE
    assert raw_pages[3].visuals[0].source == VisualSource.DRAWING

    native_result, blank_result, image_result, drawing_result = result.pages
    assert native_result.raw_text == raw_pages[0].text
    assert native_result.text == raw_pages[0].text.strip()
    assert native_result.raw_extraction_method == ExtractionMethod.NATIVE
    assert native_result.extraction_method == ExtractionMethod.NATIVE
    assert native_result.ocr_status == OCRStatus.NOT_REQUIRED
    assert blank_result.raw_text == blank_result.text == ""
    assert blank_result.needs_ocr is False
    assert blank_result.ocr_status == OCRStatus.NOT_REQUIRED
    assert image_result.needs_ocr is True
    assert drawing_result.needs_ocr is True
    assert image_result.ocr_status == OCRStatus.PENDING
    assert drawing_result.ocr_status == OCRStatus.PENDING


@pytest.mark.parametrize(
    "content_factory",
    [
        lambda: bytes(range(256)),
        lambda: b"\x89PNG\r\n\x1a\nnot-text",
        lambda: pdf_bytes("Renamed PDF with parseable content."),
        lambda: b"X" + pdf_bytes("Prefixed parseable PDF content."),
    ],
    ids=["all-byte-values", "png", "renamed-pdf", "prefixed-pdf"],
)
def test_binary_content_disguised_as_text_fails_in_pipeline(
    content_factory: Callable[[], bytes],
) -> None:
    error = assert_pipeline_error(
        "txt",
        content_factory(),
        ProcessingErrorCode.CORRUPTED_TEXT,
        PipelineStage.VALIDATING,
    )
    assert error.retryable is False


def test_text_with_binary_tail_fails_in_pipeline() -> None:
    assert_pipeline_error(
        "md",
        b"A" * 100_000 + b"\x00" * 1_000,
        ProcessingErrorCode.CORRUPTED_TEXT,
        PipelineStage.VALIDATING,
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
def test_corrupted_pdfs_fail_safely(
    content_factory: Callable[[], bytes],
) -> None:
    error = assert_pipeline_error(
        "pdf",
        content_factory(),
        ProcessingErrorCode.CORRUPTED_PDF,
        PipelineStage.VALIDATING,
    )
    assert error.retryable is False
    assert "truncated" not in error.safe_message.lower()


def test_password_protected_pdf_fails_safely() -> None:
    error = assert_pipeline_error(
        "pdf",
        encrypted_pdf_bytes(),
        ProcessingErrorCode.PASSWORD_PROTECTED_PDF,
        PipelineStage.VALIDATING,
    )
    assert error.retryable is False


def test_pdf_page_limit_is_enforced() -> None:
    content = pdf_bytes("Page one", "Page two")

    assert_pipeline_error(
        "pdf",
        content,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(max_pdf_pages=1),
    )


def test_pdf_page_and_aggregate_pixel_limits_are_enforced() -> None:
    one_page = pdf_bytes("Course", width=100, height=100)
    two_pages = pdf_bytes("Page one", "Page two", width=100, height=100)

    assert_pipeline_error(
        "pdf",
        one_page,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(max_pdf_page_pixels=1),
    )
    assert_pipeline_error(
        "pdf",
        two_pages,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(
            max_pdf_page_pixels=20_000,
            max_pdf_total_pixels=15_000,
        ),
    )


def test_pdf_decoded_content_stream_limit_is_enforced() -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 10), "Course")
    content_xref = page.get_contents()[0]
    pdf.update_stream(content_xref, b"0 0 m 1 1 l S\n" * 200, compress=True)
    content = pdf.tobytes()
    pdf.close()

    assert_pipeline_error(
        "pdf",
        content,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(max_pdf_content_stream_bytes=100),
    )


def test_pdf_drawing_operation_limit_is_enforced() -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 10), "Course")
    content_xref = page.get_contents()[0]
    pdf.update_stream(content_xref, b"0 0 m 1 1 l S\n" * 10, compress=True)
    content = pdf.tobytes()
    pdf.close()

    assert_pipeline_error(
        "pdf",
        content,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(max_pdf_drawing_operations=1),
    )


def test_nested_form_stream_is_preflighted_before_page_processing() -> None:
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

    assert_pipeline_error(
        "pdf",
        content,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(max_pdf_content_stream_bytes=100),
    )


def test_direct_page_content_cannot_bypass_limits_with_image_subtype() -> None:
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

    assert_pipeline_error(
        "pdf",
        content,
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.VALIDATING,
        options=pipeline_options(
            max_pdf_content_stream_bytes=100,
            max_pdf_drawing_operations=1,
        ),
    )


def test_ocr_runs_only_for_poor_visual_pages_and_updates_provenance() -> None:
    class RecordingOCR:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, int]] = []

        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            self.calls.append((page.number + 1, language, dpi))
            return f"Recognized visual labels from page {page.number + 1}."

    pdf = pymupdf.open()
    native_page = pdf.new_page(width=400, height=400)
    native_text = "This first page has enough searchable digital text to preserve."
    native_page.insert_text((36, 36), native_text)
    insert_test_image(native_page, pymupdf.Rect(40, 80, 180, 220))
    pdf.new_page(width=400, height=400)
    image_page = pdf.new_page(width=400, height=400)
    insert_test_image(image_page, pymupdf.Rect(40, 80, 180, 220), shade=180)
    drawing_page = pdf.new_page(width=400, height=400)
    insert_test_drawing(drawing_page, pymupdf.Rect(40, 80, 180, 220))
    content = pdf.tobytes()
    pdf.close()

    extracted: list[ExtractedDocument] = []
    ocr = RecordingOCR()
    stages: list[PipelineStage] = []

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(
            ocr_enabled=True,
            ocr_language="eng+deu",
            ocr_dpi=222,
            ocr_min_text_characters=30,
        ),
        stage_callback=stages.append,
        extraction_callback=extracted.append,
        ocr_provider=ocr,
    )

    assert ocr.calls == [(3, "eng+deu", 222), (4, "eng+deu", 222)]
    assert [page.needs_ocr for page in extracted[0].contents] == [
        False,
        False,
        True,
        True,
    ]
    native_result, blank_result, image_result, drawing_result = result.pages
    assert native_result.raw_text == f"{native_text}\n"
    assert native_result.text == native_text
    assert native_result.raw_extraction_method == ExtractionMethod.NATIVE
    assert native_result.extraction_method == ExtractionMethod.NATIVE
    assert native_result.ocr_status == OCRStatus.NOT_REQUIRED
    assert blank_result.raw_text == blank_result.text == ""
    assert blank_result.extraction_method is None
    assert blank_result.needs_ocr is False
    assert blank_result.ocr_status == OCRStatus.NOT_REQUIRED
    assert image_result.raw_text == ""
    assert image_result.raw_extraction_method is None
    assert image_result.text == "Recognized visual labels from page 3."
    assert image_result.extraction_method == ExtractionMethod.OCR
    assert image_result.needs_ocr is False
    assert image_result.ocr_status == OCRStatus.SUCCEEDED
    assert drawing_result.raw_text == ""
    assert drawing_result.raw_extraction_method is None
    assert drawing_result.text == "Recognized visual labels from page 4."
    assert drawing_result.extraction_method == ExtractionMethod.OCR
    assert drawing_result.needs_ocr is False
    assert drawing_result.ocr_status == OCRStatus.SUCCEEDED
    assert stages == [
        PipelineStage.VALIDATING,
        PipelineStage.EXTRACTING_TEXT,
        PipelineStage.RUNNING_OCR,
        PipelineStage.CLEANING_TEXT,
        PipelineStage.CHUNKING,
    ]


def test_ocr_rendering_obeys_configured_pdf_pixel_limits() -> None:
    class UnexpectedOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            raise AssertionError("over-limit page must not be rendered")

    error = assert_pipeline_error(
        "pdf",
        pdf_bytes(None, image_pages={1}, width=100, height=100),
        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
        PipelineStage.RUNNING_OCR,
        options=pipeline_options(
            ocr_enabled=True,
            ocr_dpi=300,
            max_pdf_page_pixels=50_000,
        ),
        ocr_provider=UnexpectedOCR(),
    )

    assert error.retryable is False


def test_tesseract_adapter_uses_pymupdf_textpage_ocr() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.ocr_arguments: dict[str, object] = {}
            self.text_page = object()

        def get_textpage_ocr(self, **arguments: object) -> object:
            self.ocr_arguments = arguments
            return self.text_page

        def get_text(self, kind: str, *, textpage: object) -> str:
            assert kind == "text"
            assert textpage is self.text_page
            return "Adapter OCR text"

    page = FakePage()

    text = TesseractOCRProvider().extract_text(
        page,
        language="eng+spa",
        dpi=300,
    )

    assert text == "Adapter OCR text"
    assert page.ocr_arguments == {
        "language": "eng+spa",
        "dpi": 300,
        "full": False,
    }


def test_partial_ocr_does_not_duplicate_sparse_searchable_text() -> None:
    class CombinedOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return "Title\nRecognized diagram label"

    result = process_document(
        "pdf",
        pdf_bytes("Title", image_pages={1}),
        options=pipeline_options(
            ocr_enabled=True,
            ocr_min_text_characters=20,
        ),
        ocr_provider=CombinedOCR(),
    )

    assert result.pages[0].text.count("Title") == 1
    assert "Recognized diagram label" in result.pages[0].text


def test_unusable_ocr_does_not_replace_sparse_native_text() -> None:
    class InvisibleOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return "\u200b\ufeff"

    result = process_document(
        "pdf",
        pdf_bytes("Title", image_pages={1}),
        options=pipeline_options(
            ocr_enabled=True,
            ocr_min_text_characters=20,
        ),
        ocr_provider=InvisibleOCR(),
    )

    assert result.pages[0].text == "Title"
    assert result.pages[0].extraction_method == ExtractionMethod.NATIVE
    assert result.pages[0].ocr_status == OCRStatus.NO_TEXT


def test_missing_tesseract_produces_stable_safe_error() -> None:
    class UnavailableOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            raise OCRUnavailableError("private installation detail")

    error = assert_pipeline_error(
        "pdf",
        pdf_bytes(None, image_pages={1}),
        ProcessingErrorCode.OCR_UNAVAILABLE,
        PipelineStage.RUNNING_OCR,
        options=pipeline_options(ocr_enabled=True),
        ocr_provider=UnavailableOCR(),
    )

    assert error.retryable is False
    assert error.safe_message == "Local OCR is unavailable."
    assert "private" not in str(error)


def test_visual_detection_orders_deduplicates_and_filters_regions() -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=400)
    page.insert_text((36, 24), "Native text keeps this visual page processable.")

    table_shape = page.new_shape()
    for x in (40, 110, 180):
        table_shape.draw_line((x, 40), (x, 180))
    for y in (40, 110, 180):
        table_shape.draw_line((40, y), (180, y))
    table_shape.finish(color=(0, 0, 0), width=1)
    table_shape.commit()
    page.insert_text((50, 80), "A")
    page.insert_text((120, 80), "B")
    page.insert_text((50, 150), "C")
    page.insert_text((120, 150), "D")

    insert_test_image(page, pymupdf.Rect(220, 40, 360, 180), shade=180)
    insert_test_drawing(page, pymupdf.Rect(40, 220, 180, 360))
    insert_test_image(page, pymupdf.Rect(300, 300, 320, 320), shade=100)
    content = pdf.tobytes()
    pdf.close()

    first = extract_raw_document("pdf", content, options=pipeline_options())
    second = extract_raw_document("pdf", content, options=pipeline_options())

    assert first == second
    page_result = first.contents[0]
    assert page_result.has_images is True
    assert page_result.has_visual_content is True
    assert [visual.visual_index for visual in page_result.visuals] == [0, 1, 2]
    assert [visual.source for visual in page_result.visuals] == [
        VisualSource.TABLE,
        VisualSource.IMAGE,
        VisualSource.DRAWING,
    ]
    assert [visual.visual_type for visual in page_result.visuals] == [
        VisualType.TABLE,
        VisualType.FIGURE,
        VisualType.DIAGRAM,
    ]
    assert [visual.bbox for visual in page_result.visuals] == [
        (40.0, 40.0, 180.0, 180.0),
        (220.0, 40.0, 360.0, 180.0),
        (40.0, 220.0, 180.0, 360.0),
    ]


def test_visual_selection_enforces_page_and_document_limits() -> None:
    pdf = pymupdf.open()
    for page_number in range(2):
        page = pdf.new_page(width=400, height=400)
        page.insert_text((36, 24), f"Searchable native content page {page_number + 1}.")
        insert_test_image(page, pymupdf.Rect(40, 40, 140, 140), shade=50)
        insert_test_image(page, pymupdf.Rect(200, 40, 300, 140), shade=200)
    content = pdf.tobytes()
    pdf.close()

    page_limited = extract_raw_document(
        "pdf",
        content,
        options=pipeline_options(max_visuals_per_page=1),
    )
    document_limited = extract_raw_document(
        "pdf",
        content,
        options=pipeline_options(
            max_visuals_per_page=2,
            max_visuals_per_document=3,
        ),
    )

    assert [len(page.visuals) for page in page_limited.contents] == [1, 1]
    assert [len(page.visuals) for page in document_limited.contents] == [2, 1]
    assert [visual.visual_index for visual in document_limited.contents[0].visuals] == [
        0,
        1,
    ]
    assert [visual.visual_index for visual in document_limited.contents[1].visuals] == [
        0
    ]


def test_visual_document_limit_does_not_disable_ocr_candidates() -> None:
    pdf = pymupdf.open()
    for _ in range(2):
        page = pdf.new_page(width=400, height=400)
        insert_test_image(page, pymupdf.Rect(40, 40, 300, 300), shade=150)
    content = pdf.tobytes()
    pdf.close()

    extracted = extract_raw_document(
        "pdf",
        content,
        options=pipeline_options(max_visuals_per_document=1),
    )

    assert [len(page.visuals) for page in extracted.contents] == [1, 0]
    assert [page.has_visual_content for page in extracted.contents] == [True, True]
    assert [page.needs_ocr for page in extracted.contents] == [True, True]


def test_repeated_small_images_are_filtered_as_decorative() -> None:
    pdf = pymupdf.open()
    image_xref = 0
    for page_number in range(3):
        page = pdf.new_page(width=400, height=400)
        page.insert_text(
            (36, 24),
            f"Searchable course page {page_number + 1} has enough native text.",
        )
        if image_xref:
            page.insert_image(pymupdf.Rect(40, 40, 100, 100), xref=image_xref)
        else:
            pixel = pymupdf.Pixmap(
                pymupdf.csRGB,
                pymupdf.IRect(0, 0, 4, 4),
                False,
            )
            pixel.clear_with(175)
            image_xref = page.insert_image(
                pymupdf.Rect(40, 40, 100, 100),
                stream=pixel.tobytes("png"),
            )
    content = pdf.tobytes()
    pdf.close()

    extracted = extract_raw_document("pdf", content, options=pipeline_options())

    assert all(page.has_images for page in extracted.contents)
    assert all(not page.has_visual_content for page in extracted.contents)
    assert all(page.visuals == () for page in extracted.contents)
    assert all(page.needs_ocr is False for page in extracted.contents)


def test_distinct_images_with_matching_metadata_are_not_filtered() -> None:
    pdf = pymupdf.open()
    for page_number, shade in enumerate((50, 100, 150), start=1):
        page = pdf.new_page(width=400, height=400)
        page.insert_text(
            (36, 24),
            f"Searchable course page {page_number} has enough native text.",
        )
        insert_test_image(page, pymupdf.Rect(40, 40, 100, 100), shade=shade)
    content = pdf.tobytes()
    pdf.close()

    extracted = extract_raw_document("pdf", content, options=pipeline_options())

    assert all(page.has_visual_content for page in extracted.contents)
    assert all(len(page.visuals) == 1 for page in extracted.contents)


def test_disabled_visual_provider_returns_not_configured_without_stage() -> None:
    class DisabledProvider:
        enabled = False

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> None:
            raise AssertionError("disabled provider must not be called")

    stages: list[PipelineStage] = []

    result = process_document(
        "pdf",
        pdf_bytes(
            "Searchable text that does not need enrichment.",
            image_pages={1},
            width=300,
            height=300,
        ),
        options=pipeline_options(),
        stage_callback=stages.append,
        image_provider=DisabledProvider(),
    )

    assert PipelineStage.UNDERSTANDING_IMAGES not in stages
    assert result.pages[0].visual_analysis_status == (
        PageVisualAnalysisStatus.NOT_CONFIGURED
    )
    assert result.pages[0].visuals[0].analysis_status == (
        VisualAnalysisStatus.NOT_CONFIGURED
    )
    assert result.pages[0].visuals[0].description is None


def test_enabled_provider_receives_region_crops_and_returns_typed_descriptions() -> (
    None
):
    class EnabledProvider:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[tuple[int, int, VisualType, int, int]] = []

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            with pymupdf.open(stream=visual_png, filetype="png") as image:
                width = image[0].rect.width
                height = image[0].rect.height
            self.calls.append(
                (page_number, visual_index, suggested_type, int(width), int(height))
            )
            return VisualDescription(
                visual_type=VisualType.CHART,
                description="Enrollment rises by quarter.\x00   \r\n  Peak: Q4.   ",
            )

    provider = EnabledProvider()
    stages: list[PipelineStage] = []

    result = process_document(
        "pdf",
        pdf_bytes(
            "Native page text remains authoritative and separate.",
            image_pages={1},
            width=300,
            height=300,
        ),
        options=pipeline_options(image_dpi=72),
        stage_callback=stages.append,
        image_provider=provider,
    )

    assert provider.calls == [(1, 0, VisualType.FIGURE, 72, 72)]
    page = result.pages[0]
    assert page.text == (
        "Native page text remains authoritative and separate.\n\n"
        "[Chart]\nEnrollment rises by quarter.\nPeak: Q4."
    )
    assert page.visual_analysis_status == PageVisualAnalysisStatus.COMPLETED
    assert len(page.visuals) == 1
    visual = page.visuals[0]
    assert visual.visual_type == VisualType.CHART
    assert visual.analysis_status == VisualAnalysisStatus.SUCCEEDED
    assert visual.description == "Enrollment rises by quarter.   \r\n  Peak: Q4."
    merged_chunk_text = "\n".join(chunk.text for chunk in result.chunks)
    assert "[Chart]" in merged_chunk_text
    assert "Enrollment rises by quarter." in merged_chunk_text
    assert "Peak: Q4." in merged_chunk_text
    assert stages == [
        PipelineStage.VALIDATING,
        PipelineStage.EXTRACTING_TEXT,
        PipelineStage.UNDERSTANDING_IMAGES,
        PipelineStage.CLEANING_TEXT,
        PipelineStage.CHUNKING,
    ]


def test_text_cleaning_normalizes_unicode_controls_and_whitespace() -> None:
    assert (
        pipeline._clean_text(
            "C\u0327alıs\u0327ma\u00a0  gu\u0308zel.\u200b\n\n\n\ufffd\ufffd\ufffd\nSon.",
            file_type="txt",
        )
        == "Çalışma güzel.\n\nSon."
    )
    assert pipeline._clean_text("Before\x00After", file_type="txt") == "BeforeAfter"
    cleaned = pipeline._clean_text("One   line.\n\n\nNext.", file_type="txt")
    assert pipeline._clean_text(cleaned, file_type="txt") == cleaned
    assert (
        pipeline._clean_text(
            "First\fSecond\u2028Third\u2029Fourth",
            file_type="txt",
        )
        == "First\nSecond\nThird\n\nFourth"
    )


def test_pdf_cleaning_repairs_guarded_line_wraps_and_hyphenation() -> None:
    assert pipeline._clean_text(
        "The operating sys-\ntem manages hard-\nware.\n\n"
        "client-\nserver and well-\nknown remain hyphenated.\n\n"
        "soft\u00ad\nhyphen joins.\n\n"
        "- list item\n- next item",
        file_type="pdf",
    ) == (
        "The operating sys-tem manages hard-ware.\n\n"
        "client-server and well-known remain hyphenated.\n\n"
        "softhyphen joins.\n\n"
        "- list item\n- next item"
    )


def test_pdf_cleaning_preserves_separate_layout_blocks() -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((36, 100), "Introduction")
    page.insert_text((36, 250), "this starts a separate paragraph.")
    content = pdf.tobytes()
    pdf.close()

    result = process_document("pdf", content, options=pipeline_options())

    assert result.pages[0].text == ("Introduction\n\nthis starts a separate paragraph.")


def test_pdf_layout_content_uses_text_blocks_only() -> None:
    class BlockPage:
        rect = pymupdf.Rect(0, 0, 595, 842)

        def get_text(self, kind: str, **options: object):
            assert kind == "blocks"
            assert options == {"flags": pymupdf.TEXTFLAGS_TEXT, "sort": True}
            return [
                (36.0, 10.0, 200.0, 30.0, "Header\n", 0, 0),
                (36.0, 100.0, 500.0, 140.0, "Body text\n", 1, 0),
                (250.0, 812.0, 320.0, 832.0, "1\n", 2, 0),
            ]

    assert pipeline._pdf_layout_content(BlockPage()) == (
        ("Header", "Body text", "1"),
        ("Header",),
        ("1",),
    )


def test_pdf_layout_content_rejects_blocks_crossing_edge_band() -> None:
    class MixedBlockPage:
        rect = pymupdf.Rect(0, 0, 595, 842)

        def get_text(self, kind: str, **options: object):
            assert kind == "blocks"
            return [
                (
                    36.0,
                    10.0,
                    500.0,
                    120.0,
                    "CS 201\nLearning objectives\nBody text\n",
                    0,
                    0,
                ),
                (
                    36.0,
                    760.0,
                    500.0,
                    830.0,
                    "References\n1\n",
                    1,
                    0,
                ),
            ]

    assert pipeline._pdf_layout_content(MixedBlockPage()) == (
        ("CS 201\nLearning objectives\nBody text", "References\n1"),
        (),
        (),
    )


def test_pdf_cleaning_reflows_large_input_in_linear_time() -> None:
    line = "a" * 40
    text = "\n".join(line for _ in range(40_000))

    cleaned = pipeline._clean_text(text, file_type="pdf")

    assert len(cleaned) == 1_639_999
    assert cleaned.startswith(f"{line} {line}")
    assert cleaned.endswith(f"{line} {line}")


def test_repeated_pdf_headers_footers_and_page_numbers_are_removed() -> None:
    pdf = pymupdf.open()
    bodies = (
        "Binary trees are hierarchical.",
        "Graphs connect vertices.",
        "Hash tables map keys.",
    )
    for page_number, body in enumerate(bodies, start=1):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((36, 24), "CS 201")
        page.insert_text((36, 100), body)
        page.insert_text((290, 825), str(page_number))
    content = pdf.tobytes()
    pdf.close()

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(),
    )

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [page.text for page in result.pages] == [
        "Binary trees are hierarchical.",
        "Graphs connect vertices.",
        "Hash tables map keys.",
    ]


def test_multiple_repeated_pdf_edge_lines_are_removed() -> None:
    pdf = pymupdf.open()
    for page_number in range(1, 4):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((36, 16), "University")
        page.insert_text((36, 32), "CS 201")
        page.insert_text((36, 200), f"Body {page_number}.")
        page.insert_text((36, 806), "Confidential")
        page.insert_text((290, 825), str(page_number))
    content = pdf.tobytes()
    pdf.close()

    result = process_document("pdf", content, options=pipeline_options())

    assert [page.text for page in result.pages] == [
        "Body 1.",
        "Body 2.",
        "Body 3.",
    ]


def test_repeated_header_text_is_preserved_when_repeated_in_body() -> None:
    pdf = pymupdf.open()
    for page_number in range(1, 4):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((36, 24), "CS 201")
        page.insert_text((36, 200), "CS 201")
        page.insert_text((36, 240), f"Body {page_number}.")
    content = pdf.tobytes()
    pdf.close()

    result = process_document("pdf", content, options=pipeline_options())

    assert all(page.text.count("CS 201") == 1 for page in result.pages)


def test_ocr_edge_lines_are_preserved_without_layout_evidence() -> None:
    class PagedOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            page_number = page.number + 1
            return f"Course Header\nOCR body {page_number}.\nPage {page_number}"

    pdf = pymupdf.open()
    for shade in (80, 160, 240):
        page = pdf.new_page(width=400, height=400)
        insert_test_image(page, pymupdf.Rect(40, 80, 180, 220), shade=shade)
    content = pdf.tobytes()
    pdf.close()

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(ocr_enabled=True),
        ocr_provider=PagedOCR(),
    )

    assert [page.text for page in result.pages] == [
        "Course Header\nOCR body 1.\nPage 1",
        "Course Header\nOCR body 2.\nPage 2",
        "Course Header\nOCR body 3.\nPage 3",
    ]


def test_ocr_numeric_content_is_not_mistaken_for_a_page_number() -> None:
    class AnswerOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return "Question\nThe answer is\n1"

    result = process_document(
        "pdf",
        pdf_bytes(None, image_pages={1}),
        options=pipeline_options(ocr_enabled=True),
        ocr_provider=AnswerOCR(),
    )

    assert result.pages[0].text == "Question\nThe answer is\n1"


def test_repeated_pdf_content_is_kept_below_confidence_threshold() -> None:
    result = process_document(
        "pdf",
        pdf_bytes(
            "Shared heading\nFirst topic.",
            "Shared heading\nSecond topic.",
        ),
        options=pipeline_options(),
    )

    assert all(page.text.startswith("Shared heading") for page in result.pages)


def test_repeated_visual_descriptions_are_omitted_from_merged_pages() -> None:
    class LogoProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.FIGURE,
                description="A blue university logo appears in the corner.",
            )

    pdf = pymupdf.open()
    for page_number, shade in enumerate((80, 160, 240), start=1):
        page = pdf.new_page(width=400, height=400)
        page.insert_text((36, 24), f"Page {page_number} body is useful.")
        insert_test_image(page, pymupdf.Rect(40, 80, 180, 220), shade=shade)
    content = pdf.tobytes()
    pdf.close()

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(),
        image_provider=LogoProvider(),
    )

    assert sum("university logo" in page.text for page in result.pages) == 1
    assert all(
        page.visuals[0].description == "A blue university logo appears in the corner."
        for page in result.pages
    )


def test_short_repeated_pdf_body_content_is_not_removed() -> None:
    pdf = pymupdf.open()
    for _ in range(3):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((36, 200), "Same important formula.")
    content = pdf.tobytes()
    pdf.close()

    result = process_document(
        "pdf",
        content,
        options=pipeline_options(),
    )

    assert all(page.text == "Same important formula." for page in result.pages)


def test_numeric_only_pdf_page_is_not_removed_as_a_page_number() -> None:
    result = process_document("pdf", pdf_bytes("1"), options=pipeline_options())

    assert result.pages[0].text == "1"


def test_markdown_long_fences_preserve_short_fence_content() -> None:
    cleaned = pipeline._clean_text(
        "````markdown\n```\ninside   code  \n```\n````\n\nBody.  ",
        file_type="md",
    )

    assert cleaned == ("````markdown\n```\ninside   code  \n```\n````\n\nBody.  ")


def test_markdown_garbage_cleanup_preserves_fenced_code() -> None:
    cleaned = pipeline._clean_text(
        "```text\n\ufffd\ufffd\ufffd\n```\n\n\ufffd\ufffd\ufffd\nBody.",
        file_type="md",
    )

    assert cleaned == "```text\n\ufffd\ufffd\ufffd\n```\n\n\ufffd\ufffd\ufffd\nBody."


def test_markdown_soft_hyphen_does_not_join_structural_lines() -> None:
    cleaned = pipeline._clean_text(
        "Text\u00ad\n- item\n\n```text\nsoft\u00ad\nhyphen\n```",
        file_type="md",
    )

    assert cleaned == "Text\n- item\n\n```text\nsoft\nhyphen\n```"


def test_markdown_code_blocks_preserve_consecutive_blank_lines() -> None:
    cleaned = pipeline._clean_text(
        "    first\n\n\n    second\n\n<pre>\nfirst\n\n\nsecond\n</pre>",
        file_type="md",
    )

    assert cleaned == "    first\n\n\n    second\n\n<pre>\nfirst\n\n\nsecond\n</pre>"


def test_visual_description_duplicate_of_primary_text_is_not_appended() -> None:
    class DuplicateProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="Native page text remains authoritative.",
            )

    result = process_document(
        "pdf",
        pdf_bytes(
            "Native page text remains authoritative.",
            image_pages={1},
            width=300,
            height=300,
        ),
        options=pipeline_options(),
        image_provider=DuplicateProvider(),
    )

    assert result.pages[0].text == "Native page text remains authoritative."
    assert result.pages[0].visuals[0].analysis_status == VisualAnalysisStatus.SUCCEEDED


def test_visual_description_duplicate_of_primary_line_is_not_appended() -> None:
    class DuplicateProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="Diagram label.",
            )

    result = process_document(
        "pdf",
        pdf_bytes(
            "Intro line.\nDiagram label.",
            image_pages={1},
            width=300,
            height=300,
        ),
        options=pipeline_options(),
        image_provider=DuplicateProvider(),
    )

    assert result.pages[0].text.count("Diagram label.") == 1


def test_cleaning_normalizes_after_removing_invisible_characters() -> None:
    cleaned = pipeline._clean_text("e\u200b\u0301", file_type="txt")

    assert cleaned == "é"
    assert unicodedata.is_normalized("NFC", cleaned)


def test_empty_pdf_pages_remain_identifiable_after_cleaning() -> None:
    result = process_document(
        "pdf",
        pdf_bytes("Useful first page.", None, "Useful third page."),
        options=pipeline_options(),
    )

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[1].text == ""
    assert [(chunk.page_number, chunk.end_page_number) for chunk in result.chunks] == [
        (1, 3)
    ]


def test_cleaning_failure_has_dedicated_retryable_error(monkeypatch) -> None:
    def fail_cleaning(*_args, **_kwargs):
        raise RuntimeError("private cleaning detail")

    monkeypatch.setattr(pipeline, "_clean_and_merge_pages", fail_cleaning)

    error = assert_pipeline_error(
        "txt",
        b"Course notes",
        ProcessingErrorCode.TEXT_CLEANING_FAILED,
        PipelineStage.CLEANING_TEXT,
    )

    assert error.retryable is True
    assert error.safe_message == (
        "The extracted document content could not be prepared for processing."
    )
    assert "private" not in str(error)


def test_merged_visual_content_obeys_extracted_text_limit() -> None:
    class VerboseProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="A description that pushes merged content over the limit.",
            )

    error = assert_pipeline_error(
        "pdf",
        pdf_bytes("Body", image_pages={1}, width=300, height=300),
        ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED,
        PipelineStage.CLEANING_TEXT,
        options=pipeline_options(max_extracted_characters=30),
        image_provider=VerboseProvider(),
    )

    assert error.retryable is False


def test_visual_analysis_error_is_nonfatal_and_page_specific() -> None:
    class PageSpecificProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            if page_number == 1:
                raise VisualAnalysisError("provider detail")
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="Second-page diagram description.",
            )

    result = process_document(
        "pdf",
        pdf_bytes(
            "First page has native fallback text.",
            "Second page has native fallback text.",
            image_pages={1, 2},
            width=300,
            height=300,
        ),
        options=pipeline_options(),
        image_provider=PageSpecificProvider(),
    )

    first, second = result.pages
    assert first.visual_analysis_status == PageVisualAnalysisStatus.FAILED
    assert first.visuals[0].analysis_status == VisualAnalysisStatus.FAILED
    assert first.visuals[0].error_code == "VISUAL_ANALYSIS_FAILED"
    assert first.visuals[0].description is None
    assert second.visual_analysis_status == PageVisualAnalysisStatus.COMPLETED
    assert second.visuals[0].analysis_status == VisualAnalysisStatus.SUCCEEDED
    assert second.visuals[0].description == "Second-page diagram description."
    assert "First page has native fallback text." in first.text
    assert any(
        "Second-page diagram description." in chunk.text for chunk in result.chunks
    )


def test_temporary_visual_service_error_is_retryable_and_fatal() -> None:
    class TemporaryFailureProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            raise TemporaryVisualServiceError("private provider detail")

    error = assert_pipeline_error(
        "pdf",
        pdf_bytes(
            "Searchable text survives visual analysis.",
            image_pages={1},
            width=300,
            height=300,
        ),
        ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED,
        PipelineStage.UNDERSTANDING_IMAGES,
        image_provider=TemporaryFailureProvider(),
    )

    assert error.retryable is True
    assert "private" not in str(error)


def test_malformed_visual_description_is_nonfatal() -> None:
    class MalformedProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=suggested_type,
                description=1,  # type: ignore[arg-type]
            )

    result = process_document(
        "pdf",
        pdf_bytes(
            "Usable native text remains available.",
            image_pages={1},
            width=300,
            height=300,
        ),
        options=pipeline_options(),
        image_provider=MalformedProvider(),
    )

    assert result.pages[0].visual_analysis_status == PageVisualAnalysisStatus.FAILED
    assert result.pages[0].visuals[0].analysis_status == VisualAnalysisStatus.FAILED
    assert result.pages[0].visuals[0].error_code == "VISUAL_ANALYSIS_FAILED"
    assert result.chunks[0].text == "Usable native text remains available."


def test_visual_rendering_obeys_configured_pdf_pixel_limits() -> None:
    class UnexpectedProvider:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            raise AssertionError("over-limit page must not be rendered")

    with pytest.raises(DocumentProcessingError) as raised:
        process_document(
            "pdf",
            pdf_bytes(
                "Searchable text",
                image_pages={1},
                width=100,
                height=100,
            ),
            options=pipeline_options(
                image_dpi=300,
                max_pdf_page_pixels=50_000,
            ),
            image_provider=UnexpectedProvider(),
        )

    assert raised.value.code == ProcessingErrorCode.DOCUMENT_TOO_COMPLEX
    assert raised.value.stage == PipelineStage.UNDERSTANDING_IMAGES
    assert raised.value.retryable is False


def test_empty_ocr_is_recorded_and_visual_understanding_can_recover_content() -> None:
    class EmptyOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return " \r\n\t"

    class RecoveringVision:
        enabled = True

        def describe_visual(
            self,
            visual_png: bytes,
            *,
            page_number: int,
            visual_index: int,
            suggested_type: VisualType,
        ) -> VisualDescription:
            return VisualDescription(
                visual_type=VisualType.DIAGRAM,
                description="A diagram contains useful semantic content.",
            )

    stages: list[PipelineStage] = []
    result = process_document(
        "pdf",
        pdf_bytes(None, image_pages={1}),
        options=pipeline_options(ocr_enabled=True),
        stage_callback=stages.append,
        ocr_provider=EmptyOCR(),
        image_provider=RecoveringVision(),
    )

    assert result.pages[0].ocr_status == OCRStatus.NO_TEXT
    assert result.pages[0].needs_ocr is False
    assert result.pages[0].extraction_method is None
    assert result.chunks[0].text == (
        "[Diagram]\nA diagram contains useful semantic content."
    )
    assert stages == [
        PipelineStage.VALIDATING,
        PipelineStage.EXTRACTING_TEXT,
        PipelineStage.RUNNING_OCR,
        PipelineStage.UNDERSTANDING_IMAGES,
        PipelineStage.CLEANING_TEXT,
        PipelineStage.CHUNKING,
    ]


def test_empty_required_ocr_without_visual_recovery_has_no_processable_text() -> None:
    class EmptyOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return " \r\n\t"

    error = assert_pipeline_error(
        "pdf",
        pdf_bytes(None, image_pages={1}),
        ProcessingErrorCode.NO_PROCESSABLE_TEXT,
        PipelineStage.CLEANING_TEXT,
        options=pipeline_options(ocr_enabled=True),
        ocr_provider=EmptyOCR(),
    )

    assert error.retryable is False


def test_empty_ocr_page_does_not_fail_other_usable_pages() -> None:
    class EmptyOCR:
        def extract_text(
            self,
            page: pymupdf.Page,
            *,
            language: str,
            dpi: int,
        ) -> str:
            return ""

    result = process_document(
        "pdf",
        pdf_bytes(
            "This native page keeps the document usable.",
            None,
            image_pages={2},
        ),
        options=pipeline_options(ocr_enabled=True),
        ocr_provider=EmptyOCR(),
    )

    assert result.pages[1].ocr_status == OCRStatus.NO_TEXT
    assert [chunk.page_number for chunk in result.chunks] == [1]
    assert [chunk.end_page_number for chunk in result.chunks] == [1]


def test_whitespace_text_fails_at_cleaning_without_optional_stages() -> None:
    stages: list[PipelineStage] = []
    with pytest.raises(DocumentProcessingError) as raised:
        process_document(
            "txt",
            b" \r\n\t",
            options=pipeline_options(),
            stage_callback=stages.append,
        )

    assert raised.value.code == ProcessingErrorCode.NO_PROCESSABLE_TEXT
    assert raised.value.stage == PipelineStage.EXTRACTING_TEXT
    assert stages == [
        PipelineStage.VALIDATING,
        PipelineStage.EXTRACTING_TEXT,
    ]


def test_chunks_are_deterministic_nonempty_and_boundary_aligned() -> None:
    content = (
        b"Paragraph one has several words for chunking.\n\n"
        b"Paragraph two has another deterministic sequence.\n"
        b"Final line remains separate."
    )
    options = pipeline_options(
        chunk_target_characters=48,
        chunk_overlap_characters=12,
    )

    first = process_document("txt", content, options=options)
    second = process_document("txt", content, options=options)

    assert first == second
    assert len(first.chunks) >= 3
    assert [chunk.chunk_index for chunk in first.chunks] == list(
        range(len(first.chunks))
    )
    assert all(chunk.text.strip() for chunk in first.chunks)
    assert all(chunk.character_count == len(chunk.text) for chunk in first.chunks)
    assert all(chunk.page_number is None for chunk in first.chunks)
    assert all(chunk.end_page_number is None for chunk in first.chunks)
    assert first.chunks[0].text.endswith("chunking.")
    assert all(not chunk.text.startswith(" ") for chunk in first.chunks[1:])


def test_chunk_overlap_never_exceeds_configured_window() -> None:
    target = 10
    overlap = 2
    content = b"word " + (b"x" * 40)

    result = process_document(
        "txt",
        content,
        options=pipeline_options(
            chunk_target_characters=target,
            chunk_overlap_characters=overlap,
        ),
    )

    assert all(len(chunk.text) <= target + overlap for chunk in result.chunks)


def test_chunk_limit_stops_materialization() -> None:
    error = assert_pipeline_error(
        "txt",
        b"one two three four five six seven eight nine ten",
        ProcessingErrorCode.DOCUMENT_CHUNK_LIMIT_EXCEEDED,
        PipelineStage.CHUNKING,
        options=pipeline_options(
            chunk_target_characters=8,
            chunk_overlap_characters=1,
            max_document_chunks=2,
        ),
    )

    assert error.retryable is False


def test_chunks_span_pages_with_inclusive_page_ranges() -> None:
    chunks = pipeline._chunk_pages(
        (
            pipeline.PageText("First page is short.", 1),
            pipeline.PageText("Second page continues with enough text to split.", 2),
            pipeline.PageText("   ", 3),
            pipeline.PageText("Fourth page closes the document.", 4),
        ),
        pipeline_options(
            chunk_target_characters=60,
            chunk_overlap_characters=10,
        ),
    )

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert any(
        chunk.page_number == 1
        and chunk.end_page_number == 2
        and "First page" in chunk.text
        and "Second page" in chunk.text
        for chunk in chunks
    )
    assert chunks[-1].page_number == 2
    assert chunks[-1].end_page_number == 4
    assert all(chunk.page_number != 3 for chunk in chunks)


def test_stage_callback_failure_is_wrapped_without_private_detail() -> None:
    def fail_callback(stage: PipelineStage) -> None:
        raise RuntimeError("private callback traceback detail")

    with pytest.raises(DocumentProcessingError) as raised:
        process_document(
            "txt",
            b"Course notes",
            options=pipeline_options(),
            stage_callback=fail_callback,
        )
    assert raised.value.code == ProcessingErrorCode.STAGE_CALLBACK_FAILED
    assert raised.value.stage == PipelineStage.VALIDATING
    assert raised.value.retryable is True
    assert "private" not in str(raised.value)


def test_callback_failure_does_not_log_private_detail(caplog) -> None:
    def fail_callback(stage: PipelineStage) -> None:
        raise RuntimeError("private callback traceback detail")

    with pytest.raises(DocumentProcessingError):
        process_document(
            "txt",
            b"Course notes",
            options=pipeline_options(),
            stage_callback=fail_callback,
        )

    assert "private callback traceback detail" not in caplog.text


def test_pipeline_concurrency_uses_configured_processing_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0
    lock = Lock()
    original_from_bytes = pipeline.from_bytes

    def slow_from_bytes(value: bytes, **options: object):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        sleep(0.02)
        try:
            return original_from_bytes(value, **options)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(pipeline, "_PIPELINE_SEMAPHORE", BoundedSemaphore(1))
    monkeypatch.setattr(pipeline, "from_bytes", slow_from_bytes)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: process_document(
                    "txt",
                    f"Course content {index}".encode(),
                    options=pipeline_options(),
                ),
                range(4),
            )
        )

    assert maximum_active == 1


def test_pdf_warning_buffer_is_isolated_across_threads() -> None:
    valid_pdf = pdf_bytes("Valid searchable course material")
    corrupt_pdf = pdf_with_invalid_compressed_image_stream()

    def processing_result(content: bytes) -> str:
        try:
            process_document("pdf", content, options=pipeline_options())
        except DocumentProcessingError as exc:
            return exc.code
        return "valid"

    payloads = [valid_pdf, corrupt_pdf] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(processing_result, payloads))

    assert results == ["valid", ProcessingErrorCode.CORRUPTED_PDF] * 8
