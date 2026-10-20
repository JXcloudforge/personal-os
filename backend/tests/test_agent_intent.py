import asyncio
import json
import uuid

import pytest

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AgentProposal, AgentRun, AgentToolCall, ChatMessage, User
from app.services.agent_runner import AgentIntent, AgentRunError, _claims_proposal_created, classify_agent_intent, run_chat
from app.services.tool_registry import available_tools
from fastapi.testclient import TestClient


def setup_module():
    Base.metadata.create_all(bind=engine)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("告诉我下一步做什么，不要创建任务", AgentIntent.KNOWLEDGE_QA),
        ("总结我的开发计划", AgentIntent.KNOWLEDGE_QA),
        ("根据知识提出建议，不要生成任务草案", AgentIntent.KNOWLEDGE_QA),
        ("帮我制定两个任务草案，先不要创建", AgentIntent.TASK_DRAFT),
        ("请创建任务", AgentIntent.TASK_CREATE),
    ],
)
def test_chat_intent_requires_explicit_task_action(message, expected):
    assert classify_agent_intent(message) == expected


def test_task_proposal_tool_is_hidden_unless_explicitly_authorized():
    normal_names = {tool.name for tool in available_tools(personal=True, web=False)}
    draft_names = {tool.name for tool in available_tools(personal=True, web=False, task_proposal_authorized=True)}
    assert "propose_task" not in normal_names
    assert "propose_task" in draft_names


def test_negative_proposal_claim_is_not_treated_as_created():
    assert not _claims_proposal_created("没有可审批的 PENDING 提案，本次没有创建任务草案。")
    assert _claims_proposal_created("已成功创建任务草案，状态为 PENDING。")


