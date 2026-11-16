"""Owner: Soham. Stub: real SQLAlchemy engine and tables replace this."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./inkloom.db")


def init_db() -> None:
    pass
