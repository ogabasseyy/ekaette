# ─── Stage 1: Build frontend ───
FROM node:24-slim@sha256:b4687aef2571c632a1953695ce4d61d6462a7eda471fe6e272eebf0418f276ba AS frontend-build
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build

# ─── Stage 2: Build Python deps (compile wheels, then discard build tools) ───
FROM python:3.13.12-slim@sha256:a208155746991fb5c4baf3c501401c3fee09e814ab0e5121a0f53b2ca659e0e2 AS python-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache -r requirements.txt && \
    rm /usr/local/bin/uv

# ─── Stage 3: Lean runtime ───
FROM python:3.13.12-slim@sha256:a208155746991fb5c4baf3c501401c3fee09e814ab0e5121a0f53b2ca659e0e2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime-only system deps: ffmpeg for TTS audio conversion
# ffmpeg version is implicitly pinned via the SHA-pinned base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --link --from=python-build /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --link --from=python-build /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy backend code
COPY --link main.py seed_data.py ./
COPY --link app/ app/

# Copy built frontend
COPY --link --from=frontend-build /app/frontend/dist frontend/dist

# Run as non-root user
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run injects PORT; default 8080
ENV PORT=8080

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8080')+'/health')" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
