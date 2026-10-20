import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base, SessionLocal, engine

from app.models import AgentAuditEvent, AgentProposal, AgentRun, AgentToolCall, ChatConversation, ChatMessage, Task, TaskConversationLink, TaskDateHistory, TaskManagerOperation, TaskStatus, TaskStatusHistory
from app.services.task_manager import CreateTasksInput, TaskItemInput, UpdateTaskInput, explicit_task_write_intent, is_task_confirmation, requested_task_count, should_prepare_task_candidates, task_manager_required_tools
from app.services.tool_registry import _task_argument_error_code, available_tools, execute_agent_tool
from test_chat_workspace import _client, _csrf


def setup_module():
    Base.metadata.create_all(bind=engine)


def test_write_intent_classifier_and_tool_visibility():
    assert explicit_task_write_intent("今天读 Python，直接帮我安排进任务")
    assert explicit_task_write_intent("把英语听力任务改成45分钟")
    assert not explicit_task_write_intent("建议一下我应该学什么")
    assert not explicit_task_write_intent("不要创建任务")
    request = "今天把德语agent做好，第二个任务是用notebook整理A3知识点以及A4测验"
    assert not explicit_task_write_intent(request)
    assert should_prepare_task_candidates(request)
    assert task_manager_required_tools(request) == ["get_active_tasks", "prepare_tasks"]
    assert task_manager_required_tools("创建两个任务", "create") == ["get_active_tasks", "create_tasks"]
    assert task_manager_required_tools("先安排好它", "modify") == ["get_active_tasks", "update_task"]
    assert task_manager_required_tools("创建两个任务", "plan") == ["get_active_tasks"]
    assert requested_task_count("创建两个任务：1. 实战并修改德语 Agent；2. 维护个人网站") == 2
    assert requested_task_count("新增 3 项任务") == 3
    assert is_task_confirmation("确认") and is_task_confirmation("创建吧")
    assert "create_tasks" not in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True)}
    assert "create_tasks" in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True, task_write_authorized=True)}
    assert "request_delete_task" in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True, task_write_authorized=True)}
    assert "start_time" not in TaskItemInput.model_fields and "due_time" not in TaskItemInput.model_fields
    assert TaskItemInput.model_validate({"title": "English reading", "module": "english"}).module == "bilingual"
    assert TaskItemInput.model_validate({"title": "dated", "module": "stem", "start_date": "2026-10-13", "due_date": "2026-10-19", "expected_duration_days": 7}).expected_duration_days == 7
    with pytest.raises(Exception):
        TaskItemInput.model_validate({"title": "bad range", "module": "stem", "start_date": "2026-10-13", "due_date": "2026-10-19", "expected_duration_days": 3})


def test_manual_task_api_delegates_persistence_to_task_service(monkeypatch):
    from app.api import routes
    client, user_id = _client()
    original = routes.create_manual_task_service
    calls = []

    def tracked_create_manual_task(db, trusted_user_id, payload, *, idempotency_key=None):
        calls.append((trusted_user_id, idempotency_key))
        return original(db, trusted_user_id, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr(routes, "create_manual_task_service", tracked_create_manual_task)
    response = client.post("/api/tasks", headers={**_csrf(client), "Idempotency-Key": str(uuid4())},
                           json={"title": "Service routed manual task", "module": "stem"})

    assert response.status_code == 200, response.text
    assert calls == [(user_id, response.request.headers.get("Idempotency-Key"))]
    client.close()


def test_manual_task_idempotency_replays_without_changing_duplicate_semantics():
    client, user_id = _client()
    headers = _csrf(client)
    key = str(uuid4())
    payload = {"title": "Repeated manual task title", "module": "stem"}
    try:
        first = client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json=payload)
        retry = client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json=payload)
        assert first.status_code == 200 and retry.status_code == 200
        assert first.json()["id"] == retry.json()["id"]
        changed = client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json={**payload, "module": "projects"})
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "TASK_IDEMPOTENCY_KEY_CONFLICT"

        # No key remains compatible with older clients: repeated submissions are distinct tasks.
        without_key_a = client.post("/api/tasks", headers=headers, json=payload)
        without_key_b = client.post("/api/tasks", headers=headers, json=payload)
        assert without_key_a.status_code == without_key_b.status_code == 200
        assert without_key_a.json()["id"] != without_key_b.json()["id"]
        with SessionLocal() as db:
            tasks = db.scalars(select(Task).where(Task.user_id == user_id, Task.title == payload["title"])).all()
            assert len(tasks) == 3
            history = db.scalars(select(TaskStatusHistory).where(TaskStatusHistory.task_id == first.json()["id"])).all()
            assert len(history) == 1 and history[0].reason == "created"
            operation = db.scalar(select(TaskManagerOperation).where(
                TaskManagerOperation.user_id == user_id,
                TaskManagerOperation.client_operation_id == key,
            ))
            assert operation.status == "applied" and operation.mode == "manual_create"
    finally:
        client.close()


