import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Task, User, UserGoal, AgentProposal, AgentToolCall, KnowledgeCard
from app.services.agent_audit import start_run
from app.services.agent_audit import tool_call
from app.services.context_builder import active_tasks, current_goals
from app.services.proposal_service import apply_proposal_action
from app.services.tool_registry import execute_agent_tool, execute_read_tool, ToolDenied, ToolArgumentsError, _web_fetch, FetchArguments


def test_each_sqlite_connection_enables_foreign_keys_and_rejects_orphans(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    with pytest.raises(IntegrityError):
        db.add(Task(user_id=987654321, title="invalid FK fixture", module="stem"))
        db.flush()
    db.rollback()
    second_engine = create_engine("sqlite:///:memory:")
    try:
        with second_engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        second_engine.dispose()


def test_test_process_refuses_sql_against_protected_database(tmp_path, monkeypatch):
    from app import database
    protected = tmp_path / "protected.sqlite3"
    monkeypatch.setenv("PERSONAL_OS_TESTING", "1")
    monkeypatch.setattr(database, "PRODUCTION_DATABASE_URL", f"sqlite:///{protected.as_posix()}")
    attempted = create_engine(f"sqlite:///{protected.as_posix()}")
    try:
        with pytest.raises(RuntimeError, match="forbidden from accessing the production database"):
            with attempted.begin() as connection:
                connection.execute(text("CREATE TABLE must_not_exist (id INTEGER)"))
    finally:
        attempted.dispose()
    # SQLite may create an empty file while connecting; DDL remains blocked.
    if protected.exists():
        import sqlite3
        with sqlite3.connect(protected) as check:
            assert check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_application_import_refuses_production_database_under_pytest():
    import os
    import subprocess
    import sys
    from pathlib import Path
    from sqlalchemy.engine import URL
    backend = Path(__file__).resolve().parents[1]
    production = Path(__file__).resolve().parents[2] / "data" / "personal_os.sqlite3"
    production_url = URL.create("sqlite", database=str(production)).render_as_string(hide_password=False)
    env = os.environ.copy()
    env.update({
        "PERSONAL_OS_TESTING": "1",
        "PERSONAL_OS_DATABASE_URL": production_url,
        "PERSONAL_OS_PRODUCTION_DATABASE_URL": production_url,
        "PERSONAL_OS_RUNTIME_ENVIRONMENT": "test",
    })
    result = subprocess.run([sys.executable, "-c", "import app.database"], cwd=backend, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "must use an isolated database" in result.stderr


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_context_excludes_other_users_and_sensitive_domains(db):
    a, b = User(username='a', password_hash='unused'), User(username='b', password_hash='unused')
    db.add_all([a, b]); db.flush()
    db.add_all([Task(user_id=a.id, title='allowed', module='stem'), Task(user_id=a.id, title='private health', module='health'), Task(user_id=b.id, title='other user', module='stem'), UserGoal(user_id=a.id, domain='finance', title='private finance'), UserGoal(user_id=a.id, domain='english', title='reading')]); db.flush()
    assert [x['title'] for x in active_tasks(db, a.id)] == ['allowed']
    assert [x['title'] for x in current_goals(db, a.id)] == ['reading']


def test_tool_registry_rejects_mismatched_run_and_user_before_read_or_audit(db):
    user_a, user_b = User(username='tool-owner-a', password_hash='unused'), User(username='tool-owner-b', password_hash='unused')
    db.add_all([user_a, user_b]); db.flush()
    db.add(Task(user_id=user_b.id, title='private-to-b', module='stem')); db.flush()
    run_a = start_run(db, user_a.id, 'agent_chat', 'mock', 'mock', None, 'active_tasks')
    db.commit()

    with pytest.raises(ToolDenied, match='AGENT_RUN_NOT_OWNED'):
        asyncio.run(execute_agent_tool(db, run_a, user_b.id, 'get_active_tasks', '{}', personal=True, web=False))
    with pytest.raises(ToolDenied, match='AGENT_RUN_NOT_OWNED'):
        execute_read_tool(db, run_a, user_b.id, 'get_active_tasks')
    with pytest.raises(ToolDenied, match='AGENT_RUN_NOT_OWNED'):
        execute_read_tool(db, SimpleNamespace(id=run_a.id, user_id=user_b.id), user_b.id, 'get_active_tasks')

    assert db.query(AgentToolCall).filter_by(run_id=run_a.id).count() == 0


def test_native_proposal_requires_query_and_approval_then_is_idempotent(db):
    user = User(username='owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'active_tasks')
    item = dict(domain='projects', title='Exercise', action='Implement one function tool', estimated_minutes=30, why_today='Research practice', acceptance_criteria='Tool result is returned', evidence_required='Test output')
    def execute(name, args):
        return asyncio.run(execute_agent_tool(db, run, user.id, name, json.dumps(args), personal=True, web=False, task_proposal_authorized=(name == "propose_task")))
    with pytest.raises(ToolDenied, match='TASK_DUPLICATE_CHECK_REQUIRED'):
        execute('propose_task', item)
    execute('get_active_tasks', {})
    result = execute('propose_task', item)
    assert result['status'] == 'PENDING' and db.query(Task).count() == 0
    proposal = db.get(AgentProposal, result['proposal_id'])
    edited = [{**item, 'title': 'Edited exercise'}]
    _, created, _ = apply_proposal_action(db, user, proposal, action='approve', items=edited, note='test-only approval')
    assert len(created) == 1 and db.get(Task, created[0]).title == 'Edited exercise'
    assert db.get(Task, created[0]).status.value == 'TODO'
    apply_proposal_action(db, user, proposal, action='approve', items=edited, note=None)
    assert db.query(Task).count() == 1
    other = execute('propose_task', {**item, 'title': 'Rejected practice'})
    apply_proposal_action(db, user, db.get(AgentProposal, other['proposal_id']), action='reject', items=None, note=None)
    assert db.query(Task).count() == 1
    with pytest.raises(ToolArgumentsError):
        execute('propose_task', {**item, 'sources': ['https://invented.example/article']})


def test_standard_proposal_approval_uses_task_service_operation_and_audit(db):
    from datetime import date
    from app.models import AgentAuditEvent, TaskManagerOperation, TaskStatusHistory
    from app.services.proposal_service import apply_proposal_action

    user = User(username='proposal-task-boundary', password_hash='unused')
    db.add(user); db.flush()
    proposal = AgentProposal(
        user_id=user.id,
        proposal_key='proposal-task-boundary-1',
        proposal_type='task_plan',
        plan_date=date(2026, 10, 19),
        status='PENDING',
        title='Task Service boundary check',
        items=[{'domain': 'stem', 'title': 'Task Service boundary task', 'action': 'Implement boundary',
                'estimated_minutes': 25, 'why_today': 'Verify unified writes',
                'acceptance_criteria': 'Created through Task Service', 'evidence_required': 'pytest'}],
        rationale='Test architecture boundary',
    )
    db.add(proposal); db.commit()

    _, created, skipped = apply_proposal_action(db, user, proposal, action='approve', items=None, note='Approved')

    assert len(created) == 1 and skipped == []
    assert db.query(TaskManagerOperation).filter_by(user_id=user.id, status='applied').count() == 1
    assert db.query(TaskStatusHistory).filter_by(task_id=created[0], reason='created_by_task_manager').count() == 1
    assert db.query(AgentAuditEvent).filter_by(user_id=user.id, event_type='task_manager_created').count() == 1
    assert db.get(Task, created[0]).task_code == f'ai-{proposal.id}-1'


def test_distinct_proposals_receive_distinct_task_operation_ids(db):
    from datetime import date
    from app.models import TaskManagerOperation

    user = User(username="distinct-proposal-operations", password_hash="unused")
    db.add(user); db.flush()
    proposals = [AgentProposal(
        user_id=user.id, proposal_key=f"distinct-op-{index}", proposal_type="task_plan",
        plan_date=date(2026, 10, 19), status="PENDING", title=f"Distinct operation {index}",
        items=[{"domain": "stem", "title": f"Distinct proposal task {index}", "action": "Create task",
                "estimated_minutes": 15, "why_today": "Check key separation",
                "acceptance_criteria": "Each proposal is independently applied", "evidence_required": "pytest"}],
    ) for index in (1, 2)]
    db.add_all(proposals); db.commit()
    created_task_ids = []
    for proposal in proposals:
        _, created, skipped = apply_proposal_action(db, user, proposal, action="approve", items=None, note="approve")
        assert len(created) == 1 and skipped == []
        created_task_ids.extend(created)
    operations = db.query(TaskManagerOperation).filter_by(user_id=user.id, status="applied").all()
    assert len(operations) == 2
    assert len({operation.client_operation_id for operation in operations}) == 2
    assert len(set(created_task_ids)) == 2


def test_proposal_approval_rolls_back_task_when_source_is_not_owned(db):
    from datetime import date
    from app.models import ChatConversation, TaskManagerOperation
    from app.services.proposal_service import apply_proposal_action

    owner = User(username='proposal-source-owner', password_hash='unused')
    other = User(username='proposal-source-other', password_hash='unused')
    db.add_all([owner, other]); db.flush()
    foreign_conversation = ChatConversation(id='00000000-0000-0000-0000-0000000000ab', user_id=other.id)
    db.add(foreign_conversation); db.flush()
    proposal = AgentProposal(
        user_id=owner.id,
        proposal_key='proposal-invalid-source-1',
        proposal_type='task_plan',
        plan_date=date(2026, 10, 19),
        status='PENDING',
        title='Reject foreign source reference',
        items=[{'domain': 'stem', 'title': 'Must roll back', 'action': 'Create then reject source',
                'estimated_minutes': 20, 'why_today': 'Exercise rollback',
                'acceptance_criteria': 'No task persists', 'evidence_required': 'Database assertions',
                'source_conversation_id': foreign_conversation.id}],
        rationale='Test ownership and atomicity',
    )
    db.add(proposal); db.commit()

    with pytest.raises(ValueError, match='TASK_SOURCE_NOT_AVAILABLE'):
        apply_proposal_action(db, owner, proposal, action='approve', items=None, note='Approval attempt')

    db.expire_all()
    assert db.get(AgentProposal, proposal.id).status == 'PENDING'
    assert db.query(Task).filter_by(user_id=owner.id, title='Must roll back').count() == 0
    assert db.query(TaskManagerOperation).filter_by(user_id=owner.id).count() == 0


def test_concurrent_approval_of_same_proposal_creates_one_task(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    from threading import Barrier
    from sqlalchemy.orm import sessionmaker
    from app.models import TaskManagerOperation

    database_path = tmp_path / "proposal-approval-race.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", connect_args={"timeout": 15, "check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sessions() as seed:
            user = User(username="concurrent-approval", password_hash="unused")
            seed.add(user); seed.flush()
            proposal = AgentProposal(
                user_id=user.id, proposal_key="concurrent-approval-case", proposal_type="task_plan",
                plan_date=date(2026, 10, 19), status="PENDING", title="Concurrent approval",
                items=[{"domain": "stem", "title": "One concurrent task", "action": "Create once",
                        "estimated_minutes": 15, "why_today": "Verify idempotency",
                        "acceptance_criteria": "Exactly one task", "evidence_required": "Database count"}],
            )
            seed.add(proposal); seed.commit()
            user_id, proposal_id = user.id, proposal.id

        barrier = Barrier(2)
        def approve():
            with sessions() as session:
                user = session.get(User, user_id)
                candidate = session.get(AgentProposal, proposal_id)
                barrier.wait(timeout=10)
                return apply_proposal_action(session, user, candidate, action="approve", items=None, note="parallel")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: approve(), range(2)))
        with sessions() as verify:
            assert verify.query(Task).filter_by(user_id=user_id, title="One concurrent task").count() == 1
            assert verify.query(TaskManagerOperation).filter_by(user_id=user_id, status="applied").count() == 1
            approved = verify.get(AgentProposal, proposal_id)
            assert approved.status == "APPROVED"
        created_counts = sorted(len(result[1]) for result in results)
        assert created_counts == [0, 1]
    finally:
        engine.dispose()


def test_proposal_source_must_be_verified_in_same_run_and_user(db):
    user = User(username='run-scoped-sources', password_hash='unused'); db.add(user); db.flush()
    old_run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    source_url = 'https://docs.example.org/guide'
    tool_call(db, old_run, user.id, 'web_search', True, 'completed', result={'source_urls':[source_url]})
    current_run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'personal,web')
    item = {'domain':'stem','title':'Source-bound practice','action':'Implement a small example','estimated_minutes':30,'why_today':'Practice the cited concept','acceptance_criteria':'A test passes','evidence_required':'Test output','sources':[source_url]}
    async def execute(name, args):
            return await execute_agent_tool(db, current_run, user.id, name, json.dumps(args), personal=True, web=True, task_proposal_authorized=(name == "propose_task"))
    asyncio.run(execute('get_active_tasks', {}))
    with pytest.raises(ToolArgumentsError, match='Source URL has not been returned'):
        asyncio.run(execute('propose_task', item))
    tool_call(db, current_run, user.id, 'web_search', True, 'completed', result={'source_urls':[source_url]})
    result = asyncio.run(execute('propose_task', item))
    proposal = db.get(AgentProposal, result['proposal_id'])
    assert result['status'] == 'PENDING'
    assert proposal.run_id == current_run.id and proposal.user_id == user.id


def test_model_claim_without_persisted_propose_tool_is_reported_not_created(db, monkeypatch):
    from app.models import AIProvider, AgentToolCall
    from app.services import agent_runner
    user = User(username='false-proposal-claim', password_hash='unused'); db.add(user); db.flush()
    provider = AIProvider(user_id=user.id, provider='deepseek', base_url='https://api.deepseek.com', model='deepseek-flash', enabled=True, total_prompt_tokens=0, total_completion_tokens=0)
    monkeypatch.setattr(agent_runner, 'ensure_provider', lambda _db, _name, _uid: provider)
    monkeypatch.setattr(agent_runner, 'require_user_api_key', lambda _provider, _user_id: 'mock-only-key')
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'choices':[{'message':{'role':'assistant','content':'任务草案已成功创建，状态为 PENDING。','tool_calls':None}}],'usage':{'prompt_tokens':11,'completion_tokens':8}}
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr(agent_runner.httpx, 'AsyncClient', Client)
    result = asyncio.run(agent_runner.run_chat(db, user.id, '请创建任务草案并等待审批', [], include_personal=True, enable_web=False))
    assert result['proposal_status'] == 'not_created'
    assert result['proposal'] is None
    assert '尚未生成任务草案' in result['content']
    assert db.query(AgentProposal).filter_by(user_id=user.id).count() == 0
    assert db.query(AgentToolCall).filter_by(run_id=result['run_id'], tool_name='propose_task').count() == 0


def test_tool_whitelist_and_schema_are_enforced(db):
    user = User(username='owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, '')
    with pytest.raises(ToolDenied, match='TOOL_NOT_ALLOWED'):
        asyncio.run(execute_agent_tool(db, run, user.id, 'get_active_tasks', '{}', personal=False, web=False))
    with pytest.raises(ToolArgumentsError):
        asyncio.run(execute_agent_tool(db, run, user.id, 'get_active_tasks', '{"user_id":999}', personal=True, web=False))


def test_web_search_and_fetch_have_per_run_limits_and_source_validation(db):
    from app.models import AgentToolCall
    user = User(username='web-owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    db.add_all([AgentToolCall(run_id=run.id, user_id=user.id, tool_name='web_search', allowed=True, status='completed', result_summary=json.dumps({'source_urls':['https://docs.example.org/guide']})), AgentToolCall(run_id=run.id, user_id=user.id, tool_name='web_search', allowed=True, status='completed', result_summary='{}')])
    db.flush()
    with pytest.raises(ToolDenied, match='SEARCH_CALL_LIMIT'):
        asyncio.run(execute_agent_tool(db, run, user.id, 'web_search', json.dumps({'query':'another search'}), personal=False, web=True))
    with pytest.raises(ToolDenied, match='FETCH_URL_NOT_FROM_SEARCH'):
        asyncio.run(execute_agent_tool(db, run, user.id, 'web_fetch', json.dumps({'url':'https://invented.example.org/page'}), personal=False, web=True))
    db.add_all([AgentToolCall(run_id=run.id, user_id=user.id, tool_name='web_fetch', allowed=True, status='completed'), AgentToolCall(run_id=run.id, user_id=user.id, tool_name='web_fetch', allowed=True, status='completed')]); db.flush()
    with pytest.raises(ToolDenied, match='FETCH_CALL_LIMIT'):
        asyncio.run(execute_agent_tool(db, run, user.id, 'web_fetch', json.dumps({'url':'https://docs.example.org/guide'}), personal=False, web=True))


def test_fetch_can_see_just_completed_search_with_autoflush_disabled(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        from app.services import tool_registry as registry
        user = User(username='same-run', password_hash='unused'); session.add(user); session.flush()
        run = start_run(session, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
        # The audit helper flushes, so the next tool sees this source in the same
        # transaction, matching the production SessionLocal configuration.
        tool_call(session, run, user.id, 'web_search', True, 'completed', result={'source_urls':['https://docs.example.org/page']})
        async def fake_fetch(args):
            return {'status':'fetched','url':args.url,'title':'Docs','text':'body','retrieved_at':'now'}
        monkeypatch.setattr(registry, '_web_fetch', fake_fetch)
        fetched = asyncio.run(registry.execute_agent_tool(session, run, user.id, 'web_fetch', json.dumps({'url':'https://docs.example.org/page'}), personal=False, web=True))
        assert fetched['status'] == 'fetched'
        count = session.query(__import__('app.models',fromlist=['AgentToolCall']).AgentToolCall).filter_by(run_id=run.id,tool_name='web_fetch').count()
        assert count == 1
    engine.dispose()


def test_search_domain_filters_accept_official_hosts_and_reject_bad_values():
    from app.services.tool_registry import SearchArguments
    from pydantic import ValidationError
    request = SearchArguments(query='Tool Calling', include_domains=['API-DOCS.DEEPSEEK.COM', 'platform.openai.com'])
    assert request.include_domains == ['api-docs.deepseek.com', 'platform.openai.com']
    with pytest.raises(ValidationError):
        SearchArguments(query='Tool Calling', include_domains=['https://platform.openai.com'])


def test_source_matching_normalizes_fragment_path_and_tracking_but_keeps_functional_queries(db):
    from app.services.tool_registry import _source_was_searched
    user = User(username='source-owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    source = 'https://developers.openai.com/api/docs/guides/function-calling#custom-tools'
    tool_call(db, run, user.id, 'web_search', True, 'completed', result={'source_urls':[source]})
    assert _source_was_searched(db, run, user.id, 'https://developers.openai.com/api/docs/guides/function-calling')
    assert _source_was_searched(db, run, user.id, 'https://developers.openai.com/api/docs/guides/function-calling/?utm_source=results')
    assert _source_was_searched(db, run, user.id, 'https://developers.openai.com/api/docs/guides/function%2Dcalling#overview')
    assert not _source_was_searched(db, run, user.id, 'https://developers.openai.com/api/docs/guides/function-calling?api-mode=responses')
    assert not _source_was_searched(db, run, user.id, 'https://api.developers.openai.com/api/docs/guides/function-calling')
    assert not _source_was_searched(db, run, user.id, 'https://developers.openai.com.evil.example/api/docs/guides/function-calling')
    assert not _source_was_searched(db, run, user.id, 'https://evil.example/api/docs/guides/function-calling')
    other_run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    assert not _source_was_searched(db, other_run, user.id, 'https://developers.openai.com/api/docs/guides/function-calling')


def test_search_source_from_second_search_is_usable_in_same_run(db, monkeypatch):
    from app.models import AgentToolCall
    from app.services import tool_registry as registry
    user = User(username='second-search-source', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    async def fake_search(_db, _uid, args):
        return {'provider':'tavily','results':[{'url':'https://developers.openai.com/api/docs/guides/function-calling#custom-tools','title':'OpenAI official','published_date':None,'source':'developers.openai.com','retrieved_at':'now','snippet':'summary'}]}
    async def fake_fetch(args):
        return {'status':'fetched','url':args.url,'title':'OpenAI official','text':'actual page body','truncated':False,'retrieved_at':'now'}
    monkeypatch.setattr(registry, '_web_search', fake_search)
    monkeypatch.setattr(registry, '_web_fetch', fake_fetch)
    search = asyncio.run(registry.execute_agent_tool(db, run, user.id, 'web_search', json.dumps({'query':'OpenAI official function calling'}), personal=False, web=True))
    assert search['results'][0]['url'].startswith('https://developers.openai.com/')
    fetched = asyncio.run(registry.execute_agent_tool(db, run, user.id, 'web_fetch', json.dumps({'url':'https://developers.openai.com/api/docs/guides/function-calling'}), personal=False, web=True))
    assert fetched['status'] == 'fetched' and fetched['text'] == 'actual page body'
    assert db.query(AgentToolCall).filter_by(run_id=run.id, status='completed').count() == 2


def test_fetch_failures_consume_budget_and_are_distinct_from_budget_denials(db, monkeypatch):
    from app.models import AgentToolCall
    from app.services import tool_registry as registry
    user = User(username='fetch-budget-owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'web')
    tool_call(db, run, user.id, 'web_search', True, 'completed', result={'source_urls':['https://docs.example.org/a','https://docs.example.org/b']})
    async def failed_fetch(args): raise ToolDenied('FETCH_HTTP_502')
    monkeypatch.setattr(registry, '_web_fetch', failed_fetch)
    for url in ['https://docs.example.org/a','https://docs.example.org/b']:
        with pytest.raises(ToolDenied, match='FETCH_HTTP_502'):
            asyncio.run(registry.execute_agent_tool(db, run, user.id, 'web_fetch', json.dumps({'url':url}), personal=False, web=True))
    with pytest.raises(ToolDenied, match='FETCH_CALL_LIMIT'):
        asyncio.run(registry.execute_agent_tool(db, run, user.id, 'web_fetch', json.dumps({'url':'https://docs.example.org/a'}), personal=False, web=True))
    calls = db.query(AgentToolCall).filter_by(run_id=run.id, tool_name='web_fetch').all()
    assert [call.status for call in calls] == ['failed','failed','denied']


def test_proposal_tool_sees_duplicate_check_with_autoflush_disabled():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        user = User(username='proposal-same-run', password_hash='unused'); session.add(user); session.flush()
        run = start_run(session, user.id, 'agent_chat', 'mock', 'mock', None, 'personal')
        item = {'domain':'stem','title':'One exercise','action':'Write a tiny function','estimated_minutes':30,'why_today':'Practice','acceptance_criteria':'One passing test','evidence_required':'Test result'}
        asyncio.run(execute_agent_tool(session, run, user.id, 'get_active_tasks', '{}', personal=True, web=False))
        proposal = asyncio.run(execute_agent_tool(session, run, user.id, 'propose_task', json.dumps(item), personal=True, web=False, task_proposal_authorized=True))
        assert proposal['status'] == 'PENDING'
        assert session.query(Task).count() == 0
    engine.dispose()


def test_propose_task_goal_contract_knowledge_citations_and_confirmation_are_idempotent(db):
    from sqlalchemy import select
    from app.models import KnowledgeTaskLink
    from app.services.tool_registry import ProposeTaskArguments, TOOLS
    user = User(username='proposal-contract', password_hash='unused'); db.add(user); db.flush()
    card = KnowledgeCard(user_id=user.id, title='German agent notes', summary='Build and test a German agent', content_markdown='Keep the agent task focused and verify a small working flow.', domain='bilingual', knowledge_type='Concept', review_status='DRAFT')
    db.add(card); db.flush()
    assert card.id == 1
    task = Task(user_id=user.id, title='Existing task #7', module='bilingual'); db.add(task); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'active_tasks,knowledge')
    base = {'domain':'bilingual', 'title':'Build the German agent flow', 'action':'Implement a small agent flow', 'estimated_minutes':45,
            'why_today':'Use the saved design notes', 'acceptance_criteria':'A focused test verifies the flow', 'evidence_required':'Test output',
            'existing_task_id':task.id, 'knowledge_card_ids':[card.id], 'sources':[]}
    for goal in ({}, {'goal_id': None}):
        parsed = ProposeTaskArguments.model_validate({**base, **goal})
        assert parsed.goal_id is None and parsed.existing_task_id == task.id
        assert 'due_date' not in TOOLS['propose_task'].openai_schema()['function']['parameters']['properties']
    tool = lambda name, args: asyncio.run(execute_agent_tool(db, run, user.id, name, json.dumps(args), personal=True, web=False, task_proposal_authorized=(name == 'propose_task')))
    tool('get_active_tasks', {})
    with pytest.raises(ToolArgumentsError, match='KNOWLEDGE_CARD_NOT_AVAILABLE'):
        tool('propose_task', {**base, 'knowledge_card_ids':[999]})
    assert db.query(AgentProposal).count() == 0
    first = tool('propose_task', base)
    second = tool('propose_task', {**base, 'title':'Test the German agent flow'})
    assert first['status'] == second['status'] == 'PENDING'
    assert db.query(Task).count() == 1
    assert db.query(AgentProposal).count() == 2
    assert db.get(AgentProposal, first['proposal_id']).items[0]['knowledge_card_ids'] == [card.id]
    assert not hasattr(db.get(Task, task.id), 'parent_task_id')
    proposal = db.get(AgentProposal, first['proposal_id'])
    edited = [{**proposal.items[0], 'title':'Edited German agent flow'}]
    apply_proposal_action(db, user, proposal, action='edit', items=edited, note='Edited before confirmation')
    assert db.query(Task).count() == 1
    _, created, _ = apply_proposal_action(db, user, proposal, action='approve', items=None, note='Confirmed')
    assert len(created) == 1
    real_task = db.get(Task, created[0])
    assert real_task.title == 'Edited German agent flow'
    assert real_task.start_date is None and real_task.due_date is None
    assert db.scalar(select(KnowledgeTaskLink).where(KnowledgeTaskLink.user_id == user.id, KnowledgeTaskLink.card_id == card.id, KnowledgeTaskLink.task_id == real_task.id))
    apply_proposal_action(db, user, proposal, action='approve', items=None, note='Replay')
    assert db.query(Task).count() == 2


def test_invalid_tool_arguments_audit_exact_safe_field_without_raw_values(db):
    from sqlalchemy import select
    user = User(username='diagnostic-owner', password_hash='unused'); db.add(user); db.flush()
    run = start_run(db, user.id, 'agent_chat', 'mock', 'mock', None, 'personal')
    raw = {'domain':'stem', 'title':'DO NOT LOG THIS SECRET TEXT', 'action':'implement', 'estimated_minutes':'forty', 'why_today':'practice', 'acceptance_criteria':'pass', 'evidence_required':'output'}
    with pytest.raises(ToolArgumentsError) as error:
        asyncio.run(execute_agent_tool(db, run, user.id, 'propose_task', json.dumps(raw), personal=True, web=False, task_proposal_authorized=True))
    assert error.value.diagnostic == {'error_code':'INVALID_TOOL_ARGUMENTS', 'validation_stage':'tool_arguments', 'field_path':'estimated_minutes', 'expected_type_or_constraint':'integer', 'actual_type':'string', 'recoverable':True}
    call = db.scalar(select(AgentToolCall).where(AgentToolCall.run_id == run.id, AgentToolCall.tool_name == 'propose_task'))
    assert json.loads(call.result_summary)['validation']['field_path'] == 'estimated_minutes'
    assert 'DO NOT LOG THIS SECRET TEXT' not in call.arguments_summary + call.result_summary
    assert db.query(AgentProposal).count() == db.query(Task).count() == 0


def test_fetch_pins_public_ip_and_preserves_tls_hostname(monkeypatch):
    from app.services import tool_registry as registry
    monkeypatch.setattr(registry, '_resolve_public_url', lambda url: ('docs.example.com', '1.1.1.1'))
    captured = {}
    class Response:
        status_code = 200
        headers = {'content-type': 'text/html'}
        encoding = 'utf-8'
        def raise_for_status(self): pass
        async def aiter_bytes(self): yield b'<title>Official docs</title><main>Function calls return tool results.</main>'
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Client:
        def __init__(self, **kwargs): assert kwargs['trust_env'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def stream(self, method, url, **kwargs): captured.update(url=str(url), **kwargs); return Response()
    monkeypatch.setattr(registry.httpx, 'AsyncClient', Client)
    result = asyncio.run(_web_fetch(FetchArguments(url='https://docs.example.com/tool-calling')))
    assert captured['url'] == 'https://1.1.1.1/tool-calling'
    assert captured['headers']['Host'] == captured['extensions']['sni_hostname'] == 'docs.example.com'
    assert result['url'] == 'https://docs.example.com/tool-calling'
    assert result['status'] == 'fetched'


def test_dns_rebinding_to_private_ip_is_blocked_before_fetch(monkeypatch):
    from app.services import tool_registry as registry
    answers = iter(['8.8.8.8', '127.0.0.1'])
    def resolve(host, port, type):
        ip = next(answers)
        return [(2, 1, 6, '', (ip, port))]
    monkeypatch.setattr(registry.socket, 'getaddrinfo', resolve)
    streams = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def stream(self, *args, **kwargs): streams.append(True); raise AssertionError('must not connect')
    monkeypatch.setattr(registry.httpx, 'AsyncClient', Client)
    with pytest.raises(ToolArgumentsError, match='Private, reserved'):
        asyncio.run(registry._web_fetch(FetchArguments(url='https://rebind.example/path')))
    assert streams == []


def test_redirect_to_private_or_non_https_target_is_blocked(monkeypatch):
    from app.services import tool_registry as registry
    original = registry._resolve_public_url
    def resolve(url):
        if url.startswith('https://docs.example.org/'):
            return 'docs.example.org', '1.1.1.1'
        return original(url)
    monkeypatch.setattr(registry, '_resolve_public_url', resolve)
    requests = []
    class Response:
        status_code = 302
        headers = {'location':'http://127.0.0.1/latest'}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def stream(self, *args, **kwargs): requests.append(True); return Response()
    monkeypatch.setattr(registry.httpx, 'AsyncClient', Client)
    with pytest.raises(ToolArgumentsError):
        asyncio.run(registry._web_fetch(FetchArguments(url='https://docs.example.org/start')))
    assert len(requests) == 1
