"""Synthetic regression tests for user-owned model credentials."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AIProvider, AgentRun, ChatConversation, ChatMessage, SearchProvider, User
from app.services import agent_runner
from app.services.provider_service import encrypt_api_key
from app.services.tool_registry import ToolDenied, _get_search_key
from app.api import routes


def setup_module():
    Base.metadata.create_all(bind=engine)


def _client():
    username = f"provider-boundary-{uuid.uuid4().hex[:12]}"
    password = "Provider-Boundary-Password-2026!"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        user_id = user.id
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client, user_id


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def _remove_user(user_id):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is not None:
            db.delete(user)
            db.commit()


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "synthetic model response"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


class _CaptureClient:
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return _FakeResponse()


def test_ai_chat_uses_only_calling_users_enabled_credential(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(routes.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(routes.httpx, "AsyncClient", _CaptureClient)
    owner, owner_id = _client()
    other, other_id = _client()
    personal_key = "synthetic-user-a-private-key"
    try:
        with SessionLocal() as db:
            db.add(AIProvider(user_id=owner_id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash", encrypted_api_key=encrypt_api_key(personal_key), enabled=True))
            db.commit()

        allowed = owner.post("/api/ai/chat", headers=_csrf(owner), json={"prompt": "synthetic prompt", "consent_granted": True})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["content"] == "synthetic model response"
        assert [item[1]["headers"]["Authorization"] for item in _CaptureClient.calls] == [f"Bearer {personal_key}"]

        denied = other.post("/api/ai/chat", headers=_csrf(other), json={"prompt": "synthetic prompt", "consent_granted": True})
        assert denied.status_code == 503
        assert denied.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert "设置" in denied.json()["detail"]["message"]
        assert personal_key not in denied.text and "synthetic-shared-environment-key" not in denied.text
        assert len(_CaptureClient.calls) == 1
        assert other.get("/api/settings/ai/providers").json()[0]["configured"] is False
    finally:
        owner.close()
        other.close()
        _remove_user(owner_id)
        _remove_user(other_id)


def test_disabled_user_provider_does_not_fall_back_to_shared_key(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(routes.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(routes.httpx, "AsyncClient", _CaptureClient)
    client, user_id = _client()
    try:
        with SessionLocal() as db:
            db.add(AIProvider(user_id=user_id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash", encrypted_api_key=encrypt_api_key("synthetic-disabled-user-key"), enabled=False))
            db.commit()
        response = client.post("/api/ai/chat", headers=_csrf(client), json={"prompt": "synthetic prompt", "consent_granted": True})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_DISABLED"
        assert not _CaptureClient.calls
        assert "synthetic-disabled-user-key" not in response.text
    finally:
        client.close()
        _remove_user(user_id)


def test_provider_connection_test_requires_users_own_enabled_key(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(routes.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(routes.httpx, "AsyncClient", _CaptureClient)
    client, user_id = _client()
    try:
        response = client.post("/api/settings/ai/providers/deepseek/test", headers=_csrf(client))
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert not _CaptureClient.calls
    finally:
        client.close()
        _remove_user(user_id)


def test_agent_chat_rejects_shared_key_before_model_request(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(agent_runner.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", _CaptureClient)
    client, user_id = _client()
    try:
        response = client.post("/api/agent/chat", headers=_csrf(client), json={"message": "Answer with a short sentence."})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert "设置" in response.json()["detail"]["message"]
        assert not _CaptureClient.calls
    finally:
        client.close()
        _remove_user(user_id)


def test_agent_chat_uses_only_the_authenticated_users_personal_key(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(agent_runner.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", _CaptureClient)
    owner, owner_id = _client()
    other, other_id = _client()
    personal_key = "synthetic-agent-user-a-key"
    try:
        with SessionLocal() as db:
            db.add(AIProvider(user_id=owner_id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash", encrypted_api_key=encrypt_api_key(personal_key), enabled=True))
            db.commit()
        allowed = owner.post("/api/agent/chat", headers=_csrf(owner), json={"message": "Answer with one sentence."})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["content"] == "synthetic model response"
        denied = other.post("/api/agent/chat", headers=_csrf(other), json={"message": "Answer with one sentence."})
        assert denied.status_code == 503
        assert denied.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert [item[1]["headers"]["Authorization"] for item in _CaptureClient.calls] == [f"Bearer {personal_key}"]
        assert personal_key not in denied.text and "synthetic-shared-environment-key" not in denied.text
    finally:
        owner.close()
        other.close()
        _remove_user(owner_id)
        _remove_user(other_id)


def test_agent_plan_and_evening_review_reject_shared_key_but_keep_offline_mock(monkeypatch):
    _CaptureClient.calls = []
    monkeypatch.setattr(agent_runner.settings, "deepseek_api_key", "synthetic-shared-environment-key")
    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", _CaptureClient)
    with SessionLocal() as db:
        user = User(username=f"provider-agent-{uuid.uuid4().hex[:12]}", password_hash="unused")
        db.add(user)
        db.commit()
        user_id = user.id
        with pytest.raises(agent_runner.AgentRunError) as plan_error:
            asyncio.run(agent_runner.run_plan(db, user_id, 20, None, "deepseek"))
        assert plan_error.value.code == "PROVIDER_NOT_CONFIGURED"
        with pytest.raises(agent_runner.AgentRunError) as evening_error:
            asyncio.run(agent_runner.run_evening(db, user_id, "deepseek"))
        assert evening_error.value.code == "PROVIDER_NOT_CONFIGURED"
        assert not _CaptureClient.calls
        _, _, _, _ = asyncio.run(agent_runner.run_plan(db, user_id, 20, None, "mock"))
        assert not _CaptureClient.calls
        db.delete(user)
        db.commit()


def test_knowledge_refinement_rejects_shared_key_without_outbound_call(monkeypatch):
    from app.api import knowledge_routes
    _CaptureClient.calls = []
    called = []
    monkeypatch.setattr("app.services.provider_service.settings.deepseek_api_key", "synthetic-shared-environment-key")
    async def fake_refine(*args):
        called.append(args)
        return None
    monkeypatch.setattr(knowledge_routes, "refine_text", fake_refine)
    client, user_id = _client()
    try:
        with SessionLocal() as db:
            conversation = ChatConversation(id=str(uuid.uuid4()), user_id=user_id, title="Synthetic source", domain="general")
            db.add(conversation)
            db.flush()
            message = ChatMessage(user_id=user_id, conversation_id=conversation.id, role="assistant", content="Synthetic assistant source text.", status="completed")
            db.add(message)
            db.commit()
            payload = {"conversation_id": conversation.id, "message_id": message.id}
        response = client.post("/api/knowledge/refine-from-message", headers=_csrf(client), json=payload)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert not called and not _CaptureClient.calls
        assert "synthetic-shared-environment-key" not in response.text
    finally:
        client.close()
        _remove_user(user_id)


def test_provider_settings_do_not_claim_shared_key_is_user_configured(monkeypatch):
    from app.services.provider_service import ensure_provider, provider_view
    monkeypatch.setattr("app.services.provider_service.settings.deepseek_api_key", "synthetic-shared-environment-key")
    with SessionLocal() as db:
        user = User(username=f"provider-view-{uuid.uuid4().hex[:12]}", password_hash="unused")
        db.add(user)
        db.flush()
        provider = ensure_provider(db, "deepseek", user.id)
        view = provider_view(provider)
        assert view["configured"] is False
        assert view["credential_source"] == "none"
        assert view["key_hint"] is None
        db.delete(user)
        db.commit()


def test_tool_registry_never_uses_another_users_search_credential():
    from app.models import SearchProvider
    with SessionLocal() as db:
        owner = User(username=f"search-owner-{uuid.uuid4().hex[:8]}", password_hash="unused")
        other = User(username=f"search-other-{uuid.uuid4().hex[:8]}", password_hash="unused")
        db.add_all([owner, other])
        db.flush()
        db.add(SearchProvider(user_id=owner.id, provider="tavily", encrypted_api_key=encrypt_api_key("synthetic-user-a-search-key"), enabled=True))
        db.flush()
        assert _get_search_key(db, owner.id) == "synthetic-user-a-search-key"
        with pytest.raises(ToolDenied, match="SEARCH_PROVIDER_NOT_CONFIGURED"):
            _get_search_key(db, other.id)
        db.delete(owner)
        db.delete(other)
        db.commit()
