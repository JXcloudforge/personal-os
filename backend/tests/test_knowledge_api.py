from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AgentRun, AgentToolCall, ChatConversation, ChatMessage, KnowledgeAuditEvent, KnowledgeCard, Task, User
from app.services.tool_registry import TOOLS


def setup_module():
    Base.metadata.create_all(bind=engine)


def _client():
    username = f"knowledge_{uuid4().hex[:10]}"
    password = "knowledge-test-password"
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


def _chat_source(user_id: int, *, with_tools: bool = True):
    conversation_id = str(uuid4())
    with SessionLocal() as db:
        run = AgentRun(user_id=user_id, workflow="chat", mode="standard", provider="deepseek", model="deepseek-flash", authorized_scope="web_search", status="completed")
        db.add(run)
        db.flush()
        conversation = ChatConversation(id=conversation_id, user_id=user_id, title="Verified Tool Calling research", domain="stem")
        db.add(conversation)
        db.flush()
        message = ChatMessage(user_id=user_id, conversation_id=conversation_id, run_id=run.id, role="assistant", content="Models request tools; the backend executes them.", status="completed")
        db.add(message)
        db.flush()
        if with_tools:
            db.add(AgentToolCall(run_id=run.id, user_id=user_id, tool_name="web_search", allowed=True, status="completed", result_summary='{"sources":[{"url":"https://api-docs.deepseek.com/guides/tool_calls","title":"DeepSeek Tool Calls","retrieved_at":"2026-10-13T08:00:00Z"}]}'))
            db.add(AgentToolCall(run_id=run.id, user_id=user_id, tool_name="web_fetch", allowed=True, status="completed", result_summary='{"url":"https://api-docs.deepseek.com/guides/tool_calls","title":"DeepSeek Tool Calls","retrieved_at":"2026-10-13T08:01:00Z"}'))
        db.commit()
        return conversation_id, message.id, run.id


def test_knowledge_http_create_edit_filter_export_and_chat_provenance():
    client, user_id = _client()
    conversation_id, message_id, run_id = _chat_source(user_id)
    payload = {"title": "Function Calling execution boundary", "summary": "The model requests; backend executes.", "content_markdown": "# Verified\n\nThe server executes tools and returns a tool result.", "domain": "stem", "knowledge_type": "Concept", "tags": ["Agent", "tool calling"], "conversation_id": conversation_id, "message_id": message_id}
    assert client.post("/api/knowledge", json=payload).status_code == 403
    created_response = client.post("/api/knowledge", headers=_csrf(client), json=payload)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    card_id = created["id"]
    assert created["sources"][0]["evidence_kind"] == "MODEL_GENERATED"
    assert created["sources"][0]["agent_run_id"] == run_id
    assert next(item for item in created["sources"] if item["url"])["fetch_status"] == "fetched_body"
    assert client.get("/api/knowledge?q=backend&domain=stem&tag=agent").json()[0]["id"] == card_id

    # One original message can legitimately produce multiple independently edited knowledge cards.
    second = client.post("/api/knowledge", headers=_csrf(client), json={**payload, "title": "Backend tool result return", "knowledge_type": "Procedure"})
    assert second.status_code == 200
    assert second.json()["id"] != card_id

    updated = client.patch(f"/api/knowledge/{card_id}", headers=_csrf(client), json={"summary": "Edited by the user", "tags": ["Reviewed"]})
    assert updated.status_code == 200
    assert updated.json()["summary"] == "Edited by the user"
    assert any(item["event"] == "created" for item in updated.json()["history"])
    assert any(item["event"] == "updated" for item in updated.json()["history"])
    assert updated.json()["tags"] == ["Reviewed"]
    assert "The server executes tools" in client.get(f"/api/knowledge/{card_id}/export?format=markdown").text
    assert client.get(f"/api/knowledge/{card_id}/export?format=json").json()["sources"][1]["agent_run_id"] == run_id


def test_knowledge_task_association_ownership_lifecycle_and_source_independence():
    owner, owner_id = _client()
    outsider, _ = _client()
    headers = _csrf(owner)
    conversation_id, message_id, _ = _chat_source(owner_id)
    task = owner.post("/api/tasks", headers=headers, json={"title": "Build tool calling exercise", "module": "ai_engineering", "acceptance_criteria": "Run the local Python tool loop"}).json()
    card = owner.post("/api/knowledge", headers=headers, json={"title": "Agent practice", "content_markdown": "User note", "domain": "stem", "tags": ["practice"], "knowledge_type": "Practice", "conversation_id": conversation_id, "message_id": message_id}).json()
    card_id = card["id"]
    link = owner.post(f"/api/knowledge/{card_id}/tasks/{task['id']}", headers=headers)
    assert link.status_code == 200 and link.json()["created"] is True
    assert owner.post(f"/api/knowledge/{card_id}/tasks/{task['id']}", headers=headers).json()["created"] is False
    assert owner.get(f"/api/knowledge?task_id={task['id']}").json()[0]["id"] == card_id

    assert outsider.get(f"/api/knowledge/{card_id}").status_code == 404
    assert outsider.delete(f"/api/knowledge/{card_id}", headers=_csrf(outsider)).status_code == 404

    assert owner.delete(f"/api/knowledge/{card_id}", headers=headers).json()["recoverable"] is True
    assert owner.get("/api/knowledge/recently-deleted").json()[0]["id"] == card_id
    assert owner.post(f"/api/knowledge/{card_id}/restore", headers=headers).status_code == 200
    assert owner.delete(f"/api/knowledge/{card_id}", headers=headers).status_code == 200
    preview = owner.get(f"/api/knowledge/{card_id}/permanent-delete-preview").json()
    assert preview["can_purge"] is True and preview["task_links_to_detach"] == 1
    assert owner.post(f"/api/knowledge/{card_id}/permanent-delete", headers=headers, json={"confirm_title": "wrong"}).status_code == 422
    assert owner.post(f"/api/knowledge/{card_id}/permanent-delete", headers=headers, json={"confirm_title": "Agent practice"}).status_code == 200
    assert owner.get(f"/api/knowledge/{card_id}").status_code == 404
    assert owner.get(f"/api/tasks/{task['id']}").status_code == 200
    with SessionLocal() as db:
        assert db.get(ChatConversation, conversation_id) is not None
        assert db.get(ChatMessage, message_id) is not None
        assert db.scalar(__import__("sqlalchemy").select(Task).where(Task.id == task["id"], Task.user_id == owner_id)) is not None


