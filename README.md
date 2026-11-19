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
