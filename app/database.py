from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# connect_args needed for SQLite to allow usage across threads (FastAPI is multi-threaded)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, then close it.

    Used as a FastAPI dependency — FastAPI calls this automatically for any
    endpoint that declares `db: Session = Depends(get_db)`.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables that don't exist yet. Safe to call repeatedly."""
    Base.metadata.create_all(bind=engine)
