# ---- frontend ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- app ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    SOULTRACKER_DB=/config/soultracker.db \
    SOULTRACKER_SHARES=/shares
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY soultracker ./soultracker
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./frontend/dist

EXPOSE 8484
VOLUME /config
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/api/health')"
CMD ["uvicorn", "soultracker.app:app", "--host", "0.0.0.0", "--port", "8484", "--no-access-log"]
