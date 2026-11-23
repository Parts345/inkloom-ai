# Inkloom AI — Intelligent Brand Governance Platform (Team Caraxes)

Pre-publication brand governance platform built for **CodeZen India 2026 (Round 2: The Build)**. Modular monolith combining a **FastAPI + FAISS + Sentence Transformers** backend with a **React + TypeScript + Tailwind CSS** frontend, served from a single container.

* **Live Demo:** https://inkloom-ai-production-97765d64.up.railway.app/
* **Repository:** https://github.com/Parts345/inkloom-ai

---

## Core Capabilities

* **Multi-Format Ingestion:** Accepts pasted text, `.txt`, `.docx` (paragraphs, headings, run text colors, embedded images), `.pdf`, and standalone `.png` / `.jpg` assets.
* **Hybrid Verification Pipeline:** Runs deterministic Python/OpenCV/WCAG validators concurrently (`asyncio.gather`) with FAISS (`all-MiniLM-L6-v2`) RAG retrieval and schema-validated LLM verification (with automatic deterministic fallback).
* **Explainable Findings & Inline Diffs:** Flags brand terminology, factual claims, pricing/expiry mismatches, tone, palette consistency, and WCAG 2.2 AA contrast/accessibility gaps with source evidence and minimal suggested edits.
* **Human-in-the-Loop Governance:** Role-based workflow (`Creator`, `Approver`, `Admin`), versioned revisions, append-only audit trail, and live Governance Analytics.

---

## Demo Mode (No Login Required)

`DEMO_MODE=true` is enabled by default so evaluators can switch roles (**Creator / Approver / Admin**) directly from the top-right header without authentication friction. Set `DEMO_MODE=false` to enforce JWT authentication.

---

## Configure the LLM Verifier (Optional)

The platform works out of the box without an external API key using deterministic checks and the rule-grounded verifier. To enable live LLM verification (Groq or Gemini OpenAI-compatible endpoint):

```bash
cp .env.example .env
# Edit .env and set LLM_PROVIDER, LLM_MODEL, and LLM_API_KEY
```

`.env` is git-ignored and excluded from the Docker image. Never commit API keys to source control.

---

## Run with Docker or Podman

### Using Docker Compose
```bash
docker compose up -d --build
# Open http://localhost:7860
```

### Using Podman Compose
```bash
podman-compose up -d --build
# Open http://localhost:7860
```

After updating `.env`, recreate the container so it picks up the new environment variables:
```bash
docker compose down && docker compose up -d --build
# Or with Podman:
podman-compose down && podman-compose up -d --build
```

---

## Run Locally (Development)

### Terminal 1 — Backend (FastAPI)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000 2>&1 | tee server.log
```

### Terminal 2 — Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173 (proxies /api requests to :8000)
```

---

## Team Caraxes

* **Sanket Jotiba Nandavadekar** — Team Leader; Product & Architecture Lead
* **Soham Sunil Ramane** — Backend Developer
* **Ushank Ajit Rajeshirke** — AI/ML Developer
* **Riyesh Gurjar** — Document & Image Processing Developer
* **Yash Dilipkumar Jayant** — Frontend Developer & QA Lead

## Round 3 features

- **Scanned-PDF OCR.** Pages with no text layer are rendered and read with Tesseract (`tesseract-ocr` is installed in the image). Extraction logs `ocr_units=N` in the audit trail, and the review page shows an OCR badge.
- **Approved visual assets.** Approvers and Admins upload logos, banners, or palettes in Brand Knowledge Base. Campaign images are checked against them by a vision model (`VLM_MODEL`). Without a vision-capable model, or without an API key, deterministic OpenCV and Pillow checks run instead.
- **Dispute-driven rule refinement.** Governance Analytics clusters creator disputes by rule and proposes an updated rule. Admins apply it with one click, which re-indexes the Knowledge Base and writes to the audit trail. Disputes raised before the last update are not proposed again.

### Environment

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `groq` or `gemini`. Sets the endpoint when `LLM_BASE_URL` is unset. |
| `LLM_API_KEY` | Key for the text, dispute, and vision checks. |
| `LLM_MODEL` | Text and dispute model, for example `openai/gpt-oss-120b`. |
| `VLM_MODEL` | Vision model. Set this to a model your provider lists as vision-capable. |

### Smoke test

    cd backend && python -m tests.fixtures.make_fixtures

Upload `scanned_sample.pdf` to check OCR. Upload `off_brand_logo.png` after an approved logo exists, to check the vision check. See `backend/tests/fixtures/README.md`.

### Governance analytics

- `approval_rate` is approved divided by (approved + rejected) submissions.
- `top_rules` and `disputed_rules` group findings by the rule they cite.
