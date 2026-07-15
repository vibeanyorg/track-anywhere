FROM python:3.12-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip uv \
    && python -m venv --without-pip /opt/venv

COPY pyproject.toml uv.lock README.md ./
COPY alembic ./alembic
COPY backend ./backend
COPY cli ./cli
COPY alembic.ini ./

RUN VIRTUAL_ENV=/opt/venv uv sync --frozen --no-dev --extra postgres \
        --active --no-editable --no-cache \
    && find /opt/venv -type d \( -name __pycache__ -o -name test -o -name tests \) -prune -exec rm -rf '{}' + \
    && find /opt/venv -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

FROM node:22-alpine AS web-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

ENV NEXT_TELEMETRY_DISABLED=1

COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS api-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/backend/app:/app/cli" \
    TRACK_ANYWHERE_MODE=production \
    TRACK_ANYWHERE_STATIC_DIRECTORY=/app/frontend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system track-anywhere \
    && useradd --system --gid track-anywhere --home-dir /nonexistent --shell /usr/sbin/nologin track-anywhere

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=python-builder /app/backend/app /app/backend/app
COPY --from=python-builder /app/cli/track_anywhere_cli /app/cli/track_anywhere_cli
COPY --from=python-builder /app/alembic /app/alembic
COPY --from=python-builder /app/alembic.ini /app/alembic.ini
COPY --from=web-builder /app/frontend/out /app/frontend

USER track-anywhere

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; body=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v2/ready', timeout=3)); assert body == {'status':'ok','api_version':'v2','checks':{'database':'ok','schema':'ok'}}"

CMD ["uvicorn", "track_anywhere.server:app", "--app-dir", "backend/app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "60"]
