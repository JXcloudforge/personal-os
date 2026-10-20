from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from uuid import uuid4

from app.database import Base, engine
from app.main import app
from app.config import settings
from app.auth.security import hash_password
from app.database import SessionLocal
from app.models import User


def setup_module():
    Base.metadata.create_all(bind=engine)


def csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def test_login_does_not_reveal_whether_username_exists():
    existing_username = f"auth-probe-{uuid4().hex}"
    with SessionLocal() as db:
        db.add(User(username=existing_username, password_hash=hash_password("a-secure-test-password")))
        db.commit()

    try:
        with TestClient(app) as client:
            wrong_password = client.post("/api/auth/login", json={"username": existing_username, "password": "wrong-test-password"})
            unknown_user = client.post("/api/auth/login", json={"username": f"missing-{uuid4().hex}", "password": "wrong-test-password"})

        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json() == unknown_user.json()
    finally:
        with SessionLocal() as db:
            user = db.query(User).filter_by(username=existing_username).one_or_none()
            if user is not None:
                db.delete(user)
                db.commit()


def test_health_and_setup_login_task_flow():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        setup = client.post("/api/auth/setup", json={"username": "tester", "password": "a-secure-password"})
        assert setup.status_code == 200
        task = client.post("/api/tasks", headers=csrf(client), json={"title": "Verify Personal OS", "module": "system"})
        assert task.status_code == 200
        task_id = task.json()["id"]
        assert client.get("/api/tasks").status_code == 200
        assert client.patch(f"/api/tasks/{task_id}", headers=csrf(client), json={"status": "DONE"}).status_code == 422
        assert client.post(f"/api/tasks/{task_id}/evidence", headers=csrf(client), json={"content": "pytest output", "verification_level": "E3_TEST"}).status_code == 200
        assert client.patch(f"/api/tasks/{task_id}", headers=csrf(client), json={"status": "DONE"}).status_code == 200


def test_provider_secret_is_encrypted_and_never_returned():
    settings.credential_master_key = Fernet.generate_key().decode()
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "tester", "password": "a-secure-password"})
        assert login.status_code == 200
        headers = csrf(client)
        saved = client.put("/api/settings/ai/providers/deepseek", headers=headers, json={"api_key": "test-secret-key", "model": "deepseek-flash", "enabled": True})
        assert saved.status_code == 200
        assert "test-secret-key" not in saved.text
        from app.database import SessionLocal
        from app.models import AIProvider
        with SessionLocal() as db:
            stored = db.query(AIProvider).filter_by(provider="deepseek").one()
            assert stored.encrypted_api_key != "test-secret-key"
            assert stored.encrypted_api_key is not None
        providers = client.get("/api/settings/ai/providers")
        assert providers.status_code == 200
        deepseek = next(item for item in providers.json() if item["provider"] == "deepseek")
        assert deepseek["configured"] is True
        assert "encrypted_api_key" not in deepseek
        deleted = client.delete("/api/settings/ai/providers/deepseek/credential", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["configured"] is False


def test_private_records_are_scoped_to_authenticated_user():
    with SessionLocal() as db:
        db.add(User(username="other-user", password_hash=hash_password("another-secure-password")))
        db.commit()
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "other-user", "password": "another-secure-password"})
        assert login.status_code == 200
        assert client.get("/api/tasks").json() == []
        assert client.patch("/api/tasks/1", headers=csrf(client), json={"title": "must not cross users"}).status_code == 404


def test_provider_settings_require_login():
    with TestClient(app) as client:
        assert client.get("/api/settings/ai/providers").status_code == 401
        assert client.put("/api/settings/ai/providers/deepseek", json={"api_key": "not-a-real-key"}).status_code == 401


def test_system_info_serves_authoritative_product_history_only_after_login():
    with TestClient(app) as client:
        assert client.get("/api/system/info").status_code == 401
        assert client.post("/api/auth/login", json={"username": "tester", "password": "a-secure-password"}).status_code == 200
        response = client.get("/api/system/info")
        assert response.status_code == 200
        payload = response.json()
        assert payload["app_version"] == "0.4.0"
        assert [item["version"] for item in payload["releases"]] == ["0.3.3", "0.3.2", "0.3.1", "0.3.0", "0.2.0", "0.1.0"]
        assert [item["version"] for item in payload["roadmap"]] == ["0.5.0", "0.6.0", "0.7.0"]
        assert all(item["status"] == "planned" for item in payload["roadmap"])


