"""Owner: Soham. SQLAlchemy engine and session (SQLite by default; PostgreSQL via DATABASE_URL)."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./inkloom.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields one SQLAlchemy session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all database tables (safe and idempotent on every startup)."""
    from app import models  # noqa: F401  (ensures all ORM models are registered on Base)

    Base.metadata.create_all(bind=engine)