from pathlib import Path

import pytest
from sqlalchemy.engine import URL, make_url

from app.config import BACKEND_ROOT, normalize_database_url
from app.database import validate_database_target


def test_relative_sqlite_database_url_is_anchored_to_backend_not_working_directory(tmp_path, monkeypatch):
    relative = "sqlite:///../data/personal_os.sqlite3"
    expected = (BACKEND_ROOT / "../data/personal_os.sqlite3").resolve()

    monkeypatch.chdir(tmp_path)
    first = normalize_database_url(relative)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    second = normalize_database_url(relative)

    assert Path(make_url(first).database) == expected
    assert Path(make_url(second).database) == expected


def test_production_refuses_missing_sqlite_file_without_creating_it(tmp_path):
    missing = tmp_path / "must-not-be-created.sqlite3"
    url = URL.create("sqlite", database=str(missing))

    with pytest.raises(RuntimeError, match="refusing to create an empty database"):
        validate_database_target(url, "production")

    assert not missing.exists()


def test_test_runtime_can_use_a_new_isolated_sqlite_database(tmp_path):
    missing = tmp_path / "pytest-isolated.sqlite3"
    url = URL.create("sqlite", database=str(missing))
    validate_database_target(url, "test", allow_missing=False)
    assert not missing.exists()
