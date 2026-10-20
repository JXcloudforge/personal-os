"""Production startup must reject an unmigrated synthetic SQLite database."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app import schema_startup


main = importlib.import_module("app.main")
BACKEND = Path(__file__).resolve().parents[1]


def _upgrade(path: Path, revision: str) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, revision)


def _production(monkeypatch, path: Path) -> list[str]:
    monkeypatch.setattr(settings, "runtime_environment", "production")
    monkeypatch.setattr(settings, "secret_key", "synthetic-schema-guard-secret-over-32-characters")
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path.as_posix()}")
    started: list[str] = []

    async def idle_scheduler():
        await asyncio.Event().wait()

    def fake_scheduler():
        started.append("scheduler")
        return idle_scheduler()

    monkeypatch.setattr(main, "_local_backup_scheduler", fake_scheduler)
    return started


def _expect_startup_rejected(monkeypatch, path: Path) -> None:
    started = _production(monkeypatch, path)
    with pytest.raises(RuntimeError, match="schema|migration|database"):
        with TestClient(main.app) as client:
            client.get("/api/health")
    assert started == []


def test_current_head_starts_and_health_is_ready(monkeypatch, tmp_path):
    path = tmp_path / "head.sqlite3"
    _upgrade(path, "head")
    _production(monkeypatch, path)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}


def test_old_v033_head_rejected_before_scheduler_even_under_pytest(monkeypatch, tmp_path):
    path = tmp_path / "v033.sqlite3"
    _upgrade(path, "f3a4b5c6d7e8")
    _expect_startup_rejected(monkeypatch, path)


def test_conflicting_migration_heads_rejected_before_scheduler(monkeypatch, tmp_path):
    path = tmp_path / "head.sqlite3"
    _upgrade(path, "head")

    class ConflictingScripts:
        def __init__(self, _):
            pass

        def get_heads(self):
            return ("a6c7d8e9f0a1", "another_head")

    monkeypatch.setattr(schema_startup, "ScriptDirectory", ConflictingScripts)
    _expect_startup_rejected(monkeypatch, path)


@pytest.mark.parametrize("case", ["missing-file", "missing-table", "unknown", "multiple", "unreadable"])
def test_unusable_schema_rejected_before_scheduler(monkeypatch, tmp_path, case):
    path = tmp_path / f"{case}.sqlite3"
    if case == "unreadable":
        path.write_bytes(b"synthetic invalid sqlite bytes")
    elif case != "missing-file":
        with sqlite3.connect(path) as connection:
            if case != "missing-table":
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.execute("INSERT INTO alembic_version VALUES ('unknown_revision')")
                if case == "multiple":
                    connection.execute("INSERT INTO alembic_version VALUES ('a6c7d8e9f0a1')")
    _expect_startup_rejected(monkeypatch, path)


def test_failed_migration_still_rejected_then_clean_upgrade_starts(monkeypatch, tmp_path):
    failed = tmp_path / "failed.sqlite3"
    _upgrade(failed, "f3a4b5c6d7e8")
    with sqlite3.connect(failed) as connection:
        connection.execute("CREATE TABLE registration_invites (conflict TEXT)")
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{failed.as_posix()}")
    with pytest.raises(Exception):
        command.upgrade(config, "head")
    _expect_startup_rejected(monkeypatch, failed)

    recoverable = tmp_path / "recoverable.sqlite3"
    _upgrade(recoverable, "f3a4b5c6d7e8")
    _expect_startup_rejected(monkeypatch, recoverable)
    _upgrade(recoverable, "head")
    _production(monkeypatch, recoverable)
    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200


def test_repeated_head_checks_leave_database_bytes_and_records_unchanged(monkeypatch, tmp_path):
    path = tmp_path / "read-only.sqlite3"
    _upgrade(path, "head")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users(username,password_hash,is_active) VALUES (?,?,?)", ("synthetic", "synthetic-hash", 1))
    before = hashlib.sha256(path.read_bytes()).digest()
    _production(monkeypatch, path)
    for _ in range(3):
        with TestClient(main.app) as client:
            assert client.get("/api/health").status_code == 200
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "a6c7d8e9f0a1"
