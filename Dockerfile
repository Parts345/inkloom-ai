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
    HF_HOME=/home/app/.cache/huggingface \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown app:app /data

# Tesseract powers scanned-PDF OCR (services/extraction.py). Installed from apt so it works offline.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgl1 \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --timeout 120 --retries 10 torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --timeout 120 --retries 10 -r backend/requirements.txt \
 && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('rapidocr ok')"
 
# Bake the embedding model into the image so the first analysis never waits on the Hugging Face Hub.
RUN mkdir -p /home/app/.cache/huggingface \
 && HF_HOME=/home/app/.cache/huggingface python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', device='cpu')" \
 && chown -R app:app /home/app/.cache

COPY backend/app ./app
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

USER app
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
