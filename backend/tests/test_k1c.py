import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AIProvider, AgentModelUsage, AgentRun, AgentToolCall, ChatMessage, Task, TaskStatusHistory, User


def setup_module():
    _assert_test_database()
    Base.metadata.create_all(bind=engine)


def _assert_test_database():
    production = (Path(__file__).resolve().parents[2] / "data" / "personal_os.sqlite3").resolve()
    assert os.environ.get("PERSONAL_OS_TESTING") == "1"
    assert engine.url.database and Path(engine.url.database).resolve() != production
    assert "personal_os_pytest_" in str(engine.url.database)


def _client():
    _assert_test_database()
    username = f"k1c-{Fernet.generate_key().decode()[:10]}"
    password = "K1C-Secure-Test-Password-2026!"
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


def test_message_source_trace_chat_task_approval_and_task_lifecycle():
    owner, user_id = _client()
    other, _ = _client()
    try:
        headers = _csrf(owner)
        conversation = owner.post("/api/agent/chat/conversations", headers=headers, json={"domain": "stem"}).json()
        with SessionLocal() as db:
            run = AgentRun(user_id=user_id, workflow="agent_chat", mode="mock", provider="deepseek", model="deepseek-flash", authorized_scope="public_web_search_fetch", status="completed")
            db.add(run)
            db.flush()
            question = ChatMessage(user_id=user_id, conversation_id=conversation["id"], run_id=run.id, role="user", content="Explain tool calling", status="completed")
            db.add(question)
            db.flush()
            answer = ChatMessage(user_id=user_id, conversation_id=conversation["id"], run_id=run.id, role="assistant", content="Tool calling uses an execution round trip.", status="completed")
            db.add(answer)
            db.flush()
            answer_id, run_id = answer.id, run.id
            db.add(AgentToolCall(run_id=run.id, user_id=user_id, tool_name="web_search", allowed=True, status="completed", result_summary=json.dumps({"sources": [{"url": "https://docs.example.org/tools", "title": "Official Tools", "retrieved_at": "2026-10-12T12:00:00Z"}]})))
            db.add(AgentToolCall(run_id=run.id, user_id=user_id, tool_name="web_fetch", allowed=True, status="completed", result_summary=json.dumps({"url": "https://docs.example.org/tools", "title": "Official Tools", "retrieved_at": "2026-10-12T12:01:00Z"})))
            db.commit()
        trace = owner.get(f"/api/agent/chat/conversations/{conversation['id']}").json()["messages"][1]
        assert trace["id"] == answer_id and trace["run"]["id"] == run_id
        assert trace["question"] == "Explain tool calling"
        assert [tool["name"] for tool in trace["tools"]] == ["web_search", "web_fetch"]
        assert trace["sources"][0]["evidence_level"] == "fetched_body"

        before = len(owner.get("/api/tasks").json())
        response = owner.post(f"/api/agent/chat/messages/{answer_id}/task-proposal", headers=_csrf(owner), json={"title": "Build a tool-call demo", "description": "Implement the request, execution, and result return.", "module": "ai_engineering", "estimated_minutes": 30, "acceptance_criteria": "One local Python tool call returns a verified result."})
        assert response.status_code == 200, response.text
        proposal = response.json()
        assert proposal["status"] == "PENDING" and proposal["run_id"] == run_id
        assert len(owner.get("/api/tasks").json()) == before
        assert other.patch(f"/api/agent/proposals/{proposal['id']}", headers=_csrf(other), json={"action": "approve"}).status_code == 404
        approved = owner.patch(f"/api/agent/proposals/{proposal['id']}", headers=_csrf(owner), json={"action": "approve"})
        assert approved.status_code == 200, approved.text
        task_id = approved.json()["created_task_ids"][0]
        detail = owner.get(f"/api/tasks/{task_id}").json()
        assert detail["estimated_minutes"] == 30
        assert detail["links"][0]["conversation_id"] == conversation["id"]
        assert detail["links"][0]["message_id"] == answer_id
        assert other.get(f"/api/tasks/{task_id}").status_code == 404

        assert owner.post(f"/api/tasks/{task_id}/links", headers=_csrf(owner), json={"conversation_id": conversation["id"], "message_id": answer_id}).json()["created"] is False
        second = owner.post("/api/agent/chat/conversations", headers=_csrf(owner), json={"domain": "global"}).json()
        assert owner.post(f"/api/tasks/{task_id}/links", headers=_csrf(owner), json={"conversation_id": second["id"]}).json()["created"] is True
        assert len(owner.get(f"/api/tasks/{task_id}").json()["links"]) == 2

        assert owner.patch(f"/api/tasks/{task_id}", headers=_csrf(owner), json={"status": "DONE"}).status_code == 422
        assert owner.post(f"/api/tasks/{task_id}/evidence", headers=_csrf(owner), json={"content": "Local test output confirms the flow.", "verification_level": "E3_TEST"}).status_code == 200
        assert owner.patch(f"/api/tasks/{task_id}", headers=_csrf(owner), json={"status": "DONE"}).status_code == 200
        assert owner.patch(f"/api/tasks/{task_id}", headers=_csrf(owner), json={"status": "TODO"}).status_code == 200
        assert owner.patch(f"/api/tasks/{task_id}", headers=_csrf(owner), json={"archived": True, "is_pinned": True}).status_code == 200
        assert owner.get("/api/tasks").json() == []
        assert len(owner.get("/api/tasks?include_archived=true").json()) == 1
        assert owner.patch(f"/api/tasks/{task_id}", headers=_csrf(owner), json={"archived": False}).status_code == 200
        assert owner.delete(f"/api/tasks/{task_id}", headers=_csrf(owner)).status_code == 200
        assert len(owner.get("/api/tasks?include_deleted=true").json()) == 1
        assert owner.post(f"/api/tasks/{task_id}/restore", headers=_csrf(owner), json={}).status_code == 200
        with SessionLocal() as db:
            assert db.query(Task).filter_by(id=task_id, user_id=user_id).count() == 1
            assert db.query(TaskStatusHistory).filter_by(task_id=task_id).count() >= 3
    finally:
        owner.close()
        other.close()


