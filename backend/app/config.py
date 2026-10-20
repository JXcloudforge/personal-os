from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"


class Settings(BaseSettings):
    app_name: str = "Personal OS"
    # No development or production secret is silently supplied. Production
    # startup validates this value; current opaque DB-backed sessions do not
    # depend on it, so initializing it does not invalidate existing sessions.
    secret_key: str | None = None
    runtime_environment: Literal["development", "test", "production"] = "development"
    database_url: str = f"sqlite:///{(DATA_DIR / 'personal_os.sqlite3').as_posix()}"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    credential_master_key: str | None = None
    cookie_secure: bool = False
    session_days: int = 14
    frontend_dist: str = str(APP_ROOT / "frontend" / "dist")
    allowed_origins: str = ""
    trusted_hosts: str = "127.0.0.1,localhost,testserver"
    app_timezone: str = "Asia/Shanghai"

    # Resolve the private backend environment file from this module, not the
    # shell's working directory; Windows services and CLI launches vary cwd.
    model_config = SettingsConfigDict(env_file=str(BACKEND_ROOT / ".env"), env_prefix="PERSONAL_OS_", extra="ignore")


settings = Settings()


def normalize_database_url(database_url: str) -> str:
    """Resolve relative SQLite paths from the backend directory, never process cwd."""
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return database_url
    if parsed.database.startswith("file:"):
        return database_url
    path = Path(parsed.database)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return parsed.set(database=str(path.resolve())).render_as_string(hide_password=False)


settings.database_url = normalize_database_url(settings.database_url)
