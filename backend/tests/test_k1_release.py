import asyncio
import json

import pytest

from app.database import Base, SessionLocal, engine
from app.models import AIProvider, AgentToolCall, ChatMessage, Task, TaskConversationLink
from app.services.agent_audit import start_run
from app.services.tool_registry import ToolArgumentsError, ToolDenied, available_tools, execute_agent_tool
from test_chat_workspace import _client, _csrf


def setup_module():
    Base.metadata.create_all(bind=engine)


def test_conversation_task_tool_real_query_authorization_and_isolation():
    owner, owner_id = _client()
    other, other_id = _client()
    try:
        conversation = owner.post('/api/agent/chat/conversations', headers=_csrf(owner), json={'domain': 'stem'}).json()
        another = owner.post('/api/agent/chat/conversations', headers=_csrf(owner), json={'domain': 'stem'}).json()
        with SessionLocal() as db:
            run = start_run(db, owner_id, 'agent_chat', 'mock', 'mock', None, 'conversation_tasks')
            tasks = [Task(user_id=owner_id, title=f'Linked task {i}', module='stem', description=f'Next step {i}', priority=2) for i in range(2)]
            archived = Task(user_id=owner_id, title='Archived task', module='stem', priority=2)
            hidden = Task(user_id=owner_id, title='Health task', module='health', priority=2)
            db.add_all([*tasks, archived, hidden]); db.flush()
            for task in [*tasks, archived, hidden]:
                db.add(TaskConversationLink(user_id=owner_id, task_id=task.id, conversation_id=conversation['id']))
            archived.archived_at = __import__('datetime').datetime.now()
            db.commit()
            assert 'get_conversation_tasks' in [tool.name for tool in available_tools(personal=True, web=False, conversation_id=conversation['id'])]
            assert 'get_conversation_tasks' not in [tool.name for tool in available_tools(personal=False, web=False, conversation_id=conversation['id'])]
            result = asyncio.run(execute_agent_tool(db, run, owner_id, 'get_conversation_tasks', '{}', personal=True, web=False, conversation_id=conversation['id']))
            assert {item['title'] for item in result} == {'Linked task 0', 'Linked task 1', 'Archived task'}
            assert any(item['archived'] for item in result)
            assert all(item['next_action'] for item in result)
            assert asyncio.run(execute_agent_tool(db, run, owner_id, 'get_conversation_tasks', '{}', personal=True, web=False, conversation_id=another['id'])) == []
            with pytest.raises(ToolDenied, match='TOOL_NOT_ALLOWED'):
                asyncio.run(execute_agent_tool(db, run, owner_id, 'get_conversation_tasks', '{}', personal=False, web=False, conversation_id=conversation['id']))
            with pytest.raises(ToolDenied, match='AGENT_RUN_NOT_OWNED'):
                asyncio.run(execute_agent_tool(db, run, other_id, 'get_conversation_tasks', '{}', personal=True, web=False, conversation_id=conversation['id']))
            with pytest.raises(ToolArgumentsError):
                asyncio.run(execute_agent_tool(db, run, owner_id, 'get_conversation_tasks', json.dumps({'conversation_id': another['id']}), personal=True, web=False, conversation_id=conversation['id']))
            audit = db.query(AgentToolCall).filter_by(run_id=run.id, tool_name='get_conversation_tasks', status='completed').all()
            assert len(audit) == 2
            assert db.query(AgentToolCall).filter_by(run_id=run.id, user_id=other_id).count() == 0
        shown = owner.get(f"/api/agent/chat/conversations/{conversation['id']}/tasks").json()
        assert len(shown) == 4
        assert other.get(f"/api/agent/chat/conversations/{conversation['id']}/tasks").status_code == 404
    finally:
        owner.close(); other.close()


def test_message_pages_are_stable_and_export_remains_complete():
    client, user_id = _client()
    try:
        conversation = client.post('/api/agent/chat/conversations', headers=_csrf(client), json={'domain': 'general'}).json()
        with SessionLocal() as db:
            for index in range(23):
                db.add(ChatMessage(user_id=user_id, conversation_id=conversation['id'], role='user', content=f'question-{index}', status='completed'))
                db.add(ChatMessage(user_id=user_id, conversation_id=conversation['id'], role='assistant', content=f'answer-{index}', status='completed'))
            db.commit()
        pages = []
        cursor = None
        while True:
            suffix = f'?before_id={cursor}' if cursor else ''
            response = client.get(f"/api/agent/chat/conversations/{conversation['id']}{suffix}")
            assert response.status_code == 200
            page = response.json(); pages.append(page)
            assert len([item for item in page['messages'] if item['role'] == 'assistant']) <= 10
            if not page['has_more']:
                break
            cursor = page['next_before_id']
        ids = [item['id'] for page in reversed(pages) for item in page['messages']]
        assert len(ids) == 46 and ids == sorted(set(ids))
        assert pages[0]['messages'][0]['content'] == 'question-13'
        exported = client.get(f"/api/agent/chat/conversations/{conversation['id']}/export?format=json").json()
        assert [item['id'] for item in exported['messages']] == ids
    finally:
        client.close()


def test_mock_agent_calls_real_conversation_query_and_audits_it(monkeypatch):
    from app.services import agent_runner
    client, user_id = _client()
    try:
        conversation = client.post('/api/agent/chat/conversations', headers=_csrf(client), json={'domain': 'stem'}).json()
        with SessionLocal() as db:
            task = Task(user_id=user_id, title='Tool-call exercise', module='stem', description='Run the Python example', priority=1)
            db.add(task); db.flush()
            db.add(TaskConversationLink(user_id=user_id, task_id=task.id, conversation_id=conversation['id']))
            db.commit()
            task_id = task.id
            provider = AIProvider(user_id=user_id, provider='deepseek', base_url='https://api.deepseek.com', model='deepseek-flash', enabled=True, total_prompt_tokens=0, total_completion_tokens=0)
            monkeypatch.setattr(agent_runner, 'ensure_provider', lambda *_: provider)
            monkeypatch.setattr(agent_runner, 'require_user_api_key', lambda _provider, _user_id: 'test-only')
            calls = []

            class Response:
                def __init__(self, body): self.body = body
                def raise_for_status(self): pass
                def json(self): return self.body

            class FakeClient:
                def __init__(self, *args, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
                async def post(self, _path, headers, json):
                    calls.append(json)
                    if len(calls) == 1:
                        return Response({'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'get_conversation_tasks', 'arguments': '{}'}}]}}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 3}})
                    assert json['messages'][-1]['role'] == 'tool'
                    assert json['messages'][-1]['tool_call_id'] == 'call-1'
                    tool_result = __import__('json').loads(json['messages'][-1]['content'])
                    assert tool_result[0]['id'] == task_id
                    return Response({'choices': [{'message': {'role': 'assistant', 'content': f"关联任务 #{tool_result[0]['id']}：{tool_result[0]['title']}，下一步：{tool_result[0]['next_action']}"}}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 4}})

            monkeypatch.setattr(agent_runner.httpx, 'AsyncClient', FakeClient)
            result = asyncio.run(agent_runner.run_chat(db, user_id, '当前会话关联了什么任务？', [], include_personal=True, enable_web=False, conversation_id=conversation['id']))
            assert len(calls) == 2
            assert 'get_conversation_tasks' in [item['function']['name'] for item in calls[0]['tools']]
            assert f'#{task_id}' in result['content']
            assert db.query(AgentToolCall).filter_by(run_id=result['run_id'], tool_name='get_conversation_tasks', status='completed').count() == 1
    finally:
        client.close()
