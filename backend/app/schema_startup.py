"""Read-only production startup check for the configured SQLite schema."""

import sqlite3
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .config import BACKEND_ROOT, settings


def validate_schema_revision() -> None:
    """Fail startup unless the database has the sole revision required by this code."""
    try:
        heads = ScriptDirectory(str(BACKEND_ROOT / "alembic")).get_heads()
    except Exception:
        raise RuntimeError("Database schema check failed: migration head is unavailable") from None
    if len(heads) != 1:
        raise RuntimeError("Database schema check failed: migration scripts require one head")

    try:
        url = make_url(settings.database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("unsupported database target")
        database = Path(url.database)
        if not database.is_file():
            raise ValueError("database file is absent")
        # mode=ro prevents SQLite from creating a missing file or writing to an
        # existing one. This check never creates a SQLAlchemy engine/session.
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            revisions = connection.execute("SELECT version_num FROM alembic_version LIMIT 2").fetchall()
    except (ArgumentError, ValueError, OSError, sqlite3.Error):
        raise RuntimeError("Database schema check failed: database or revision is unavailable") from None

    if len(revisions) != 1 or revisions[0][0] != heads[0]:
        raise RuntimeError("Database schema check failed: run Alembic upgrade head before startup")