def test_natural_language_manager_persists_tool_created_tasks_idempotently(monkeypatch):
    client, user_id = _client()
    headers = _csrf(client)
    calls = {"count": 0}

    async def fake_run_chat(db, uid, message, history, **kwargs):
        calls["count"] += 1
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        async def execute(name, args):
            return await execute_agent_tool(db, run, uid, name, json.dumps(args), personal=True, web=False, conversation_id=kwargs.get("conversation_id"), task_manager=True, task_write_authorized=kwargs.get("task_write_authorized", False), operation_id=kwargs.get("operation_id"))
        active = await execute("get_active_tasks", {})
        assert isinstance(active, list)
        names = {tool.name for tool in available_tools(personal=True, web=False, task_manager=True, task_write_authorized=kwargs.get("task_write_authorized", False))}
        if kwargs.get("task_write_authorized"):
            assert "create_tasks" in names
            operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == uid, TaskManagerOperation.client_operation_id == kwargs["operation_id"]))
            result = await execute("create_tasks", {"items": [
                {"title": "软件工程学习实战并修改德语 Agent", "module": "ai_engineering", "acceptance_criteria": "完成一项软件工程实战并对德语 Agent 进行修改"},
                {"title": "个人网站开发维护", "module": "projects", "acceptance_criteria": "完成个人网站的一项开发或维护改进"},
            ]})
            assert operation is not None
            return {"content": "模型草稿文本", "run_id": run.id, "provider": "mock", "model": "mock", "tools": [{"name": "get_active_tasks", "status": "completed"}, {"name": "create_tasks", "status": "completed"}]}
        assert "create_tasks" not in names
        return {"content": "可以考虑学习 Python Agent。", "run_id": run.id, "provider": "mock", "model": "mock", "tools": []}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    operation_id = str(uuid4())
    payload = {"message": "给我创建两个任务：1. 软件工程学习实战，修改德语 Agent；2. 个人网站开发维护。", "operation_id": operation_id, "mode": "create"}
    try:
        result = client.post("/api/task-manager/chat", headers=headers, json=payload)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] == "completed" and body["write_authorized"] is True
        assert len(body["tasks"]) == 2
        assert {row["module"] for row in body["tasks"]} == {"ai_engineering", "projects"}
        assert {row["title"] for row in body["tasks"]} == {"软件工程学习实战并修改德语 Agent", "个人网站开发维护"}
        assert all(row["status"] == "TODO" for row in body["tasks"])
        today = client.get("/api/dashboard").json()["today_date"]
        assert all(row["start_date"] == today and row["due_date"] == today for row in body["tasks"])
        assert "#" in body["content"]
        replay = client.post("/api/task-manager/chat", headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
        assert calls["count"] == 1
        assert len(client.get("/api/tasks").json()) == 2
        repeated_click = client.post("/api/task-manager/chat", headers=headers, json={**payload, "operation_id": str(uuid4())})
        assert repeated_click.status_code == 200 and repeated_click.json()["status"] == "no_write"
        assert len([action for action in repeated_click.json()["actions"] if action.get("kind") == "duplicate"]) == 2 and len(client.get("/api/tasks").json()) == 2
        assert len(client.get(f"/api/agent/chat/conversations/{body['conversation_id']}").json()["messages"]) == 2
    finally:
        client.close()


def test_create_mode_single_website_request_defaults_domain_date_and_todo_via_http_and_real_service(monkeypatch):
    client, user_id = _client()
    headers = _csrf(client)
    from app.services import agent_runner
    monkeypatch.setattr(agent_runner, "require_user_api_key", lambda _provider, _user_id: "mock-only-not-a-real-key")
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_read", "type": "function", "function": {"name": "get_active_tasks", "arguments": "{}"}}]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_create", "type": "function", "function": {"name": "create_tasks", "arguments": "{\"items\":[{\"title\":\"开发个人网站\"}]}"}}]}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        {"choices": [{"message": {"role": "assistant", "content": "已创建任务。", "tool_calls": None}}], "usage": {"prompt_tokens": 15, "completion_tokens": 3}},
    ]
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): return None
        def json(self): return self.payload
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, path, **kwargs): return Response(responses.pop(0))
    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", Client)
    operation_id = str(uuid4())
    try:
        response = client.post("/api/task-manager/chat", headers=headers, json={"message": "开发个人网站", "mode": "create", "operation_id": operation_id})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed" and body["write_authorized"] is True
        assert len(body["tasks"]) == 1 and body["tasks"][0]["id"] > 0
        assert body["tasks"][0]["module"] == "projects" and body["tasks"][0]["status"] == "TODO"
        assert body["tasks"][0]["start_date"] == body["tasks"][0]["due_date"] == client.get("/api/dashboard").json()["today_date"]
        assert client.get("/api/tasks").json()[0]["id"] == body["tasks"][0]["id"]
    finally:
        client.close()


def test_task_item_tool_schema_exposes_allowed_domains_and_infers_missing_module():
    schema = CreateTasksInput.model_json_schema()
    module = schema["$defs"]["TaskItemInput"]["properties"]["module"]
    assert set(module["enum"]) == {"general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "ai_engineering", "system"}
    parsed = TaskItemInput.model_validate({"title": "开发个人网站"})
    assert parsed.module == "projects"
    with pytest.raises(Exception) as invalid_domain:
        TaskItemInput.model_validate({"title": "开发", "module": "unknown_domain"})
    assert _task_argument_error_code("create_tasks", invalid_domain.value) == "TASK_DOMAIN_INVALID"


def test_model_task_count_mismatch_is_reported_and_batch_is_not_written(monkeypatch):
    client, _ = _client()
    headers = _csrf(client)
    from app.services import agent_runner
    monkeypatch.setattr(agent_runner, "require_user_api_key", lambda _provider, _user_id: "mock-only-not-a-real-key")
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "read", "type": "function", "function": {"name": "get_active_tasks", "arguments": "{}"}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "write", "type": "function", "function": {"name": "create_tasks", "arguments": "{\"items\":[{\"title\":\"任务甲\"}]}"}}]}}]},
    ]
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): return None
        def json(self): return self.payload
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, path, **kwargs): return Response(responses.pop(0))
    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", Client)
    operation_id = str(uuid4())
    try:
        response = client.post("/api/task-manager/chat", headers=headers, json={"message": "创建两个任务：任务甲；任务乙", "mode": "create", "operation_id": operation_id})
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "TASK_COUNT_MISMATCH"
        assert client.get("/api/tasks").json() == []
        operation = client.get(f"/api/task-manager/operations/{operation_id}").json()
        assert operation["failure_code"] == "TASK_COUNT_MISMATCH"
        from app.models import AgentToolCall
        with SessionLocal() as db:
            row = db.scalar(select(AgentToolCall).where(AgentToolCall.run_id == operation["run_id"], AgentToolCall.tool_name == "create_tasks"))
            assert row is not None and row.error_code == "TASK_COUNT_MISMATCH"
    finally:
        client.close()


