from pathlib import Path

import pytest

from app.config import settings
from app.main import validate_runtime_security
from scripts.initialize_runtime_security import initialize


def test_production_startup_rejects_missing_secret_and_insecure_cookie(monkeypatch):
    monkeypatch.setattr(settings, "runtime_environment", "production")
    monkeypatch.setattr(settings, "secret_key", None)
    monkeypatch.setattr(settings, "cookie_secure", True)
    with pytest.raises(RuntimeError, match="PERSONAL_OS_SECRET_KEY"):
        validate_runtime_security()
    monkeypatch.setattr(settings, "secret_key", "test-only-secret-with-at-least-32-characters")
    monkeypatch.setattr(settings, "cookie_secure", False)
    with pytest.raises(RuntimeError, match="PERSONAL_OS_COOKIE_SECURE"):
        validate_runtime_security()
    monkeypatch.setattr(settings, "cookie_secure", True)
    validate_runtime_security()


def test_runtime_secret_initializer_preserves_fernet_and_never_uses_default(tmp_path):
    env_file = Path(tmp_path) / ".env"
    env_file.write_text("PERSONAL_OS_CREDENTIAL_MASTER_KEY=fernet-fixture-value\n", encoding="utf-8")
    initialize(env_file)
    values = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert values["PERSONAL_OS_CREDENTIAL_MASTER_KEY"] == "fernet-fixture-value"
    assert values["PERSONAL_OS_RUNTIME_ENVIRONMENT"] == "production"
    assert values["PERSONAL_OS_COOKIE_SECURE"] == "true"
    assert len(values["PERSONAL_OS_SECRET_KEY"]) >= 32
    assert values["PERSONAL_OS_SECRET_KEY"] != "change-me-before-network-use"
    generated = values["PERSONAL_OS_SECRET_KEY"]
    initialize(env_file)
    assert dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)["PERSONAL_OS_SECRET_KEY"] == generated
