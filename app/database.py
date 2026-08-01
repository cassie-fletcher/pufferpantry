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
    """Create tables that don't exist and add any missing columns.

    Safe to call repeatedly. create_all only creates missing TABLES — it
    never alters existing ones — so additive columns are applied here with
    explicit ALTERs (checked against pragma first: idempotent). Never drops
    or rewrites anything; see the data-safety rules.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_recipe_columns()


def _ensure_recipe_columns() -> None:
    import json as _json

    from sqlalchemy import text

    with engine.begin() as conn:
        existing = {
            row[1] for row in conn.execute(text("PRAGMA table_info(recipes)"))
        }
        if "photo_filenames" not in existing:
            conn.execute(text("ALTER TABLE recipes ADD COLUMN photo_filenames JSON"))
            # Backfill: every existing single photo becomes a one-page list.
            for rid, fname in conn.execute(
                text("SELECT id, photo_filename FROM recipes WHERE photo_filename IS NOT NULL")
            ).fetchall():
                conn.execute(
                    text("UPDATE recipes SET photo_filenames = :v WHERE id = :id"),
                    {"v": _json.dumps([fname]), "id": rid},
                )
        if "sub_recipe_yields" not in existing:
            conn.execute(text("ALTER TABLE recipes ADD COLUMN sub_recipe_yields JSON"))