def test_operation_failure_is_terminal_and_stale_running_operation_recovers():
    client, user_id = _client()
    headers = _csrf(client)
    conversation = client.post("/api/agent/chat/conversations", headers=headers, json={"domain": "tasks"}).json()
    message = "创建测试任务"
    mode = "create"
    operation_id = str(uuid4())
    fingerprint = hashlib.sha256(f"{mode}\0{conversation['id']}\0{message}\0{json.dumps({'knowledge_card_ids': [], 'candidate_tasks': None}, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
    with SessionLocal() as db:
        failed = TaskManagerOperation(user_id=user_id, client_operation_id=operation_id, conversation_id=conversation["id"],
            status="failed", mode=mode, request_fingerprint=fingerprint, failure_code="MODEL_TIMEOUT", action_details=[])
        db.add(failed)
        stale_id = str(uuid4())
        stale_message = message + " stale"
        stale_fp = hashlib.sha256(f"{mode}\0{conversation['id']}\0{stale_message}\0{json.dumps({'knowledge_card_ids': [], 'candidate_tasks': None}, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
        stale = TaskManagerOperation(user_id=user_id, client_operation_id=stale_id, conversation_id=conversation["id"],
            status="started", mode=mode, request_fingerprint=stale_fp, action_details=[],
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=4),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=4))
        db.add(stale); db.commit()
    try:
        exact_retry = client.post("/api/task-manager/chat", headers=headers, json={"message": message, "mode": mode, "conversation_id": conversation["id"], "operation_id": operation_id})
        assert exact_retry.status_code == 200
        assert exact_retry.json()["status"] == "failed" and exact_retry.json()["failure_code"] == "MODEL_TIMEOUT"
        mismatch = client.post("/api/task-manager/chat", headers=headers, json={"message": "different request", "mode": mode, "conversation_id": conversation["id"], "operation_id": operation_id})
        assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "OPERATION_REQUEST_MISMATCH"
        recovered = client.get(f"/api/task-manager/operations/{stale_id}")
        assert recovered.status_code == 200 and recovered.json()["status"] == "failed"
        assert recovered.json()["failure_code"] == "OPERATION_TIMED_OUT_NO_WRITE"
        busy_id = str(uuid4())
        busy_message = message + " busy"
        busy_fp = hashlib.sha256(f"{mode}\0{conversation['id']}\0{busy_message}\0{json.dumps({'knowledge_card_ids': [], 'candidate_tasks': None}, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
        with SessionLocal() as db:
            db.add(TaskManagerOperation(user_id=user_id, client_operation_id=busy_id, conversation_id=conversation["id"], status="started", mode=mode, request_fingerprint=busy_fp, action_details=[])); db.commit()
        busy = client.post("/api/task-manager/chat", headers=headers, json={"message": busy_message, "mode": mode, "conversation_id": conversation["id"], "operation_id": busy_id})
        assert busy.status_code == 200 and busy.json()["processing"] is True
        assert len(client.get("/api/tasks").json()) == 0
    finally:
        client.close()


def test_planning_mode_is_read_only_even_when_message_uses_create_words(monkeypatch):
    client, _ = _client()
    headers = _csrf(client)
    observed = {}
    async def fake_run_chat(db, uid, message, history, **kwargs):
        observed.update({key: kwargs.get(key) for key in ("task_write_authorized", "task_prepare_authorized", "required_task_tools")})
        assert "create_tasks" not in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True, task_write_authorized=False, task_prepare_authorized=False)}
        assert "prepare_tasks" not in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True, task_write_authorized=False, task_prepare_authorized=False)}
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        return {"content": "只读建议", "run_id": run.id, "provider": "mock", "model": "mock", "tools": [{"name": "get_active_tasks", "status": "completed"}]}
    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        result = client.post("/api/task-manager/chat", headers=headers, json={"message": "创建一个 Python 学习任务", "mode": "plan", "operation_id": str(uuid4())})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "no_write" and result.json()["write_authorized"] is False
        assert observed == {"task_write_authorized": False, "task_prepare_authorized": False, "required_task_tools": ["get_active_tasks"]}
        assert client.get("/api/tasks").json() == []
    finally:
        client.close()


