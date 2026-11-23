"""Owner: Soham. SQLAlchemy engine and session (SQLite by default; PostgreSQL via DATABASE_URL)."""
import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

log = logging.getLogger(__name__)

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


def add_missing_columns(target: Engine | None = None) -> list[str]:
    """Add columns the models declare but an existing table lacks.

    ``create_all`` creates missing tables but never alters existing ones, so a database
    created by an earlier release fails at query time (for example, ``no such column:
    knowledge_entries.asset_type``). New columns are added as nullable, because existing
    rows cannot be given a NOT NULL value without a default. Returns the ``table.column``
    names that were added.
    """
    from app import models  # noqa: F401  (registers every table on Base.metadata)

    target = target or engine
    added: list[str] = []
    with target.begin() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() will build it
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                col_type = column.type.compile(dialect=target.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )
                added.append(f"{table.name}.{column.name}")
                log.warning("Schema migration: added column %s.%s", table.name, column.name)
    return added


def init_db() -> None:
    """Create missing tables, then bring existing tables up to the current schema. Idempotent."""
    from app import models  # noqa: F401  (ensures all ORM models are registered on Base)

    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
