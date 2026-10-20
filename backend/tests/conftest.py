"""Select a unique isolated test database before any app module is imported."""
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import URL

_production_db = Path(__file__).resolve().parents[2] / "data" / "personal_os.sqlite3"
_test_db = Path(tempfile.gettempdir()) / f"personal_os_pytest_{uuid4().hex}.sqlite3"
os.environ["PERSONAL_OS_TESTING"] = "1"
os.environ["PERSONAL_OS_PRODUCTION_DATABASE_URL"] = URL.create("sqlite", database=str(_production_db)).render_as_string(hide_password=False)
os.environ["PERSONAL_OS_DATABASE_URL"] = URL.create("sqlite", database=str(_test_db)).render_as_string(hide_password=False)
os.environ["PERSONAL_OS_RUNTIME_ENVIRONMENT"] = "test"
os.environ["PERSONAL_OS_COOKIE_SECURE"] = "false"
os.environ["PERSONAL_OS_TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["PERSONAL_OS_SECRET_KEY"] = "pytest-only-not-a-production-secret-value"


def pytest_sessionfinish(session, exitstatus):
    database = sys.modules.get("app.database")
    if database is not None:
        database.engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_test_db}{suffix}").unlink(missing_ok=True)