def test_dashboard_returns_date_scoped_tasks_and_keeps_unscheduled_and_overdue_separate():
    client, user_id = _client()
    headers = _csrf(client)
    today = date.fromisoformat(client.get("/api/dashboard").json()["today_date"])
    spanning = client.post("/api/tasks", headers=headers, json={"title": "跨日今日任务", "module": "stem", "start_date": (today-timedelta(days=1)).isoformat(), "due_date": (today+timedelta(days=1)).isoformat(), "expected_duration_days": 3}).json()
    overdue = client.post("/api/tasks", headers=headers, json={"title": "逾期独立任务", "module": "projects", "start_date": (today-timedelta(days=2)).isoformat(), "due_date": (today-timedelta(days=1)).isoformat(), "expected_duration_days": 2}).json()
    completed = client.post("/api/tasks", headers=headers, json={"title": "真实完成任务", "module": "system", "start_date": today.isoformat(), "due_date": today.isoformat()}).json()
    assert client.post(f"/api/tasks/{completed['id']}/evidence", headers=headers, json={"content": "用户自报验收记录", "verification_level": "E1_SELF_REPORTED"}).status_code == 200
    assert client.patch(f"/api/tasks/{completed['id']}", headers=headers, json={"status": "DONE"}).status_code == 200
    with SessionLocal() as db:
        db.add(Task(user_id=user_id, title="无日期历史任务", module="general", status=TaskStatus.TODO, start_date=None, due_date=None, expected_duration_days=None))
        db.commit()
    try:
        dashboard = client.get("/api/dashboard").json()
        assert spanning["id"] in {task["id"] for task in dashboard["today_tasks"]}
        assert overdue["id"] not in {task["id"] for task in dashboard["today_tasks"]}
        assert overdue["id"] in {task["id"] for task in dashboard["overdue_tasks"]}
        assert completed["id"] in {task["id"] for task in dashboard["completed_today_tasks"]}
        assert any(task["title"] == "无日期历史任务" for task in dashboard["unscheduled_tasks"])
    finally:
        client.close()


def test_multi_task_write_rolls_back_entire_batch_on_second_insert_failure():
    from sqlalchemy import event
    from app.models import User
    from app.services.task_manager import create_tasks
    memory_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(memory_engine)
    db = Session(memory_engine)
    user = User(username="atomic-task-user", password_hash="test")
    db.add(user); db.flush()
    run = AgentRun(user_id=user.id, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="started")
    db.add(run); db.flush()
    operation = TaskManagerOperation(user_id=user.id, client_operation_id=str(uuid4()), status="started", action_details=[])
    db.add(operation); db.commit()
    first = TaskItemInput(title="batch first", module="stem")
    second = TaskItemInput(title="batch second", module="stem")
    def fail_second(_mapper, _connection, target):
        if target.title == "batch second": raise RuntimeError("simulated second insert failure")
    event.listen(Task, "before_insert", fail_second)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            create_tasks(db, user.id, run.id, operation.client_operation_id, [first, second])
        db.expire_all()
        assert db.query(Task).filter_by(user_id=user.id).count() == 0
        assert db.get(TaskManagerOperation, operation.id).status == "started"
    finally:
        event.remove(Task, "before_insert", fail_second)
        db.close(); memory_engine.dispose()


def test_create_tool_rejects_model_splitting_explicit_two_task_request():
    from app.models import User
    from app.services.tool_registry import ToolArgumentsError
    memory_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(memory_engine)
    db = Session(memory_engine)
    user = User(username="count-guard-user", password_hash="test")
    db.add(user); db.flush()
    conversation = ChatConversation(id=str(uuid4()), user_id=user.id, title="tasks", domain="tasks")
    db.add(conversation); db.flush()
    message = ChatMessage(user_id=user.id, conversation_id=conversation.id, role="user", content="给我创建两个任务：1. 软件工程实战并修改德语 Agent；2. 维护个人网站", status="pending")
    db.add(message); db.flush()
    run = AgentRun(user_id=user.id, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="started")
    db.add(run); db.flush()
    operation = TaskManagerOperation(user_id=user.id, client_operation_id=str(uuid4()), conversation_id=conversation.id, user_message_id=message.id,
        status="started", mode="create", action_details=[{"kind": "request_constraints", "expected_task_count": 2}])
    db.add(operation); db.commit()
    args = {"items": [{"title": f"Task {index}", "module": "ai_engineering"} for index in range(1, 4)]}
    try:
        with pytest.raises(ToolArgumentsError, match="TASK_COUNT_MISMATCH"):
            asyncio.run(execute_agent_tool(db, run, user.id, "create_tasks", json.dumps(args), personal=True, web=False,
                conversation_id=conversation.id, task_manager=True, task_write_authorized=True, operation_id=operation.client_operation_id))
        assert db.query(Task).filter_by(user_id=user.id).count() == 0
    finally:
        db.close(); memory_engine.dispose()


