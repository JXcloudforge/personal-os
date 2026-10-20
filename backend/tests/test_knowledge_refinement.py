from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AgentModelUsage, AgentRun, ChatConversation, ChatMessage, KnowledgeCard, User
from app.services import knowledge_refinement_adapter as adapter


def setup_module():
    Base.metadata.create_all(bind=engine)


def _client(domain: str = "stem"):
    username, password = f"refine_{uuid4().hex[:10]}", "refine-test-password"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user); db.flush()
        conversation = ChatConversation(id=str(uuid4()), user_id=user.id, title="Refinement source", domain=domain)
        db.add(conversation); db.flush()
        message = ChatMessage(user_id=user.id, conversation_id=conversation.id, role="assistant", content="A backend executes a requested tool and returns its result using the matching call identifier.", status="completed")
        db.add(message); db.commit()
        ids = user.id, conversation.id, message.id
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client, ids


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def test_refinement_uses_one_owned_message_and_records_real_usage_without_saving_card(monkeypatch):
    client, (user_id, conversation_id, message_id) = _client()
    monkeypatch.setattr("app.api.knowledge_routes.require_user_api_key", lambda _provider, _user_id: "mock-provider-secret")
    async def fake_refine(provider, api_key, text):
        assert api_key == "mock-provider-secret"
        assert text == "executes a requested tool"
        return adapter.KnowledgeRefinement(worth_saving=True, title="Tool execution boundary", summary="Backend executes model-requested tools.", content_markdown="The model requests; the backend executes and returns the result.", limitations=["Provider-specific details require source review."]), {"prompt_tokens": 41, "completion_tokens": 28}
    monkeypatch.setattr("app.api.knowledge_routes.refine_text", fake_refine)
    payload = {"conversation_id": conversation_id, "message_id": message_id, "selected_text": "executes a requested tool"}
    response = client.post("/api/knowledge/refine-from-message", headers=_csrf(client), json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate"]["title"] == "Tool execution boundary"
    assert body["usage"] == {"input_tokens": 41, "output_tokens": 28, "status": "completed"}
    with SessionLocal() as db:
        run = db.get(AgentRun, body["run_id"])
        usage = db.scalar(select(AgentModelUsage).where(AgentModelUsage.run_id == run.id))
        original = db.get(ChatMessage, message_id)
        assert run.user_id == user_id and run.workflow == "knowledge_refinement" and run.status == "completed"
        assert run.authorized_scope == f"single_assistant_message:{message_id}"
        assert usage.conversation_id == conversation_id and usage.message_id == message_id
        assert (usage.input_tokens, usage.output_tokens) == (41, 28)
        assert "executes a requested tool" in original.content
        assert db.scalar(select(KnowledgeCard).where(KnowledgeCard.user_id == user_id)) is None
        original_content = original.content
    saved = client.post("/api/knowledge", headers=_csrf(client), json={"title": body["candidate"]["title"], "summary": body["candidate"]["summary"], "content_markdown": body["candidate"]["content_markdown"], "domain": "stem", "conversation_id": conversation_id, "message_id": message_id})
    assert saved.status_code == 200 and saved.json()["id"] > 0
    assert saved.json()["review_status"] == "DRAFT"
    assert saved.json()["sources"][0]["message_id"] == message_id
    with SessionLocal() as db:
        assert db.get(ChatMessage, message_id).content == original_content


def test_refinement_requires_csrf_ownership_and_exact_selected_substring(monkeypatch):
    owner, (_, conversation_id, message_id) = _client()
    other, _ = _client()
    monkeypatch.setattr("app.api.knowledge_routes.refine_text", lambda *_args: None)
    request = {"conversation_id": conversation_id, "message_id": message_id}
    assert TestClient(app).post("/api/knowledge/refine-from-message", json=request).status_code == 401
    assert owner.post("/api/knowledge/refine-from-message", json=request).status_code == 403
    assert other.post("/api/knowledge/refine-from-message", headers=_csrf(other), json=request).status_code == 404
    mismatch = owner.post("/api/knowledge/refine-from-message", headers=_csrf(owner), json={**request, "selected_text": "invented selection"})
    assert mismatch.status_code == 422 and mismatch.json()["detail"]["code"] == "SELECTION_NOT_IN_SOURCE"


def test_sensitive_domain_requires_explicit_consent_and_credentials_are_blocked(monkeypatch):
    client, (_, conversation_id, message_id) = _client("health")
    called = False
    async def fake_refine(*_args):
        nonlocal called
        called = True
    monkeypatch.setattr("app.api.knowledge_routes.refine_text", fake_refine)
    request = {"conversation_id": conversation_id, "message_id": message_id}
    denied = client.post("/api/knowledge/refine-from-message", headers=_csrf(client), json=request)
    assert denied.status_code == 422 and denied.json()["detail"]["code"] == "SENSITIVE_CONSENT_REQUIRED"
    assert called is False

    with SessionLocal() as db:
        message = db.get(ChatMessage, message_id)
        message.content = "API_KEY=" + "sk-" + "a" * 32
        db.commit()
    blocked = client.post("/api/knowledge/refine-from-message", headers=_csrf(client), json={**request, "sensitive_data_consent": True})
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "CREDENTIAL_CONTENT_BLOCKED"
    assert called is False


def test_refinement_provider_failure_is_audited_and_does_not_create_knowledge(monkeypatch):
    client, (user_id, conversation_id, message_id) = _client()
    monkeypatch.setattr("app.api.knowledge_routes.require_user_api_key", lambda _provider, _user_id: "mock-provider-secret")
    async def fail(*_args):
        raise adapter.RefinementError("INVALID_MODEL_OUTPUT", "bad structured output")
    monkeypatch.setattr("app.api.knowledge_routes.refine_text", fail)
    response = client.post("/api/knowledge/refine-from-message", headers=_csrf(client), json={"conversation_id": conversation_id, "message_id": message_id})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "INVALID_MODEL_OUTPUT"
    with SessionLocal() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.user_id == user_id, AgentRun.workflow == "knowledge_refinement").order_by(AgentRun.id.desc()))
        usage = db.scalar(select(AgentModelUsage).where(AgentModelUsage.run_id == run.id))
        assert run.status == "failed" and run.error_code == "INVALID_MODEL_OUTPUT"
        assert usage.status == "failed"
        assert db.scalar(select(KnowledgeCard).where(KnowledgeCard.user_id == user_id)) is None


