FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    HOME=/tmp \
    TMPDIR=/tmp

RUN groupadd --gid 10001 lumina \
    && useradd --uid 10001 --gid 10001 \
        --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin lumina \
    && mkdir -p /app /data/uploads /data/chroma \
    && chown -R 10001:10001 /data \
    && chmod 0750 /data /data/uploads /data/chroma

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install \
        --no-cache-dir \
        --require-hashes \
        --only-binary=:all: \
        --requirement requirements.txt \
    && python -m pip check

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
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "100", "--timeout-graceful-shutdown", "330"]
