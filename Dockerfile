# The interface, compiled to static files the API serves from its own origin.
# NODE_ENV must not be production here: vite and typescript are devDependencies
# and npm would skip them.
FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS web

ENV NODE_ENV=development \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
# frontend/tsconfig.json references the Playwright suite's tsconfig and Vite
# parses that reference graph while resolving the HTML entry, so this one file
# is needed from a suite the image never runs.
COPY frontend/e2e/tsconfig.json ./e2e/tsconfig.json
COPY frontend/vite.config.ts frontend/index.html ./
COPY frontend/public ./public
COPY frontend/src ./src

# The complete API prefix compiled into the bundle. The root-relative default
# is what makes the deployment same-origin, and it is why changing the
# published port needs no rebuild. An absolute http(s) URL builds a
# deliberately cross-origin bundle and then also needs CORS_ALLOWED_ORIGINS on
# the API. The application validates this at module scope and throws on a bad
# value, which shows as a blank page.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# vite build rather than `npm run build`: the package script is `tsc -b && vite
# build`, and tsc -b would follow the project reference above and drag the
# Playwright suite into a type-check CI already runs on its own.
RUN npx --no-install vite build \
    && test -f dist/index.html \
    && test -d dist/assets


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

# Outside /app, beside the weights, so the application directory stays free of
# anything but the application. backend/app/spa.py reads this path; unset, the
# image would serve the API alone.
ENV LUMINA_WEB_ROOT=/opt/lumina/web
COPY --from=web /build/dist /opt/lumina/web
RUN find /opt/lumina/web -type d -exec chmod 0555 {} + \
    && find /opt/lumina/web -type f -exec chmod 0444 {} +

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
# Shell form so FORWARDED_ALLOW_IPS is expanded, and the flags appear only when
# it is set. Nothing stands between a browser and this container by default, so
# no X-Forwarded-For is believed and the per-IP rate limits key on the real
# peer; set the variable only to the address of a TLS reverse proxy you run
# yourself, because any wider value lets a caller choose its own rate-limit
# identity. A JSON exec-form CMD would pass the expansion to uvicorn literally.
# exec keeps uvicorn as the signal-receiving process.
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port 8000 --limit-concurrency 100 --timeout-graceful-shutdown 330 --no-access-log ${FORWARDED_ALLOW_IPS:+--proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\"}"]