def test_adapter_rejects_non_json_or_invalid_structured_model_output(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"content": "not JSON"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, *args, **kwargs): return FakeResponse()
    monkeypatch.setattr(adapter.httpx, "AsyncClient", FakeClient)
    try:
        asyncio.run(adapter.refine_text(SimpleNamespace(base_url="https://api.example", model="mock"), "mock-key", "source"))
    except adapter.RefinementError as exc:
        assert exc.code == "INVALID_MODEL_OUTPUT"
    else:
        raise AssertionError("invalid model output must not be accepted")


def test_adapter_rejects_model_invented_source_urls_and_preserves_consumed_usage(monkeypatch):
    class FakeResponse:
        def raise_for_status(self): return None
        def json(self):
            import json
            return {"choices": [{"message": {"content": json.dumps({"worth_saving": True, "title": "Topic", "summary": "Summary", "content_markdown": "See https://fake.example/claimed-source", "limitations": []})}}], "usage": {"prompt_tokens": 7, "completion_tokens": 9}}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, *args, **kwargs): return FakeResponse()
    monkeypatch.setattr(adapter.httpx, "AsyncClient", FakeClient)
    try:
        asyncio.run(adapter.refine_text(SimpleNamespace(base_url="https://api.example", model="mock"), "mock-key", "Source text without any URL."))
    except adapter.RefinementError as exc:
        assert exc.code == "UNSUPPORTED_SOURCE_URL"
        assert exc.usage == {"prompt_tokens": 7, "completion_tokens": 9}
    else:
        raise AssertionError("model-invented source URLs must not be accepted")