def test_advice_only_does_not_write_and_update_can_be_undone(monkeypatch):
    client, _ = _client()
    headers = _csrf(client)
    target = client.post("/api/tasks", headers=headers, json={"title": "英语听力练习", "estimated_minutes": 20}).json()

    async def fake_run_chat(db, uid, message, history, **kwargs):
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        if kwargs.get("task_write_authorized"):
            args = UpdateTaskInput(task_id=target["id"], match_title="英语听力练习", estimated_minutes=45) if "45分钟" in message else UpdateTaskInput(task_id=target["id"], match_title="英语听力练习", due_date=(date.fromisoformat(target["due_date"]) + timedelta(days=5)))
            await execute_agent_tool(db, run, uid, "update_task", args.model_dump_json(exclude_unset=True), personal=True, web=False, conversation_id=kwargs.get("conversation_id"), task_manager=True, task_write_authorized=True, operation_id=kwargs.get("operation_id"))
        return {"content": "测试回答", "run_id": run.id, "provider": "mock", "model": "mock", "tools": []}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        no_write = client.post("/api/task-manager/chat", headers=headers, json={"message": "建议一下我应该怎么学习英语", "operation_id": str(uuid4()), "mode": "plan"})
        assert no_write.status_code == 200 and no_write.json()["write_authorized"] is False
        assert no_write.json()["content"].startswith("本轮未获任务写入授权")
        assert client.get(f"/api/tasks/{target['id']}").json()["estimated_minutes"] == 20

        changed = client.post("/api/task-manager/chat", headers=headers, json={"message": "把英语听力练习改成45分钟", "operation_id": str(uuid4()), "mode": "modify"})
        assert changed.status_code == 200 and changed.json()["status"] == "completed"
        assert client.get(f"/api/tasks/{target['id']}").json()["estimated_minutes"] == 45
        undo = client.post(f"/api/task-manager/operations/{changed.json()['operation_id']}/undo", headers=headers, json={})
        assert undo.status_code == 200
        assert client.get(f"/api/tasks/{target['id']}").json()["estimated_minutes"] == 20
        assert client.post(f"/api/task-manager/operations/{changed.json()['operation_id']}/undo", headers=headers, json={}).status_code == 409

        original_due = client.get(f"/api/tasks/{target['id']}").json()["due_date"]
        delayed_due = (date.fromisoformat(original_due) + timedelta(days=5)).isoformat()
        delayed = client.post("/api/task-manager/chat", headers=headers, json={"message": f"把英语听力练习延期到{delayed_due}", "operation_id": str(uuid4()), "mode": "modify"})
        assert delayed.status_code == 200 and client.get(f"/api/tasks/{target['id']}").json()["due_date"] == delayed_due
        assert len(client.get(f"/api/tasks/{target['id']}").json()["date_history"]) == 1
        assert client.post(f"/api/task-manager/operations/{delayed.json()['operation_id']}/undo", headers=headers, json={}).status_code == 200
        restored = client.get(f"/api/tasks/{target['id']}").json()
        assert restored["due_date"] == original_due and len(restored["date_history"]) == 2
    finally:
        client.close()