def test_search_summary_with_failed_fetch_remains_unread_evidence():
    client, user_id = _client()
    conversation_id, message_id, run_id = _chat_source(user_id, with_tools=False)
    url = "https://developers.openai.com/api/docs/guides/function-calling"
    with SessionLocal() as db:
        db.add(AgentToolCall(run_id=run_id, user_id=user_id, tool_name="web_search", allowed=True, status="completed", result_summary=json.dumps({"sources": [{"url": url, "title": "Official Function Calling Guide"}]})))
        db.add(AgentToolCall(run_id=run_id, user_id=user_id, tool_name="web_fetch", allowed=False, status="denied", error_code="FETCH_BLOCKED", arguments_summary=json.dumps({"url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest()}), result_summary="{}"))
        db.commit()
    created = client.post("/api/knowledge", headers=_csrf(client), json={"title": "Function calling", "content_markdown": "Summary only", "domain": "global", "conversation_id": conversation_id, "message_id": message_id}).json()
    source = next(item for item in created["sources"] if item["url"] == url)
    assert source["fetch_status"] == "search_summary_only_fetch_failed"
    assert source["evidence_kind"] == "SEARCH_SNIPPET"


def test_knowledge_requires_owned_assistant_message_and_does_not_join_general_agent_context():
    owner, owner_id = _client()
    outsider, _ = _client()
    conversation_id, message_id, _ = _chat_source(owner_id)
    response = outsider.post("/api/knowledge", headers=_csrf(outsider), json={"title": "Forged reference", "content_markdown": "x", "conversation_id": conversation_id, "message_id": message_id})
    assert response.status_code == 404
    empty = owner.post("/api/knowledge", headers=_csrf(owner), json={"title": "No outside source", "content_markdown": "Manual note", "domain": "health"}).json()
    assert empty["origin_note"] == "无外部来源"
    assert empty["sources"][0]["evidence_kind"] == "USER_NOTE"
    assert "get_knowledge" not in TOOLS
    assert "knowledge_cards" in Base.metadata.tables


def test_retrieval_returns_only_active_owned_cards_and_real_sources():
    owner, owner_id = _client()
    outsider, _ = _client()
    headers = _csrf(owner)
    card = owner.post("/api/knowledge", headers=headers, json={
        "title": "Quantum circuit study guide",
        "summary": "A reviewed guide to quantum circuits",
        "content_markdown": "A quantum circuit applies unitary gates to qubits.",
        "domain": "stem", "tags": ["quantum", "circuits"], "knowledge_type": "Concept",
    }).json()
    other_card = outsider.post("/api/knowledge", headers=_csrf(outsider), json={
        "title": "Quantum circuit private note", "content_markdown": "secret outsider knowledge",
        "domain": "stem", "knowledge_type": "Concept",
    }).json()
    assert card["id"] != other_card["id"]
    assert owner.post("/api/knowledge/retrieve", json={"query": "quantum"}).status_code == 403
    assert outsider.post("/api/knowledge/retrieve", headers=_csrf(outsider), json={"query": "quantum"}).json()["items"][0]["id"] == other_card["id"]
    result = owner.post("/api/knowledge/retrieve", headers=headers, json={"query": "quantum circuits", "domains": ["stem"], "top_k": 1})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "completed"
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == card["id"]
    assert body["items"][0]["title"] == card["title"]
    assert body["items"][0]["excerpt"] and len(body["items"][0]["excerpt"]) <= 640
    assert body["items"][0]["sources"][0]["evidence_kind"] == "USER_NOTE"
    assert owner.post("/api/knowledge/retrieve", headers=headers, json={"query": "not-in-any-card"}).json() == {"status": "empty", "query": "not-in-any-card", "items": []}
    assert owner.delete(f"/api/knowledge/{card['id']}", headers=headers).status_code == 200
    assert owner.post("/api/knowledge/retrieve", headers=headers, json={"query": "quantum"}).json()["items"] == []
    with SessionLocal() as db:
        assert db.get(KnowledgeCard, card["id"]).user_id == owner_id


def test_retrieval_finds_chinese_prompt_with_mixed_language_card_title():
    client, _ = _client()
    headers = _csrf(client)
    card = client.post("/api/knowledge", headers=headers, json={
        "title": "德语agent规划输入与输出",
        "summary": "德语 Agent 的结构化规划流程",
        "content_markdown": "德语 Agent 先校验输入字段，再按稳定结构返回规划结果。",
        "domain": "bilingual", "tags": ["deutsch", "agent"],
    }).json()
    result = client.post("/api/knowledge/retrieve", headers=headers, json={
        "query": "根据知识卡片「德语agent」，生成两条可编辑任务草案，保留知识来源。",
        "domains": ["bilingual"], "top_k": 5,
    })
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "completed"
    assert body["items"][0]["id"] == card["id"]
    assert body["items"][0]["title"] == card["title"]
