# syntax=docker/dockerfile:1

# ---- Stage 1: build the React frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime serving API + static assets ----
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEMO_MODE=true \
    FRONTEND_DIST=/app/frontend/dist \
    DATABASE_URL=sqlite:////data/inkloom.db \
    HF_HOME=/home/app/.cache/huggingface

WORKDIR /app

RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown app:app /data

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

USER app
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