def test_agent_runner_native_function_call_creates_only_after_real_tool_result(monkeypatch):
    client, user_id = _client()
    headers = _csrf(client)
    from app.services import agent_runner
    monkeypatch.setattr(agent_runner, "require_user_api_key", lambda _provider, _user_id: "mock-only-not-a-real-key")
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_read_tasks", "type": "function", "function": {"name": "get_active_tasks", "arguments": "{}"}}]}}], "usage": {"prompt_tokens": 17, "completion_tokens": 3}},
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_create_tasks", "type": "function", "function": {"name": "create_tasks", "arguments": json.dumps({"items": [{"title": "Mock Agent 30 分钟练习", "module": "ai_engineering", "estimated_minutes": 30, "acceptance_criteria": "本地测试能观察工具参数和结果回传"}]}, ensure_ascii=False)}}]}}], "usage": {"prompt_tokens": 25, "completion_tokens": 6}},
        {"choices": [{"message": {"role": "assistant", "content": "工具结果已返回。", "tool_calls": None}}], "usage": {"prompt_tokens": 31, "completion_tokens": 8}},
    ]
    sent = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): return None
        def json(self): return self.payload

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, path, **kwargs):
            sent.append(kwargs["json"])
            return Response(responses.pop(0))

    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", Client)
    try:
        response = client.post("/api/task-manager/chat", headers=headers, json={"message": "把 Mock Agent 练习直接安排进任务", "operation_id": str(uuid4()), "mode": "create"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed" and len(body["tasks"]) == 1
        assert body["tasks"][0]["title"] == "Mock Agent 30 分钟练习"
        assert len(sent) == 3
        tool_names = {tool["function"]["name"] for tool in sent[0]["tools"]}
        assert "create_tasks" in tool_names
        returned_tool_messages = [message for message in sent[2]["messages"] if message.get("role") == "tool"]
        assert {message["tool_call_id"] for message in returned_tool_messages} == {"call_read_tasks", "call_create_tasks"}
        assert [item["name"] for item in body["tools"]] == ["get_active_tasks", "create_tasks"]
        assert len(client.get("/api/tasks").json()) == 1
    finally:
        client.close()


def test_task_write_tool_enforces_owner_operation_and_schema():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    from app.models import User
    owner = User(username="task-owner", password_hash="x")
    outsider = User(username="task-outsider", password_hash="x")
    db.add_all([owner, outsider]); db.flush()
    run = AgentRun(user_id=owner.id, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="started")
    db.add(run); db.flush()
    operation = TaskManagerOperation(user_id=owner.id, client_operation_id=str(uuid4()), status="started", action_details=[])
    db.add(operation); db.flush()
    args = {"items": [{"title": "isolated task", "module": "stem"}]}
    with pytest.raises(Exception):
        asyncio.run(execute_agent_tool(db, run, outsider.id, "create_tasks", json.dumps(args), personal=True, web=False, task_manager=True, task_write_authorized=True, operation_id=operation.client_operation_id))
    assert db.query(Task).filter_by(title="isolated task").count() == 0
    with pytest.raises(Exception):
        CreateTasksInput.model_validate({"items": [{"title": "bad", "module": "unknown-domain", "start_date": "2026-10-14", "due_date": "2026-10-13"}]})
    db.close()
    engine.dispose()


def test_delete_requires_second_confirmation_and_can_be_undone(monkeypatch):
    client, _ = _client()
    headers = _csrf(client)
    task = client.post("/api/tasks", headers=headers, json={"title": "旧版草稿任务"}).json()

    async def fake_run_chat(db, uid, message, history, **kwargs):
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        await execute_agent_tool(db, run, uid, "request_delete_task", json.dumps({"task_id": task["id"], "match_title": task["title"]}), personal=True, web=False, conversation_id=kwargs.get("conversation_id"), task_manager=True, task_write_authorized=True, operation_id=kwargs.get("operation_id"))
        return {"content": "待确认", "run_id": run.id, "provider": "mock", "model": "mock", "tools": [{"name": "request_delete_task", "status": "completed"}]}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        response = client.post("/api/task-manager/chat", headers=headers, json={"message": "删除任务 旧版草稿任务", "operation_id": str(uuid4())})
        assert response.status_code == 200 and response.json()["status"] == "awaiting_confirmation"
        operation_id = response.json()["operation_id"]
        assert response.json()["confirmation_required"] is True
        assert any(item["id"] == task["id"] for item in client.get("/api/tasks").json())
        assert len(client.get("/api/task-manager/pending-confirmations").json()) == 1
        confirmed = client.post(f"/api/task-manager/operations/{operation_id}/confirm", headers=headers, json={})
        assert confirmed.status_code == 200
        assert all(item["id"] != task["id"] for item in client.get("/api/tasks").json())
        assert client.post(f"/api/task-manager/operations/{operation_id}/confirm", headers=headers, json={}).json()["idempotent_replay"] is True
        assert client.post(f"/api/task-manager/operations/{operation_id}/undo", headers=headers, json={}).status_code == 200
        assert any(item["id"] == task["id"] for item in client.get("/api/tasks").json())
    finally:
        client.close()


def test_cross_turn_task_preview_confirmation_commits_same_two_items(monkeypatch):
    client, user_id = _client()
    headers = _csrf(client)
    seen = {}

    async def fake_run_chat(db, uid, message, history, **kwargs):
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        seen["run_id"] = run.id
        seen["required"] = kwargs.get("required_task_tools")
        async def execute(name, args):
            return await execute_agent_tool(db, run, uid, name, json.dumps(args, ensure_ascii=False), personal=True, web=False,
                conversation_id=kwargs.get("conversation_id"), task_manager=True, task_write_authorized=kwargs.get("task_write_authorized", False), operation_id=kwargs.get("operation_id"))
        await execute("get_active_tasks", {})
        assert "prepare_tasks" in {tool.name for tool in available_tools(personal=True, web=False, task_manager=True)}
        return_value = await execute("prepare_tasks", {"items": [
            {"title": "完成德语 Agent 开发", "module": "ai_engineering", "estimated_minutes": 60, "acceptance_criteria": "本地 Agent 工具链可运行"},
            {"title": "用 Notebook 整理 A3 知识点并完成 A4 测验", "module": "general", "estimated_minutes": 45, "acceptance_criteria": "A3 知识点已整理且 A4 测验有真实作答"},
        ]})
        assert return_value["formal_tasks_created"] is False
        return {"content": "模型候选说明", "run_id": run.id, "provider": "mock", "model": "mock", "tools": [{"name": "get_active_tasks", "status": "completed"}, {"name": "prepare_tasks", "status": "completed"}]}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        draft = client.post("/api/task-manager/chat", headers=headers, json={"message": "今天把德语agent做好，第二个任务是用notebook整理A3知识点以及A4测验", "operation_id": str(uuid4())})
        assert draft.status_code == 200, draft.text
        preview = draft.json()
        assert preview["status"] == "awaiting_user_confirmation"
        assert preview["candidate_tasks"][1]["title"] == "用 Notebook 整理 A3 知识点并完成 A4 测验"
        assert client.get("/api/tasks").json() == []
        assert seen["required"] == ["get_active_tasks", "prepare_tasks"]

        def should_not_call_model(*args, **kwargs):
            raise AssertionError("Confirmation must execute the persisted candidate deterministically")
        monkeypatch.setattr("app.api.routes.run_chat", should_not_call_model)
        confirmed = client.post("/api/task-manager/chat", headers=headers, json={"message": "确认", "conversation_id": preview["conversation_id"], "operation_id": str(uuid4())})
        assert confirmed.status_code == 200, confirmed.text
        result = confirmed.json()
        assert result["status"] == "completed" and result["write_authorized"] is True
        assert len(result["tasks"]) == 2
        assert len({row["id"] for row in result["tasks"]}) == 2
        assert {row["title"] for row in result["tasks"]} == {item["title"] for item in preview["candidate_tasks"]}
        assert {row["module"] for row in result["tasks"]} == {"ai_engineering", "general"}
        replay = client.post("/api/task-manager/chat", headers=headers, json={"message": "确认", "conversation_id": preview["conversation_id"], "operation_id": result["operation_id"]})
        assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
        assert len(client.get("/api/tasks").json()) == 2
        no_pending = client.post("/api/task-manager/chat", headers=headers, json={"message": "创建吧", "conversation_id": preview["conversation_id"], "operation_id": str(uuid4())})
        assert no_pending.status_code == 200 and no_pending.json()["status"] == "no_write"
        assert len(client.get("/api/tasks").json()) == 2
        with SessionLocal() as db:
            op = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.client_operation_id == preview["operation_id"], TaskManagerOperation.user_id == user_id))
            assert op.status == "completed" and op.expires_at is None
            assert any(item.get("kind") == "confirmed_by" for item in op.action_details)
    finally:
        client.close()


