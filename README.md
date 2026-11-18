# Inkloom AI (Team Caraxes)

Pre-publication brand governance platform. Modular monolith: FastAPI backend and
React + TypeScript + Tailwind frontend, served from one container.

## Run with Docker
    docker compose up --build
    open http://localhost:7860

## Run locally
    cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
    cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api to :8000)

## Demo mode
DEMO_MODE=true (default) lets judges switch roles (Creator / Approver / Admin) from the
header with no login. Set DEMO_MODE=false to require JWT authentication.

## Status
Skeleton. Extraction, deterministic checks, LLM verification and persistence are stubs
that satisfy the interfaces in app/services/*.py; each owner replaces their stub.


running 
t1:

cd ~/COMP/inkloom-ai-main/frontend
npm run dev

t2:

cd ~/COMP/inkloom-ai-main/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000 2>&1 | tee server.log

podman-compose up -d --build
## Configure the LLM verifier (optional)

The app works without an LLM key using deterministic checks. To enable LLM verification:

    cp .env.example .env     # then edit .env and set LLM_API_KEY

`.env` is git-ignored and excluded from the Docker image. Never paste a key into chat, issues, or commits. Rotate any key that was shared.

After changing `.env`, recreate the container so it picks up the new value:

    docker compose down && docker compose up -d --build
