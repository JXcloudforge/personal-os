import os
import subprocess
import sys
import asyncio
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
import httpx

from app.models import AIProvider
from app.services import provider_service


def run_process(database_url: str, master_key: str, code: str) -> str:
    env = os.environ.copy()
    env["PERSONAL_OS_DATABASE_URL"] = database_url
    env["PERSONAL_OS_CREDENTIAL_MASTER_KEY"] = master_key
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_provider_credential_survives_process_restart_and_delete(tmp_path):
    db_path = tmp_path / "credential-restart.sqlite3"
    database_url = f"sqlite:///{db_path.as_posix()}"
    master_key = Fernet.generate_key().decode()

    run_process(database_url, master_key, """
from app.database import Base, SessionLocal, engine
from app.models import AIProvider, User
from app.auth.security import hash_password
from app.services.provider_service import encrypt_api_key
Base.metadata.create_all(bind=engine)
db = SessionLocal()
user = User(username='restart-test', password_hash=hash_password('restart-test-password'))
db.add(user)
db.flush()
db.add(AIProvider(user_id=user.id, provider='deepseek', base_url='https://api.deepseek.com', model='deepseek-flash', encrypted_api_key=encrypt_api_key('generated-test-secret'), enabled=True, status='configured'))
db.commit()
db.close()
""")

    assert run_process(database_url, master_key, """
from app.database import SessionLocal
from app.models import AIProvider
from app.services.provider_service import decrypt_api_key
db = SessionLocal()
provider = db.query(AIProvider).filter_by(provider='deepseek').one()
assert decrypt_api_key(provider) == 'generated-test-secret'
print('restart-decryption-ok')
""") == "restart-decryption-ok"

    assert run_process(database_url, master_key, """
from app.database import SessionLocal
from app.models import AIProvider
db = SessionLocal()
provider = db.query(AIProvider).filter_by(provider='deepseek').one()
provider.encrypted_api_key = None
provider.enabled = False
db.commit()
print('deleted')
""") == "deleted"

    assert run_process(database_url, master_key, """
from app.database import SessionLocal
from app.models import AIProvider
db = SessionLocal()
provider = db.query(AIProvider).filter_by(provider='deepseek').one()
assert provider.encrypted_api_key is None
print('deleted-cannot-be-used')
""") == "deleted-cannot-be-used"


def test_default_env_file_is_backend_absolute_path():
    from app.config import BACKEND_ROOT, Settings

    assert Path(Settings.model_config["env_file"]) == BACKEND_ROOT / ".env"


def test_provider_connection_error_categories_are_distinct():
    class MockResponse:
        def __init__(self, code: int, payload: dict):
            self.status_code = code
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            response = httpx.Response(self.status_code, json=self._payload, request=request)
            raise httpx.HTTPStatusError("mocked", request=request, response=response)

    class MockClient:
        def __init__(self, response, **_kwargs):
            self.response = response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return self.response

    provider = AIProvider(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash")
    cases = [
        (401, {"error": {"message": "invalid key"}}, "auth_failed"),
        (402, {"error": {"message": "insufficient balance"}}, "insufficient_balance"),
        (404, {"error": {"message": "model not found"}}, "model_not_found"),
    ]
    for code, payload, expected in cases:
        response = MockResponse(code, payload)
        with patch.object(provider_service.httpx, "AsyncClient", lambda **kwargs: MockClient(response, **kwargs)):
            result = asyncio.run(provider_service.test_connection(provider, "generated-test-key"))
        assert result.status == expected