def test_knowledge_task_proposal_requires_confirmation_links_source_and_is_idempotent(monkeypatch):
    client, user_id = _client()
    headers = _csrf(client)
    card = client.post("/api/knowledge", headers=headers, json={
        "title": "Python retrieval notes", "summary": "SQLite search ranking basics",
        "content_markdown": "A local SQLite index can rank title, summary, tags and body matches.",
        "domain": "stem", "knowledge_type": "Procedure", "tags": ["sqlite", "retrieval"],
    }).json()

    async def fake_run_chat(db, uid, message, history, **kwargs):
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools,user_authorized_knowledge_cards", status="completed")
        db.add(run); db.flush()
        assert kwargs["knowledge_context"][0]["id"] == card["id"]
        operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == uid, TaskManagerOperation.client_operation_id == kwargs["operation_id"]))
        operation.run_id = run.id
        return_value = await execute_agent_tool(db, run, uid, "prepare_tasks", json.dumps({"items": [
            {"title": "Build a SQLite retrieval exercise", "description": f"Use knowledge card #{card['id']}", "module": "ai_engineering", "acceptance_criteria": "Compare ranked matches"}
        ]}), personal=True, web=False, conversation_id=kwargs["conversation_id"], task_manager=True,
            task_prepare_authorized=True, operation_id=kwargs["operation_id"])
        assert return_value["formal_tasks_created"] is False
        return {"content": "Draft only", "run_id": run.id, "provider": "mock", "model": "mock", "tools": [{"name": "prepare_tasks", "status": "completed"}]}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        draft = client.post("/api/task-manager/chat", headers=headers, json={
            "message": "根据知识制定一个项目任务草案，不要填写日期。", "operation_id": str(uuid4()), "mode": "auto",
            "knowledge_card_ids": [card["id"]], "knowledge_consent_granted": True,
        })
        assert draft.status_code == 200, draft.text
        preview = draft.json()
        assert preview["status"] == "awaiting_user_confirmation"
        assert preview["candidate_tasks"][0]["start_date"] is None and preview["candidate_tasks"][0]["due_date"] is None
        assert client.get("/api/tasks").json() == []

        edited = {**preview["candidate_tasks"][0], "title": "Edited SQLite retrieval exercise", "start_date": None, "due_date": None}
        confirmed_payload = {"message": "确认", "conversation_id": preview["conversation_id"], "operation_id": str(uuid4()), "mode": "auto", "candidate_tasks": [edited]}
        confirmed = client.post("/api/task-manager/chat", headers=headers, json=confirmed_payload)
        assert confirmed.status_code == 200, confirmed.text
        result = confirmed.json()
        assert result["status"] == "completed" and result["write_authorized"] is True
        assert len(result["created"]) == 1
        task_id = result["created"][0]["id"]
        task = client.get(f"/api/tasks/{task_id}").json()
        assert task["id"] == task_id and task["title"] == edited["title"]
        assert task["start_date"] is None and task["due_date"] is None
        assert client.get(f"/api/knowledge?task_id={task_id}").json()[0]["id"] == card["id"]
        replay = client.post("/api/task-manager/chat", headers=headers, json=confirmed_payload)
        assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
        assert len(client.get("/api/tasks").json()) == 1
    finally:
        client.close()


def test_knowledge_task_provider_failure_creates_no_task(monkeypatch):
    from app.services.agent_runner import AgentRunError

    client, _ = _client()
    headers = _csrf(client)
    card = client.post("/api/knowledge", headers=headers, json={
        "title": "SQLite notes", "content_markdown": "Search title and body terms.", "domain": "stem", "knowledge_type": "Concept",
    }).json()

    async def fail_provider(*args, **kwargs):
        raise AgentRunError("MODEL_TIMEOUT", "mock timeout")

    monkeypatch.setattr("app.api.routes.run_chat", fail_provider)
    try:
        operation_id = str(uuid4())
        response = client.post("/api/task-manager/chat", headers=headers, json={
            "message": "根据知识做一个任务草案", "operation_id": operation_id, "mode": "auto",
            "knowledge_card_ids": [card["id"]], "knowledge_consent_granted": True,
        })
        assert response.status_code == 504, response.text
        assert client.get("/api/tasks").json() == []
        with SessionLocal() as db:
            failed = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.client_operation_id == operation_id))
            assert failed.status == "failed" and failed.failure_code == "MODEL_TIMEOUT"
    finally:
        client.close()