def test_request_level_usage_timezone_budget_and_user_isolation():
    client, user_id = _client()
    other, _ = _client()
    try:
        conversation = client.post("/api/agent/chat/conversations", headers=_csrf(client), json={"domain": "general"}).json()
        with SessionLocal() as db:
            run = AgentRun(user_id=user_id, workflow="agent_chat", mode="mock", provider="deepseek", model="deepseek-flash", authorized_scope="user_message", status="completed")
            db.add(run)
            db.flush()
            message = ChatMessage(user_id=user_id, conversation_id=conversation["id"], run_id=run.id, role="user", content="test usage", status="completed")
            db.add(message)
            db.flush()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            db.add_all([
                AgentModelUsage(user_id=user_id, run_id=run.id, conversation_id=conversation["id"], message_id=message.id, request_index=1, provider="deepseek", model="deepseek-flash", input_tokens=100, output_tokens=20, status="completed", requested_at=now),
                AgentModelUsage(user_id=user_id, run_id=run.id, conversation_id=conversation["id"], message_id=message.id, request_index=2, provider="deepseek", model="deepseek-flash", input_tokens=150, output_tokens=25, status="completed", requested_at=now),
                AgentModelUsage(user_id=user_id, run_id=run.id, conversation_id=conversation["id"], message_id=message.id, request_index=3, provider="deepseek", model="deepseek-flash", input_tokens=None, output_tokens=None, status="usage_unknown", requested_at=now),
            ])
            db.commit()
            conversation_id = conversation["id"]
        usage_response = client.get(f"/api/settings/ai/usage?conversation_id={conversation_id}")
        assert usage_response.status_code == 200, usage_response.text
        payload = usage_response.json()
        assert payload["timezone"] == "Asia/Shanghai"
        assert payload["conversation_usage"]["input_tokens"] == 250
        assert payload["conversation_usage"]["output_tokens"] == 45
        assert payload["conversation_usage"]["unknown_usage_records"] == 1
        assert [row["request_index"] for row in payload["recent_requests"][:3]] == [3, 2, 1]
        assert payload["search"]["credits"] is None
        assert other.get(f"/api/settings/ai/usage?conversation_id={conversation_id}").status_code == 404
        budget = client.put("/api/settings/ai/usage/budget", headers=_csrf(client), json={"max_tool_calls": 3, "max_output_tokens": 900})
        assert budget.status_code == 200 and budget.json() == {"max_tool_calls": 3, "max_output_tokens": 900}
    finally:
        client.close()
        other.close()