def test_agent_plan_requires_approval_and_persists_audit():
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "tester", "password": "a-secure-password"})
        assert login.status_code == 200
        headers = csrf(client)
        goal = client.post("/api/agent/goals", headers=headers, json={"domain": "english", "title": "Improve technical reading", "priority": 1})
        assert goal.status_code == 200
        before = len(client.get("/api/tasks").json())
        generated = client.post("/api/agent/plan", headers=headers, json={"available_minutes": 45, "mode": "mock", "consent_granted": True})
        assert generated.status_code == 200
        proposal = generated.json()
        assert proposal["status"] == "PENDING"
        assert len(client.get("/api/tasks").json()) == before
        repeated = client.post("/api/agent/plan", headers=headers, json={"available_minutes": 45, "mode": "mock", "consent_granted": True})
        assert repeated.status_code == 200
        assert repeated.json()["id"] == proposal["id"]
        edited_items = proposal["items"]
        edited_items[0]["title"] = "完成一次技术英语阅读证据记录"
        edited = client.patch(f"/api/agent/proposals/{proposal['id']}", headers=headers, json={"action": "edit", "items": edited_items, "note": "用户编辑"})
        assert edited.status_code == 200
        assert len(client.get("/api/tasks").json()) == before
        approved = client.patch(f"/api/agent/proposals/{proposal['id']}", headers=headers, json={"action": "approve", "note": "用户确认"})
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"
        assert len(client.get("/api/tasks").json()) == before + 1
        evening = client.post("/api/agent/review/evening", headers=headers, json={"mode": "mock", "consent_granted": True})
        assert evening.status_code == 200
        review_proposal = evening.json()
        assert review_proposal["proposal_type"] == "evening_review"
        saved_review = client.patch(f"/api/agent/proposals/{review_proposal['id']}", headers=headers, json={"action": "approve"})
        assert saved_review.status_code == 200
        assert client.get("/api/reviews/daily").json()
        audit = client.get("/api/agent/audit")
        assert audit.status_code == 200
        assert {item["event_type"] for item in audit.json()} >= {"run_started", "run_finished", "proposal_approved"}


def test_agent_plan_without_key_returns_clear_error_without_task_write():
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "tester", "password": "a-secure-password"})
        assert login.status_code == 200
        headers = csrf(client)
        before = len(client.get("/api/tasks").json())
        response = client.post("/api/agent/plan", headers=headers, json={"available_minutes": 30, "mode": "deepseek", "consent_granted": True})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert len(client.get("/api/tasks").json()) == before


def test_model_plan_normalizes_unknown_goal_id_instead_of_returning_500():
    from app.services.agent_runner import _validate_plan

    payload = {
        "rationale": "No confirmed goal was available.",
        "items": [{
            "domain": "system",
            "goal_id": "UNKNOWN",
            "existing_task_id": "UNKNOWN",
            "title": "Create a measurable next action",
            "action": "Write one action and its evidence criterion.",
            "estimated_minutes": 15,
            "why_today": "No target is confirmed.",
            "acceptance_criteria": "One action and one evidence criterion are recorded.",
            "evidence_required": "A saved task record.",
            "source_task_ids": ["UNKNOWN"],
        }],
    }
    items, _ = _validate_plan(payload)
    assert items[0]["goal_id"] is None
    assert items[0]["existing_task_id"] is None
    assert items[0]["source_task_ids"] == []


def test_agent_chat_executes_model_issued_sql_tool_and_returns_tool_audit(monkeypatch):
    from copy import deepcopy
    from app.models import AIProvider, Task
    from app.services.provider_service import encrypt_api_key

    with SessionLocal() as db:
        user = db.query(User).filter_by(username="other-user").one()
        db.add(Task(user_id=user.id, title="Fixture: finish circuit exercise", module="stem", priority=1))
        provider = db.query(AIProvider).filter_by(user_id=user.id, provider="deepseek").one_or_none()
        if not provider:
            provider = AIProvider(user_id=user.id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash")
            db.add(provider)
        provider.encrypted_api_key = encrypt_api_key("fake-key-for-mocked-test")
        provider.enabled = True
        db.commit()

    calls = []
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "reasoning_content": "use the task query", "tool_calls": [{"id": "call_task_1", "type": "function", "function": {"name": "get_active_tasks", "arguments": "{}"}}]}}], "usage": {"prompt_tokens": 20, "completion_tokens": 5}},
        {"choices": [{"message": {"role": "assistant", "content": "The active task is: Fixture: finish circuit exercise.", "tool_calls": None}}], "usage": {"prompt_tokens": 30, "completion_tokens": 12}},
    ]

    class MockResponse:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): return None
        def json(self): return self.payload

    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, path, **kwargs):
            calls.append(deepcopy(kwargs["json"]))
            return MockResponse(responses.pop(0))

    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", MockClient)
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "other-user", "password": "another-secure-password"}).status_code == 200
        response = client.post("/api/agent/chat", headers=csrf(client), json={"message": "What tasks remain?", "consent_granted": True, "include_personal_context": True})
        assert response.status_code == 200, response.text
        body = response.json()
        assert "Fixture: finish circuit exercise" in body["content"]
        assert body["tools"][0]["name"] == "get_active_tasks"
        assert body["tools"][0]["status"] == "completed"
        assert {entry["function"]["name"] for entry in calls[0]["tools"]} >= {"get_active_tasks", "get_current_goals"}
        assert calls[0]["thinking"] == {"type": "disabled"}
        assistant_message = calls[1]["messages"][-2]
        tool_message = calls[1]["messages"][-1]
        assert assistant_message["reasoning_content"] == "use the task query"
        assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "call_task_1"
        assert "Fixture: finish circuit exercise" in tool_message["content"]
        tool_logs = client.get(f"/api/agent/runs/{body['run_id']}/tools").json()
        assert any(entry["tool"] == "get_active_tasks" and entry["status"] == "completed" for entry in tool_logs)


