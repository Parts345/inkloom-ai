# Inkloom AI — Intelligent Brand Governance Platform (Team Caraxes)

Pre-publication brand governance platform built for **We Code Coders National Hackathon — CodeZen India 2026 (Round 3: The Finish — Final Product)**. Inkloom AI is a modular monolith combining a **FastAPI + FAISS + Sentence Transformers** backend with a **React 19 + TypeScript + Tailwind CSS** frontend, packaged into a single reproducible container.

- **Live Deployment:** https://inkloom-ai-production-97765d64.up.railway.app/
- **Source Repository:** https://github.com/Parts345/inkloom-ai

---

## Table of Contents
1. [Core Capabilities & Round 3 Improvements](#core-capabilities--round-3-improvements)
2. [System Architecture](#system-architecture)
3. [Prerequisites](#prerequisites)
4. [Quick Start: Clone & Run with Docker / Podman (Recommended)](#quick-start-clone--run-with-docker--podman-recommended)
5. [Local Development Setup (Without Docker)](#local-development-setup-without-docker)
6. [Environment Variables Reference](#environment-variables-reference)
7. [End-to-End Verification & Smoke Testing](#end-to-end-verification--smoke-testing)
8. [Automated Test Suite](#automated-test-suite)
9. [Project Structure](#project-structure)
10. [Product Roadmap](#product-roadmap)
11. [Team Caraxes](#team-caraxes)

---

## Core Capabilities & Round 3 Improvements

- **Multi-Format Ingestion:** Accepts pasted copy, `.txt`, `.docx` (paragraphs, headings, run text colors, and embedded images), digital and scanned `.pdf` documents, and standalone `.png` / `.jpg` campaign assets.
- **Hybrid Verification Engine:** Runs deterministic Python/OpenCV/WCAG 2.2 AA validators concurrently (`asyncio.gather`) alongside FAISS (`all-MiniLM-L6-v2`) RAG retrieval, multimodal Vision-Language Model (VLM) asset verification, and schema-validated LLM claim checks—with automatic deterministic fallbacks when offline or rate-limited.
- **Explainable Findings & Inline Diffs:** Flags brand terminology, unverified factual claims, pricing/expiry mismatches, tone issues, off-brand palettes, unauthorized visual assets, and WCAG 2.2 AA contrast/alt-text gaps with source evidence, confidence scores, and one-click minimal edits (`v1` -> `v2`).
- **Role-Based Governance & Audit Trail:** Built-in role switcher (`Creator`, `Approver`, `Admin`) in `DEMO_MODE=true`, immutable append-only audit trail, and Governance Analytics.

### Round 3 Delivered Improvements
1. **Scanned-PDF & Image OCR Pipeline (`backend/app/services/extraction.py`):** Pages with low extractable text density (`< 40` characters) are automatically rendered at 200 DPI via PyMuPDF and processed via **RapidOCR** (`rapidocr-onnxruntime`) with **Tesseract** (`tesseract-ocr` baked into the image) fallback. Extraction logs `ocr_units=N` in the audit trail and tags extracted blocks with an **OCR** badge in the Review Workspace.
2. **Multimodal Approved Visual Asset Verification (`backend/app/services/vision_verifier.py`):** Approvers and Admins upload approved logos, banners, and color palettes in the **Brand Knowledge Base**. Campaign images and embedded `.docx` visuals are audited against approved assets via `VLM_MODEL` (with OpenCV/Pillow perceptual and palette checks as an offline fallback).
3. **Dispute-Driven Rule Refinement (`backend/app/services/dispute_refiner.py`):** **Governance Analytics** clusters creator disputes by cited rule ID, diagnoses root causes (`ambiguous`, `overly_broad`, `outdated`), and synthesizes an updated rule proposal. Admins apply refinements with one click, triggering automatic Knowledge Base re-indexing and audit-trail logging while excluding previously resolved disputes.

---

## System Architecture

```text
+-------------------------------------------------------------------------+
|                         Single Container / App                          |
|                                                                         |
|  +------------------------------+    +-------------------------------+  |
|  |  React 19 + TypeScript + Vite|--->|     FastAPI REST Backend      |  |
|  |  (Creator / Approver / Admin)|    |  /api/submissions, /knowledge |  |
|  +------------------------------+    |  /api/analytics, /api/demo    |  |
|                                      +---------------+---------------+  |
|                                                      |                  |
|         +------------------------+-------------------+---------+        |
|         v                        v                             v        |
|  +-------------+   +---------------------------+   +-----------------+  |
|  | Extraction  |   |  Hybrid Verification      |   |  SQLite + FAISS |  |
|  | & OCR       |   │  - Deterministic Rules    |   |  - /data/       |  |
|  | - PyMuPDF   |   |  - FAISS RAG (MiniLM-L6)  |   |    inkloom.db   |  |
|  | - python-   |   |  - LLM Claim Verifier     |   |  - Approved     |  |
|  |   docx      |   |  - VLM / OpenCV Visuals   |   |    Assets       |  |
|  | - RapidOCR  |   +---------------------------+   +-----------------+  |
|  | - Tesseract |                                                        |
|  +-------------+                                                        |
+-------------------------------------------------------------------------+
```

---

## Prerequisites

- **Option A (Containerized — Recommended):** Docker (`>= 24.0`) + Docker Compose, or Podman + `podman-compose`.
- **Option B (Local Development):**
  - **Python** `3.11+`
  - **Node.js** `20+` and `npm`
  - **Tesseract OCR** system binary (`sudo apt install tesseract-ocr` on Debian/Ubuntu, `sudo dnf install tesseract` on Fedora, or `brew install tesseract` on macOS) for scanned-PDF OCR.

---

## Quick Start: Clone & Run with Docker / Podman (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Parts345/inkloom-ai.git
   cd inkloom-ai
   ```

2. **Configure environment variables (optional — works offline out of the box):**
   ```bash
   cp .env.example .env
   ```
   Open `.env` and add your API key if you want live LLM and VLM verification (otherwise Inkloom AI automatically runs deterministic + OCR + OpenCV fallbacks):
   ```env
   DEMO_MODE=true
   LLM_PROVIDER=groq
   LLM_API_KEY=your_api_key_here
   LLM_MODEL=openai/gpt-oss-20b
   VLM_PROVIDER=groq
   VLM_API_KEY=your_api_key_here
   VLM_MODEL=qwen/qwen3.8-27b
   ```

3. **Build and start the application:**
   ```bash
   # Using Docker Compose
   docker compose up -d --build

   # OR using Podman Compose
   podman-compose up -d --build
   ```

4. **Open the application:**
   - **Web UI:** http://localhost:7860
   - **Health Check:** http://localhost:7860/api/health
   - **Interactive API Docs (Swagger):** http://localhost:7860/docs

   > **Note:** On first boot, the application automatically seeds the **NovaTech Cloud** workspace (`ws_novatech_cloud`), sample brand rules, approved visual assets, and demo submissions.

5. **Rebuild after `.env` changes:**
   ```bash
   docker compose down && docker compose up -d --build
   ```

---

## Local Development Setup (Without Docker)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Parts345/inkloom-ai.git
   cd inkloom-ai
   cp .env.example .env
   ```

2. **Terminal 1 — Start the FastAPI Backend (`:8000`):**
   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate        # On Windows: .venv\Scripts\activate
   pip install --upgrade pip
   pip install torch --index-url https://download.pytorch.org/whl/cpu
   pip install -r requirements.txt

   # Use a local SQLite database file if /data does not exist on your machine
   export DATABASE_URL="sqlite:///./inkloom.db"
   uvicorn app.main:app --reload --port 8000
   ```

3. **Terminal 2 — Start the React Frontend (`:5173`):**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   Open **http://localhost:5173** in your browser (Vite proxies `/api` and `/assets/approved` requests to `http://localhost:8000`).

---

## Environment Variables Reference

| Variable | Default | Purpose |
|---|---|---|
| `DEMO_MODE` | `true` | Enables one-click role switching (**Creator**, **Approver**, **Admin**) without login friction. Set `false` for JWT auth. |
| `DATABASE_URL` | `sqlite:////data/inkloom.db` | SQLAlchemy database connection string. |
| `LLM_PROVIDER` | `groq` | `groq` or `gemini` (auto-configures OpenAI-compatible `base_url` when `LLM_BASE_URL` is unset). |
| `LLM_API_KEY` | *(empty)* | API key for text claim verification and dispute rule refinement. Falls back to deterministic rules if blank. |
| `LLM_MODEL` | `openai/gpt-oss-20b` | Model ID for text verification and dispute synthesis (e.g., `openai/gpt-oss-20b` or `openai/gpt-oss-120b`). |
| `VLM_PROVIDER` | *(inherits `LLM_PROVIDER`)* | Vision provider (`groq` or `gemini`). |
| `VLM_API_KEY` | *(inherits `LLM_API_KEY`)* | API key for multimodal image verification. Falls back to OpenCV/Pillow checks if blank. |
| `VLM_MODEL` | `qwen/qwen3.8-27b` | Vision-capable model ID (e.g., `qwen/qwen3.8-27b` or `gemini-2.5-flash`). |
| `OMP_NUM_THREADS` | `1` | Caps OpenMP/ONNX thread pool size for low-memory containers. |
| `MALLOC_ARENA_MAX` | `2` | Caps glibc memory arenas for 1 GB container stability. |

---

## End-to-End Verification & Smoke Testing

1. **Quick Sample Draft Check:**
   - Open the UI, click **New Submission** -> **Load Sample Draft with Violations** -> **Submit for review**.
   - Inspect findings, click **Accept Edit** on a suggested fix (draft increments `v1` -> `v2`), or click **Dispute** to log a creator dispute.
2. **Generate & Test Round 3 Smoke Fixtures (Scanned PDF & Off-Brand Logo):**
   ```bash
   cd backend
   source .venv/bin/activate
   python -m tests.fixtures.make_fixtures
   ```
   - Upload `backend/tests/fixtures/scanned_sample.pdf` in **New Submission** to verify Tesseract/RapidOCR extraction and the **OCR** badge.
   - Switch to **Admin** -> **Brand Knowledge Base** to inspect approved assets, then upload `backend/tests/fixtures/off_brand_logo.png` in **New Submission** to verify multimodal visual asset checking.
3. **Dispute-Driven Rule Refinement:**
   - Open **Governance Analytics** as **Admin**, inspect clustered disputes under **Disputed Rules**, and click **Publish Updated Rule** to update the Knowledge Base and re-index FAISS.

---

## Automated Test Suite

Run all **92 automated unit and integration tests** across 10 test modules:

```bash
cd backend
source .venv/bin/activate
pytest -v
```

Test modules included:
- `test_ocr_extraction.py` — Digital, scanned-only, and hybrid PDF extraction pipelines.
- `test_vision_verifier.py` — VLM structured JSON parsing, evidence ID normalization, timeout handling, and OpenCV/Pillow fallbacks.
- `test_dispute_refiner.py` — Semantic dispute clustering, rule synthesis, and offline fallback.
- `test_round3_wiring.py`, `test_round4_fixes.py`, `test_round5_features.py` — End-to-end API wiring, OCR unit filtering, span merging, and audit logging.
- `test_edit_quality.py`, `test_finding_normalisation.py`, `test_llm_merge.py`, `test_verification_mock.py` — Finding deduplication, schema normalization, and substring-safe inline edits.

---

## Project Structure

```text
inkloom-ai/
├── Dockerfile                  # Multi-stage build (Node 20 frontend + Python 3.11 + Tesseract + RapidOCR)
├── docker-compose.yml          # Single-command container deployment with persistent /data volume
├── .env.example                # Template for LLM/VLM provider configuration
├── backend/
│   ├── requirements.txt        # FastAPI, SQLAlchemy, FAISS, Sentence-Transformers, RapidOCR, OpenCV
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint, SPA static serving, startup DB/index warm-up
│   │   ├── database.py         # SQLite engine & session lifecycle
│   │   ├── models.py           # Workspace, KnowledgeEntry, Submission, Finding, Dispute, AuditEvent
│   │   ├── schemas.py          # Pydantic request/response contracts
│   │   ├── seed.py             # Idempotent NovaTech Cloud workspace & sample data seeder
│   │   ├── routers/            # submissions.py, knowledge.py, analytics.py, demo.py
│   │   └── services/           # extraction.py, deterministic.py, verification.py, vision_verifier.py, dispute_refiner.py
│   └── tests/                  # 92 pytest unit & integration tests + fixture generator
└── frontend/
    ├── package.json            # React 19, TypeScript, Vite, Tailwind CSS
    └── src/
        ├── App.tsx             # Role switcher (Creator / Approver / Admin) & navigation shell
        ├── api.ts              # Typed REST API client
        └── pages/              # NewSubmissionView, SubmissionReviewView, BrandKnowledgeView, GovernanceAnalyticsView
```

---

## Product Roadmap

- **Canva and Figma Plugins:** Run brand visual, typography, and copy governance directly inside design canvases before export.
- **CMS & Social Publishing Connectors:** Enforce pre-publication approval gates in WordPress, Webflow, HubSpot, and social schedulers.
- **Browser Extension for Web Editors:** Provide real-time inline brand and fact checks inside Google Docs, Notion, and web email clients.
- **Multi-Workspace Agency Hierarchy:** Cross-client governance dashboards and shared compliance templates for agencies and holding groups.
- **Enterprise SSO & PostgreSQL Scaling:** SAML/OIDC single sign-on, external S3 object storage, and multi-replica PostgreSQL deployment.

---

## Team Caraxes

- **Sanket Jotiba Nandavadekar** — Team Leader; Product & Architecture Lead (`sanketjn0685@gmail.com`)
- **Soham Sunil Ramane** — Backend Developer (`sohamramane318@gmail.com`)
- **Ushank Ajit Rajeshirke** — AI/ML Developer (`ushankrajeshirke2@gmail.com`)
- **Riyesh Gurjar** — Document, OCR & Image Processing Developer (`riyeshgurjar508@gmail.com`)
- **Yash Dilipkumar Jayant** — Frontend Developer & QA Lead (`yashjayant18@gmail.com`)
