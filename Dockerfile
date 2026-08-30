FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_INPUT=1 \
    HOME=/tmp \
    TMPDIR=/tmp \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 lumina \
    && useradd --uid 10001 --gid 10001 \
        --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin lumina \
    && mkdir -p /app /data/uploads /data/chroma \
    && chown -R 10001:10001 /data \
    && chmod 0750 /data /data/uploads /data/chroma

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install \
        --require-hashes \
        --only-binary=:all: \
        --requirement requirements.txt \
    && python -m pip check

# Baked at build time because the runtime container is read-only and must
# not reach the network for weights. Only the registry module is copied so
# a backend change does not re-download the weights.
ENV EMBEDDING_MODEL_CACHE_DIRECTORY=/opt/lumina/embedding-models
ENV HF_HUB_OFFLINE=1

COPY backend/__init__.py ./backend/__init__.py
COPY backend/app/__init__.py ./backend/app/__init__.py
COPY backend/app/embedding_models.py ./backend/app/embedding_models.py
COPY scripts/fetch_embedding_model.py ./scripts/fetch_embedding_model.py
RUN HF_HUB_OFFLINE=0 python scripts/fetch_embedding_model.py \
    && rm -rf ./scripts \
    && find /opt/lumina -type d -exec chmod 0555 {} + \
    && find /opt/lumina -type f -exec chmod 0444 {} +

COPY alembic.ini main.py entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app
COPY backend ./backend
COPY routes ./routes
COPY schemas ./schemas
COPY services ./services
COPY storage ./storage
COPY utils ./utils
COPY workers ./workers

RUN find /app -type d -exec chmod 0555 {} + \
    && find /app -type f -exec chmod 0444 {} + \
    && chmod 0555 /app/entrypoint.sh

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# Shell form so FORWARDED_ALLOW_IPS is expanded. A JSON exec-form CMD passes
# "${FORWARDED_ALLOW_IPS:-127.0.0.1}" to uvicorn literally, and uvicorn parses
# that into a trusted-host set matching nothing, silently disabling proxy
# header trust. exec keeps uvicorn as the signal-receiving process.
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port 8000 --limit-concurrency 100 --timeout-graceful-shutdown 330 --no-access-log --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
