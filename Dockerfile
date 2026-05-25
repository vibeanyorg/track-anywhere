FROM python:3.12-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip uv \
    && python -m venv --without-pip /opt/venv

COPY pyproject.toml uv.lock README.md ./
COPY alembic ./alembic
COPY alembic_helpers ./alembic_helpers
COPY backend ./backend
COPY cli ./cli
COPY alembic.ini ./

RUN uv pip install --python /opt/venv/bin/python --no-cache ".[postgres]" \
    && find /opt/venv -type d \( -name __pycache__ -o -name test -o -name tests \) -prune -exec rm -rf '{}' + \
    && find /opt/venv -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

FROM node:22-alpine AS web-deps

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM web-deps AS web-builder

ENV NEXT_TELEMETRY_DISABLED=1

COPY frontend ./
RUN npm run build

FROM node:22-alpine AS web-runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    TRACK_ANYWHERE_BACKEND_URL=http://127.0.0.1:8000

WORKDIR /app/frontend

COPY --from=web-builder /app/frontend/.next/standalone ./
COPY --from=web-builder /app/frontend/.next/static ./.next/static

USER node

EXPOSE 3000

CMD ["node", "server.js"]

FROM python:3.12-slim AS api-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/backend/app:/app/cli" \
    TRACK_ANYWHERE_MODE=production

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system track-anywhere \
    && useradd --system --gid track-anywhere --home-dir /nonexistent --shell /usr/sbin/nologin track-anywhere

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=python-builder /app/backend /app/backend
COPY --from=python-builder /app/cli /app/cli
COPY --from=python-builder /app/alembic /app/alembic
COPY --from=python-builder /app/alembic_helpers /app/alembic_helpers
COPY --from=python-builder /app/alembic.ini /app/alembic.ini

USER track-anywhere

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3))['status'] == 'ok'"

CMD ["uvicorn", "track_anywhere.api:app", "--app-dir", "backend/app", "--host", "0.0.0.0", "--port", "8000"]
