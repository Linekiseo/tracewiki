FROM node:22-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/scripts ./scripts
COPY frontend/src ./src

RUN npm test \
    && npm run typecheck \
    && RAG_FRONTEND_OUT_DIR=/frontend-dist npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAG_DATA_DIR=/data \
    RAG_WEB_DIR=/app/web \
    RAG_ALLOWED_LOCAL_ROOTS=/workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY web/legacy.html ./web/legacy.html
COPY web/assets ./web/assets
COPY --from=frontend-builder /frontend-dist/ ./web/
RUN pip install --no-cache-dir .

EXPOSE 8000
VOLUME ["/data"]
CMD ["evidence-rag", "serve", "--host", "0.0.0.0", "--port", "8000"]
