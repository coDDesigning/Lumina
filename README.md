# Lumina

## Document Upload

The document upload API currently runs as a standalone FastAPI application:

```bash
python -m pip install -r requirements.txt
python -m uvicorn routes.document:app --reload
```

Send `POST /upload-doc` as multipart form data using the `document` field. The
endpoint accepts PDF, TXT, and Markdown files, validates their contents, and
stores valid files in `UPLOAD_DIRECTORY` under their SHA-256 digest.

Supported extensions are configured in `app/config.json`. Upload error status
codes, stable API codes, and default messages are defined in
`app/messages.json`.

Image-only PDFs are accepted unchanged so OCR can be added later. The current
standalone endpoint uses local storage in both deployment modes and does not
enforce an upload size limit.

Run the test suite with:

```bash
python -m pytest -q
```
