"""Isolated regression checks for existing account and Session lifecycle."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AIProvider, SessionToken, User
from app.api import routes


def setup_module():
    Base.metadata.create_all(bind=engine)


def _login_client():
    username = f"session-boundary-{uuid4().hex[:12]}"
    password = "Session-Boundary-Password-2026!"
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client


def test_failed_login_does_not_create_a_server_session():
    username = f"session-failure-{uuid4().hex[:12]}"
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=hash_password("Session-Boundary-Password-2026!")))
        db.commit()
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": username, "password": "incorrect-password"})
    assert response.status_code == 401
    assert "personal_os_session" not in response.cookies
    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        assert db.query(SessionToken).filter_by(user_id=user.id).count() == 0


def test_logout_revokes_server_session_and_clears_browser_cookie():
    client = _login_client()
    token = client.cookies.get("personal_os_session")
    try:
        assert client.get("/api/auth/me").status_code == 200
        response = client.post("/api/auth/logout", headers={"X-CSRF-Token": client.cookies.get("personal_os_csrf")})
        assert response.status_code == 200
        assert "personal_os_session" not in client.cookies
        with TestClient(app) as replay:
            replay.cookies.set("personal_os_session", token)
            assert replay.get("/api/auth/me").status_code == 401
    finally:
        client.close()


def test_inactive_user_and_expired_session_are_rejected_on_protected_reads():
    inactive = _login_client()
    expired = _login_client()
    try:
        inactive_user_id = inactive.get("/api/auth/me").json()["id"]
        with SessionLocal() as db:
            user = db.get(User, inactive_user_id)
            user.is_active = False
            db.commit()
        assert inactive.get("/api/auth/me").status_code == 401
        assert inactive.get("/api/tasks").status_code == 401
        with SessionLocal() as db:
            user = db.get(User, inactive_user_id)
            username = user.username
        rejected_login = TestClient(app)
        try:
            response = rejected_login.post("/api/auth/login", json={"username": username, "password": "Session-Boundary-Password-2026!"})
            assert response.status_code == 401
            assert "personal_os_session" not in response.cookies
        finally:
            rejected_login.close()

        expired_user_id = expired.get("/api/auth/me").json()["id"]
        with SessionLocal() as db:
            session = db.query(SessionToken).filter_by(user_id=expired_user_id).one()
            session.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
            db.commit()
        assert expired.get("/api/auth/me").status_code == 401
        assert expired.get("/api/tasks").status_code == 401
    finally:
        inactive.close()
        expired.close()


def test_shared_environment_provider_key_is_never_used_by_web_user(monkeypatch):
    client = _login_client()
    used_keys = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "synthetic response"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, *, headers, json):
            used_keys.append(headers.get("Authorization"))
            return FakeResponse()

    monkeypatch.setattr(routes.settings, "deepseek_api_key", "synthetic-shared-provider-key")
    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeClient)
    try:
        user_id = client.get("/api/auth/me").json()["id"]
        with SessionLocal() as db:
            provider = db.query(AIProvider).filter_by(user_id=user_id, provider="deepseek").one_or_none()
            assert provider is None
        response = client.post(
            "/api/ai/chat",
            headers={"X-CSRF-Token": client.cookies.get("personal_os_csrf")},
            json={"prompt": "synthetic audit", "consent_granted": True},
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert "synthetic-shared-provider-key" not in response.text
        assert used_keys == []
        with SessionLocal() as db:
            provider = db.query(AIProvider).filter_by(user_id=user_id, provider="deepseek").one()
            assert provider.enabled is False
            assert provider.encrypted_api_key is None
    finally:
        client.close()