def test_task_manager_conversation_permanent_delete_redacts_body_and_preserves_tasks():
    from app.models import User
    from app.auth.security import hash_password
    owner, uid = _client()
    outsider, _ = _client()
    headers = _csrf(owner)
    task = owner.post("/api/tasks", headers=headers, json={"title": "Task survives chat purge"}).json()
    conversation = owner.post("/api/agent/chat/conversations", headers=headers, json={"domain": "tasks"}).json()
    with SessionLocal() as db:
        run = AgentRun(user_id=uid, workflow="task_manager", mode="mock", provider="mock", model="mock", authorized_scope="task_manager_tools", status="completed")
        db.add(run); db.flush()
        user_message = ChatMessage(user_id=uid, conversation_id=conversation["id"], role="user", content="private prompt to remove", status="completed")
        assistant_message = ChatMessage(user_id=uid, conversation_id=conversation["id"], run_id=run.id, role="assistant", content="private response to remove", status="completed")
        db.add_all([user_message, assistant_message]); db.flush()
        db.add(TaskConversationLink(user_id=uid, task_id=task["id"], conversation_id=conversation["id"], message_id=assistant_message.id))
        db.add(AgentToolCall(run_id=run.id, user_id=uid, tool_name="get_active_tasks", allowed=True, status="completed", arguments_summary='{"private":"arg"}', result_summary='{"private":"body"}'))
        db.commit()
        run_id = run.id
    try:
        path = f"/api/agent/chat/conversations/{conversation['id']}"
        assert outsider.delete(path, headers=_csrf(outsider)).status_code == 404
        assert owner.delete(path, headers=headers).status_code == 200
        assert owner.get("/api/agent/chat/conversations?domain=tasks&include_deleted=true").json()["conversations"][0]["id"] == conversation["id"]
        assert owner.post(path + "/permanent-delete", headers=headers, json={"confirm_title": "wrong"}).status_code == 422
        purged = owner.post(path + "/permanent-delete", headers=headers, json={"confirm_title": conversation["title"]})
        assert purged.status_code == 200 and purged.json()["formal_tasks_preserved"] is True
        assert owner.get(path).status_code == 404
        assert owner.get("/api/agent/chat/conversations?domain=tasks&include_deleted=true").json()["conversations"] == []
        assert owner.get(f"/api/tasks/{task['id']}").status_code == 200
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(conversation_id=conversation["id"], user_id=uid).count() == 0
            tool = db.scalar(select(AgentToolCall).where(AgentToolCall.user_id == uid, AgentToolCall.run_id == run_id))
            assert tool and tool.arguments_summary is None and tool.result_summary is None
            assert db.scalar(select(AgentRun).where(AgentRun.id == run_id)) is not None
            assert db.query(TaskConversationLink).filter_by(task_id=task["id"]).count() == 0
            assert db.query(AgentAuditEvent).filter_by(user_id=uid, event_type="conversation_content_purged").count() == 1
    finally:
        owner.close(); outsider.close()


def test_proposal_trash_restore_and_permanent_purge_never_delete_approved_task():
    client, uid = _client()
    headers = _csrf(client)
    pending_id = 0
    with SessionLocal() as db:
        pending = AgentProposal(user_id=uid, proposal_key=f"purge-pending-{uuid4()}", proposal_type="research_task", plan_date=date.today(), status="PENDING", title="Pending candidate", items=[{"title": "Never auto create", "action": "x"}])
        approved = AgentProposal(user_id=uid, proposal_key=f"purge-approved-{uuid4()}", proposal_type="research_task", plan_date=date.today(), status="PENDING", title="Approved historic proposal", items=[{"domain": "stem", "goal_id": None, "existing_task_id": None, "title": "Approved task survives proposal purge", "action": "content to clear", "estimated_minutes": 25, "why_today": "test", "acceptance_criteria": "test task exists", "evidence_required": "run output", "source_task_ids": [], "sources": [], "source_conversation_id": None, "source_message_id": None}])
        db.add_all([pending, approved]); db.commit(); db.refresh(pending); db.refresh(approved)
        pending_id, approved_id = pending.id, approved.id
    try:
        approved_result = client.patch(f"/api/agent/proposals/{approved_id}", headers=headers, json={"action": "approve"})
        assert approved_result.status_code == 200, approved_result.text
        task_id = approved_result.json()["created_task_ids"][0]
        trashed_pending = client.delete(f"/api/agent/proposals/{pending_id}", headers=headers)
        assert trashed_pending.status_code == 200 and trashed_pending.json()["status"] == "REJECTED" and trashed_pending.json()["deleted"]
        active_after_cancel = client.get("/api/agent/proposals").json()
        assert pending_id not in {item["id"] for item in active_after_cancel}
        assert approved_id in {item["id"] for item in active_after_cancel}
        assert client.get("/api/agent/proposals?include_deleted=true&status=REJECTED").json()[0]["id"] == pending_id
        assert task_id in {item["id"] for item in client.get("/api/tasks").json()}
        restored = client.post(f"/api/agent/proposals/{pending_id}/restore", headers=headers, json={})
        assert restored.status_code == 200 and restored.json()["deleted"] is False
        client.delete(f"/api/agent/proposals/{pending_id}", headers=headers)
        assert client.post(f"/api/agent/proposals/{pending_id}/permanent-delete", headers=headers, json={"confirm_title": "Pending candidate"}).status_code == 200
        client.delete(f"/api/agent/proposals/{approved_id}", headers=headers)
        purged = client.post(f"/api/agent/proposals/{approved_id}/permanent-delete", headers=headers, json={"confirm_title": "Approved historic proposal"})
        assert purged.status_code == 200 and purged.json()["purged"] is True
        detail = client.get(f"/api/agent/proposals?include_deleted=true&status=PURGED").json()[0]
        assert detail["items"] == [] and detail["title"] == "[内容已永久清除]"
        assert client.post(f"/api/agent/proposals/{approved_id}/permanent-delete", headers=headers, json={"confirm_title": "x"}).json()["idempotent_replay"] is True
        assert client.get(f"/api/tasks/{task_id}").status_code == 200
    finally:
        client.close()