def test_provider_test_and_legacy_ai_chat_are_counted_as_real_model_requests(monkeypatch):
    from app.config import settings
    from app.services.provider_service import ConnectionResult, encrypt_api_key

    owner, user_id = _client()
    monkeypatch.setattr(settings, "credential_master_key", Fernet.generate_key().decode())
    with SessionLocal() as db:
        db.add(AIProvider(user_id=user_id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash", enabled=True, encrypted_api_key=encrypt_api_key("mock-only-secret")))
        db.commit()

    async def mock_connection(provider, api_key):
        assert api_key == "mock-only-secret"
        return ConnectionResult(True, "connected", "mocked", 7, 2)

    class MockResponse:
        def raise_for_status(self): return None
        def json(self): return {"choices": [{"message": {"content": "mocked answer"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 4}}

    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return MockResponse()

    monkeypatch.setattr("app.api.routes.test_connection", mock_connection)
    monkeypatch.setattr("app.api.routes.httpx.AsyncClient", MockClient)
    try:
        headers = _csrf(owner)
        tested = owner.post("/api/settings/ai/providers/deepseek/test", headers=headers)
        assert tested.status_code == 200 and tested.json()["usage"] == {"prompt_tokens": 7, "completion_tokens": 2}
        chatted = owner.post("/api/ai/chat", headers=headers, json={"prompt": "Say hello", "purpose": "assistant", "consent_scope": "user_prompt_only", "consent_granted": True})
        assert chatted.status_code == 200 and chatted.json()["content"] == "mocked answer"
        usage = owner.get("/api/settings/ai/usage").json()
        assert [(row["input_tokens"], row["output_tokens"]) for row in usage["recent_requests"][:2]] == [(11, 4), (7, 2)]
        assert {row["status"] for row in usage["recent_requests"][:2]} == {"completed"}
    finally:
        owner.close()


def test_tailnet_https_cookie_flags_remain_secure_without_breaking_local_http(monkeypatch):
    from fastapi import Request, Response
    from app.api.routes import set_auth_cookies
    from app.config import settings

    monkeypatch.setattr(settings, "cookie_secure", False)
    def request_for(host):
        return Request({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/auth/login", "raw_path": b"/api/auth/login", "query_string": b"", "headers": [(b"host", host.encode())], "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000), "root_path": ""})

    tailnet = Response()
    set_auth_cookies(tailnet, "opaque-session", "csrf", request_for("personal-os.example.ts.net"))
    cookies = tailnet.headers.getlist("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith("personal_os_session="))
    csrf_cookie = next(value for value in cookies if value.startswith("personal_os_csrf="))
    assert "secure" in session_cookie.lower() and "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    assert "secure" in csrf_cookie.lower() and "httponly" not in csrf_cookie.lower() and "samesite=lax" in csrf_cookie.lower()

    local = Response()
    set_auth_cookies(local, "opaque-session", "csrf", request_for("localhost"))
    local_session = next(value for value in local.headers.getlist("set-cookie") if value.startswith("personal_os_session="))
    assert "secure" not in local_session.lower() and "httponly" in local_session.lower()