def test_knowledge_chat_ignores_model_task_call_and_keeps_grounded_answer(monkeypatch):
    captured = []
    response_number = 0

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, **kwargs):
            nonlocal response_number
            response_number += 1
            captured.append(kwargs["json"])
            if response_number == 1:
                body = {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                    "id": "call-unauthorized-proposal", "type": "function",
                    "function": {"name": "propose_task", "arguments": "{}"},
                }]}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
            else:
                body = {"choices": [{"message": {"role": "assistant", "content": "下一步先核对开发计划中的 Router 验收项，再补齐独立测试证据。依据知识卡片 #1。"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 12}}
            return Response(body)

    monkeypatch.setattr("app.services.agent_runner.require_user_api_key", lambda _provider, _user_id: "fake-test-key")
    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", HttpClient)
    username = f"intent-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password("Test-only-Password-2026!"))
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
        try:
            result = asyncio.run(run_chat(
                db,
                user_id,
                "根据我保存的德语 Agent 开发计划，告诉我下一步要做什么。不要创建任务。",
                [],
                include_personal=True,
                enable_web=False,
                knowledge_enabled=True,
                knowledge_context=[{"id": 1, "title": "计划卡", "excerpt": "先验收 Router，再记录独立测试证据。"}],
            ))
            run_id = result["run_id"]
            assert result["content"].startswith("下一步先核对开发计划")
            assert result.get("proposal_status") is None
            assert "propose_task" not in {item["function"]["name"] for item in captured[0]["tools"]}
            denied = db.query(AgentToolCall).filter_by(run_id=run_id, tool_name="propose_task").one()
            assert denied.status == "denied"
            assert denied.error_code == "TASK_PROPOSAL_NOT_AUTHORIZED"
            assert db.query(AgentProposal).filter_by(run_id=run_id).count() == 0
            retrieval = db.query(AgentToolCall).filter_by(run_id=run_id, tool_name="knowledge_retrieval").one()
            assert json.loads(retrieval.result_summary) == {"card_ids": [1]}
        finally:
            db.delete(user)
            db.commit()


def test_knowledge_question_http_flow_rejects_forged_proposal_and_persists_answer(monkeypatch):
    captured = []
    response_number = 0
    source = {"id": 7001, "title": "Agent 开发计划", "summary": "开发步骤和 Router 验收", "domain": "projects",
              "excerpt": "先验收 Router，再记录独立测试证据。", "sources": [{"id": 7002, "evidence_kind": "USER_NOTE", "title": "本地开发笔记", "fetch_status": "no_external_source"}]}

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, **kwargs):
            nonlocal response_number
            response_number += 1
            captured.append(kwargs["json"])
            if response_number == 1:
                body = {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                    "id": "http-forged-proposal", "type": "function",
                    "function": {"name": "propose_task", "arguments": "{}"},
                }]}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
            else:
                body = {"choices": [{"message": {"role": "assistant", "content": "下一步先核对开发计划中的 Router 验收项，再补齐独立测试证据；引用知识卡片 #7001。"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 12}}
            return Response(body)

    monkeypatch.setattr("app.api.routes.knowledge_service.retrieve_cards", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr("app.services.agent_runner.require_user_api_key", lambda _provider, _user_id: "fake-test-key")
    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", HttpClient)
    username = f"intent-http-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password("Test-only-Password-2026!"))
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
    client = TestClient(app)
    try:
        assert client.post("/api/auth/login", json={"username": username, "password": "Test-only-Password-2026!"}).status_code == 200
        response = client.post("/api/agent/chat", headers={"X-CSRF-Token": client.cookies.get("personal_os_csrf")}, json={
            "message": "根据我保存的德语 Agent 开发计划，告诉我下一步要做什么，列出引用的知识卡片名称和实际来源。不要创建任务。",
            "consent_granted": True, "include_personal_context": True, "use_knowledge_base": True,
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["content"].startswith("下一步先核对开发计划")
        assert payload["knowledge_sources"][0]["id"] == 7001
        assert payload["knowledge_sources"][0]["title"] == "Agent 开发计划"
        assert payload["knowledge_sources"][0]["sources"] == source["sources"]
        assert "propose_task" not in {item["function"]["name"] for item in captured[0]["tools"]}
        model_knowledge = next(item["content"] for item in captured[0]["messages"] if item["role"] == "user" and "用户授权的个人知识摘录" in item["content"])
        assert "本地开发笔记" in model_knowledge and "USER_NOTE" in model_knowledge
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(user_id=user_id, run_id=payload["run_id"], role="assistant", status="completed").count() == 1
            denied = db.query(AgentToolCall).filter_by(run_id=payload["run_id"], tool_name="propose_task").one()
            assert denied.status == "denied" and denied.error_code == "TASK_PROPOSAL_NOT_AUTHORIZED"
            assert db.query(AgentProposal).filter_by(user_id=user_id, run_id=payload["run_id"]).count() == 0
    finally:
        client.close()
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is not None:
                db.delete(user)
                db.commit()


def test_completed_knowledge_retrieval_without_final_answer_fails_run(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": ""}}], "usage": {"prompt_tokens": 3, "completion_tokens": 0}}

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.agent_runner.require_user_api_key", lambda _provider, _user_id: "fake-test-key")
    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", HttpClient)
    with SessionLocal() as db:
        user = User(username=f"empty-answer-{uuid.uuid4().hex[:12]}", password_hash=hash_password("Test-only-Password-2026!"))
        db.add(user)
        db.commit()
        db.refresh(user)
        try:
            with pytest.raises(AgentRunError) as error:
                asyncio.run(run_chat(db, user.id, "根据知识卡回答问题", [], include_personal=False, enable_web=False,
                    knowledge_enabled=True, knowledge_context=[{"id": 101, "title": "已检索卡片", "excerpt": "有限摘录。"}]))
            assert error.value.code == "EMPTY_MODEL_OUTPUT"
            retrieval = db.query(AgentToolCall).filter_by(run_id=error.value.run_id, tool_name="knowledge_retrieval").one()
            run = db.query(AgentRun).filter_by(id=error.value.run_id).one()
            assert retrieval.status == "completed"
            assert run.status == "failed"
        finally:
            db.delete(user)
            db.commit()


def test_empty_knowledge_search_is_reported_without_fabricated_sources(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "当前知识库没有找到足够资料，无法给出有来源支持的下一步建议。"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 8}}

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    monkeypatch.setattr("app.services.agent_runner.require_user_api_key", lambda _provider, _user_id: "fake-test-key")
    monkeypatch.setattr("app.services.agent_runner.httpx.AsyncClient", HttpClient)
    with SessionLocal() as db:
        user = User(username=f"empty-knowledge-{uuid.uuid4().hex[:12]}", password_hash=hash_password("Test-only-Password-2026!"))
        db.add(user)
        db.commit()
        db.refresh(user)
        try:
            result = asyncio.run(run_chat(db, user.id, "根据我的知识卡给出下一步", [], include_personal=False, enable_web=False,
                knowledge_enabled=True, knowledge_context=[]))
            assert "没有找到足够资料" in result["content"]
            assert "PENDING" not in result["content"]
            assert "no relevant cards" in captured["messages"][0]["content"]
            audit = db.query(AgentToolCall).filter_by(run_id=result["run_id"], tool_name="knowledge_retrieval").one()
            assert audit.status == "empty"
            assert json.loads(audit.result_summary) == {"card_ids": []}
        finally:
            db.delete(user)
            db.commit()
