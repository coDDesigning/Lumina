FROM python:3.12-slim-bookworm

# Prevent Python from writing pyc files to disk and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement files
COPY requirements.txt .

# Install reviewed, hash-locked dependencies.
RUN python -m pip install \
    --require-hashes \
    --only-binary=:all: \
    --requirement requirements.txt

# Copy the rest of the application
COPY . .

# Ensure entrypoint is executable
RUN chmod +x entrypoint.sh

# Ensure the data directory exists for SQLite and local storage
RUN mkdir -p /app/data

# Default to self_hosted mode, can be overridden by docker-compose
ENV DEPLOYMENT_MODE=self_hosted

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
