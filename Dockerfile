# ─── Stage 1: Build frontend ───
FROM node:24-slim AS frontend-build
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@latest --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build

# ─── Stage 2: Python runtime ───
FROM python:3.13-slim

# System deps for grpc / crypto wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY main.py seed_data.py ./
COPY app/ app/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Cloud Run injects PORT; default 8080
ENV PORT=8080

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
