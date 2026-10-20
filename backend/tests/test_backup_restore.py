import importlib.util
import sqlite3
from pathlib import Path

import pytest


_backup_script = Path(__file__).resolve().parents[2] / "scripts" / "backup_database.py"
_spec = importlib.util.spec_from_file_location("personal_os_backup_script", _backup_script)
_backup_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backup_module)


def test_backup_restore_uses_sqlite_backup_api_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id))")
        db.execute("INSERT INTO users(id) VALUES (7)")
        db.execute("INSERT INTO tasks(id,user_id) VALUES (11,7)")
    _backup_module.backup(source, backup)
    _backup_module.restore(backup, restored)
    for copied in (backup, restored):
        with sqlite3.connect(f"file:{copied.as_posix()}?mode=ro", uri=True) as db:
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT id,user_id FROM tasks").fetchall() == [(11, 7)]
    with pytest.raises(FileExistsError):
        _backup_module.restore(backup, restored)
    with pytest.raises(FileExistsError):
        _backup_module.backup(source, backup)


def test_current_alembic_head_is_accepted_for_local_backup(tmp_path):
    from alembic import command
    from alembic.config import Config

    from app.services import backup_service

    database = tmp_path / "current-head.sqlite3"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    summary = backup_service._db_summary(database)
    assert summary["revision"] == backup_service.CURRENT_REVISION
    assert summary["schema_compatible"] is True