def test_web_fetch_rejects_private_and_non_https_targets():
    import pytest
    from app.services.tool_registry import FetchArguments, ToolArgumentsError, _validate_public_url

    for url in ["http://example.com", "https://127.0.0.1/admin", "https://169.254.169.254/latest/meta-data", "https://localhost/"]:
        with pytest.raises(ToolArgumentsError):
            _validate_public_url(url)


def test_agent_plan_full_route_handles_unknown_goal_id_without_500(monkeypatch):
    from app.models import AIProvider
    from app.services.provider_service import encrypt_api_key

    output = {"rationale": "Goal was not established, so no goal is claimed.", "items": [{"domain": "system", "goal_id": "UNKNOWN", "existing_task_id": "UNKNOWN", "title": "Record one measurable next action", "action": "Write the next action and acceptance evidence.", "estimated_minutes": 15, "why_today": "No confirmed goal was returned.", "acceptance_criteria": "A clear action and checkable evidence are written.", "evidence_required": "Saved text evidence.", "source_task_ids": ["UNKNOWN"]}]}

    class Response:
        def raise_for_status(self): return None
        def json(self): return {"choices": [{"message": {"content": __import__("json").dumps(output)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return Response()

    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", Client)
    with SessionLocal() as db:
        user = db.query(User).filter_by(username="other-user").one()
        provider = db.query(AIProvider).filter_by(user_id=user.id, provider="deepseek").one_or_none()
        if not provider:
            provider = AIProvider(user_id=user.id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash")
            db.add(provider)
        provider.encrypted_api_key = encrypt_api_key("fake-key-for-mocked-test")
        provider.enabled = True
        db.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "other-user", "password": "another-secure-password"}).status_code == 200
        response = client.post("/api/agent/plan", headers=csrf(client), json={"available_minutes": 20, "mode": "deepseek", "consent_granted": True})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PENDING"
        assert response.json()["items"][0]["goal_id"] is None


def test_tavily_credential_is_encrypted_and_status_only():
    from app.models import SearchProvider
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "other-user", "password": "another-secure-password"}).status_code == 200
        response = client.put("/api/settings/search/tavily", headers=csrf(client), json={"api_key": "fake-tavily-key-for-test"})
        assert response.status_code == 200
        assert "fake-tavily-key-for-test" not in response.text
        assert client.get("/api/settings/search").json() == {"provider": "tavily", "configured": True, "enabled": True, "status": "configured"}
        with SessionLocal() as db:
            stored = db.query(SearchProvider).filter_by(provider="tavily").one()
            assert stored.encrypted_api_key and stored.encrypted_api_key != "fake-tavily-key-for-test"
        assert client.delete("/api/settings/search/tavily", headers=csrf(client)).json()["configured"] is False


def test_auth_cookies_are_secure_on_https_and_tailnet_proxy_but_dev_http_stays_usable():
    username = f"cookie-test-{uuid4().hex}"
    password = "cookie-test-secure-password"
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    settings.cookie_secure = False
    with TestClient(app, base_url="https://testserver") as https_client:
        response = https_client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(value.lower() for value in cookies if value.startswith("personal_os_session="))
        csrf_cookie = next(value.lower() for value in cookies if value.startswith("personal_os_csrf="))
        assert "secure" in session_cookie and "httponly" in session_cookie and "samesite=lax" in session_cookie
        assert "secure" in csrf_cookie and "httponly" not in csrf_cookie and "samesite=lax" in csrf_cookie
    with TestClient(app, base_url="http://testserver") as dev_client:
        response = dev_client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        assert all("; secure" not in value.lower() for value in response.headers.get_list("set-cookie"))
    with TestClient(app, base_url="http://testserver") as proxy_client:
        response = proxy_client.post("/api/auth/login", headers={"X-Forwarded-Host": "personal-os.example.ts.net"}, json={"username": username, "password": password})
        assert response.status_code == 200
        assert all("; secure" in value.lower() for value in response.headers.get_list("set-cookie"))
    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        db.delete(user)
        db.commit()
