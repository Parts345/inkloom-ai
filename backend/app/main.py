from dotenv import load_dotenv
load_dotenv()
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import demo, submissions
from app.seed import seed_demo_workspace

STATIC_DIR = Path(os.getenv("FRONTEND_DIST", "/app/frontend/dist")).resolve()
CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_demo_workspace()  # Idempotent: no-op if "NovaTech Cloud" already exists.
    yield


app = FastAPI(title="Inkloom AI", version="0.2.0", lifespan=lifespan)

# CORS is needed only for the Vite dev server; the container serves API and UI from one origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(demo.router, prefix="/api")
app.include_router(submissions.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


if (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)

    candidate = (STATIC_DIR / full_path).resolve()
    # Serve real files only when they resolve inside the dist directory (blocks ../ traversal).
    if candidate.is_file() and STATIC_DIR in candidate.parents:
        return FileResponse(candidate)

    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)  # Client-side routing fallback.
    raise HTTPException(status_code=404)
