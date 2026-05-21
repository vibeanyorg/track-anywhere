FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml uv.lock README.md ./
COPY alembic ./alembic
COPY alembic_helpers ./alembic_helpers
COPY backend ./backend
COPY cli ./cli
COPY alembic.ini ./

RUN /opt/venv/bin/uv pip install --python /opt/venv/bin/python --no-cache ".[postgres]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/backend/app:/app/cli" \
    TRACK_ANYWHERE_MODE=production

WORKDIR /app

RUN groupadd --system track-anywhere \
    && useradd --system --gid track-anywhere --home-dir /nonexistent --shell /usr/sbin/nologin track-anywhere

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

USER track-anywhere

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3))['status'] == 'ok'"

CMD ["uvicorn", "track_anywhere.api:app", "--app-dir", "backend/app", "--host", "0.0.0.0", "--port", "8000"]
