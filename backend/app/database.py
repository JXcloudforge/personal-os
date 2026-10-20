from collections.abc import Generator
import os
from pathlib import Path
import sqlite3
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import BACKEND_ROOT, DATA_DIR, normalize_database_url, settings


def _sqlite_path(url: str | URL) -> Path | None:
    parsed = make_url(url) if isinstance(url, str) else url
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, ":memory:", ""):
        return None
    path = Path(parsed.database)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path.resolve()


def _same_database(left: str | URL, right: str | URL) -> bool:
    left_path, right_path = _sqlite_path(left), _sqlite_path(right)
    return left_path is not None and right_path is not None and os.path.normcase(str(left_path)) == os.path.normcase(str(right_path))


def _testing_process() -> bool:
    return os.environ.get("PERSONAL_OS_TESTING") == "1" or "pytest" in sys.modules or any("pytest" in Path(arg).name.lower() for arg in sys.argv)


PRODUCTION_DATABASE_URL = os.environ.get(
    "PERSONAL_OS_PRODUCTION_DATABASE_URL",
    f"sqlite:///{(DATA_DIR / 'personal_os.sqlite3').as_posix()}",
)
PRODUCTION_DATABASE_URL = normalize_database_url(PRODUCTION_DATABASE_URL)


def validate_database_target(database_url: str | URL, runtime_environment: str, *, allow_missing: bool = False) -> None:
    """Prevent production SQLite from silently creating a replacement empty database."""
    if runtime_environment != "production" or allow_missing:
        return
    parsed = make_url(database_url) if isinstance(database_url, str) else database_url
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return
    path = _sqlite_path(parsed)
    if path is not None and not path.is_file():
        raise RuntimeError(
            "Configured production SQLite database is missing; refusing to create an empty database. "
            f"Expected file: {path}"
        )


validate_database_target(settings.database_url, settings.runtime_environment, allow_missing=_testing_process())
DATA_DIR.mkdir(parents=True, exist_ok=True)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@event.listens_for(Engine, "before_cursor_execute")
def _block_test_access_to_production(connection, _cursor, _statement, _parameters, _context, _executemany) -> None:
    if _testing_process() and _same_database(connection.engine.url, PRODUCTION_DATABASE_URL):
        raise RuntimeError("Automated tests are forbidden from accessing the production database")


if _testing_process() and _same_database(settings.database_url, PRODUCTION_DATABASE_URL):
    raise RuntimeError("Automated tests must use an isolated database, not the production database")

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine_options = {"connect_args": connect_args}
if settings.database_url == "sqlite:///:memory:":
    engine_options["poolclass"] = StaticPool
engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
