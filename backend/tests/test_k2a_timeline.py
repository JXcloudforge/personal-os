from datetime import date, timedelta

from test_chat_workspace import _client, _csrf
from app.database import Base, SessionLocal, engine
from app.models import ChatConversation, ChatMessage, DailyTaskSnapshot, TaskConversationLink, TaskPurgeReceipt
from sqlalchemy import select


def setup_module():
    Base.metadata.create_all(bind=engine)


def test_task_date_defaults_duration_edits_and_timeline_history():
    client, _ = _client()
    try:
        headers = _csrf(client)
        today = date.fromisoformat(client.get('/api/dashboard').json()['today_date'])
        created = client.post('/api/tasks', headers=headers, json={'title': 'three day task'}).json()
        assert created['start_date'] == today.isoformat() and created['due_date'] == today.isoformat()
        assert created['expected_duration_days'] == 1

        three_day_due = today + timedelta(days=2)
        updated = client.patch(f"/api/tasks/{created['id']}", headers=headers, json={'expected_duration_days': 3})
        assert updated.status_code == 200
        assert updated.json()['due_date'] == three_day_due.isoformat()
        assert updated.json()['expected_duration_days'] == 3
        detail = client.get(f"/api/tasks/{created['id']}").json()
        assert len(detail['date_history']) == 1

        future = today + timedelta(days=8)
        future_task = client.post('/api/tasks', headers=headers, json={'title': 'future', 'start_date': future.isoformat(), 'due_date': future.isoformat(), 'expected_duration_days': 1}).json()
        overdue = today - timedelta(days=1)
        overdue_task = client.post('/api/tasks', headers=headers, json={'title': 'overdue', 'start_date': overdue.isoformat(), 'due_date': overdue.isoformat(), 'expected_duration_days': 1}).json()
        assert {item['id'] for item in client.get('/api/tasks?timeline=today').json()} == {created['id']}
        assert {item['id'] for item in client.get('/api/tasks?timeline=future').json()} == {future_task['id']}
        assert {item['id'] for item in client.get('/api/tasks?timeline=overdue').json()} == {overdue_task['id']}
        assert client.patch(f"/api/tasks/{created['id']}", headers=headers, json={'expected_duration_days': None}).status_code == 422
        client.close()
    finally:
        client.close()


def test_daily_settlement_is_idempotent_and_never_changes_task_status():
    client, _ = _client()
    try:
        headers = _csrf(client)
        today = client.get('/api/dashboard').json()['today_date']
        task = client.post('/api/tasks', headers=headers, json={'title': 'settled task'}).json()
        body = {'review_date': today, 'actual_summary': '用户自报：完成了阅读练习。', 'task_entries': [{'task_id': task['id'], 'report_category': 'completed_today', 'actual_progress': '阅读 20 分钟', 'evidence_note': '完成练习 1'}]}
        first = client.post('/api/reviews/daily', headers=headers, json=body)
        assert first.status_code == 200
        assert first.json()['task_entries'][0]['revision'] == 1
        assert client.get(f"/api/tasks/{task['id']}").json()['status'] == 'TODO'

        body['actual_summary'] = '修订：完成阅读并记录词汇。'
        second = client.post('/api/reviews/daily', headers=headers, json=body)
        assert second.status_code == 200
        assert second.json()['task_entries'][0]['revision'] == 2
        assert second.json()['actual_summary'].startswith('修订')
        assert len(client.get('/api/reviews/daily').json()) == 1
        assert len(client.get(f"/api/reviews/daily/{today}").json()['task_entries']) == 1
        assert client.get('/api/dashboard').json()['today_review']['task_entries'][0]['status_at_settlement'] == 'TODO'
    finally:
        client.close()


def test_daily_settlement_rejects_foreign_and_false_overdue_task():
    owner, _ = _client()
    other, _ = _client()
    try:
        today = owner.get('/api/dashboard').json()['today_date']
        due_future = (date.fromisoformat(today) + timedelta(days=2)).isoformat()
        task = owner.post('/api/tasks', headers=_csrf(owner), json={'title': 'owned task', 'start_date': today, 'due_date': due_future, 'expected_duration_days': 3}).json()
        body = {'review_date': today, 'task_entries': [{'task_id': task['id'], 'report_category': 'overdue'}]}
        assert owner.post('/api/reviews/daily', headers=_csrf(owner), json=body).status_code == 422
        assert other.post('/api/reviews/daily', headers=_csrf(other), json=body).status_code == 404
    finally:
        owner.close()
        other.close()


def test_task_trash_detail_and_confirmed_purge_preserve_independent_chat_and_are_idempotent():
    client, user_id = _client()
    other, _ = _client()
    try:
        headers = _csrf(client)
        task = client.post('/api/tasks', headers=headers, json={'title': 'Disposable task fixture', 'description': 'private detail'}).json()
        conversation = client.post('/api/agent/chat/conversations', headers=headers, json={'domain': 'projects'}).json()
        with SessionLocal() as db:
            message = ChatMessage(user_id=user_id, conversation_id=conversation['id'], role='user', content='Keep this independent conversation')
            db.add(message); db.flush()
            db.add(TaskConversationLink(user_id=user_id, task_id=task['id'], conversation_id=conversation['id'], message_id=message.id))
            db.commit()
        assert task['id'] in {item['id'] for item in client.get('/api/dashboard').json()['today_tasks']}
        assert client.delete(f"/api/tasks/{task['id']}", headers=headers).status_code == 200
        assert task['id'] not in {item['id'] for item in client.get('/api/dashboard').json()['today_tasks']}
        assert client.get(f"/api/tasks/{task['id']}").status_code == 404
        assert client.get(f"/api/tasks/{task['id']}?include_deleted=true").json()['title'] == task['title']
        assert [item['id'] for item in client.get('/api/tasks?include_deleted=true').json()] == [task['id']]
        restored = client.post(f"/api/tasks/{task['id']}/restore", headers=headers, json={})
        assert restored.status_code == 200 and restored.json()['id'] == task['id']
        assert client.get(f"/api/tasks/{task['id']}").json()['title'] == task['title']
        assert client.get(f"/api/tasks/{task['id']}/links").json()[0]['conversation_id'] == conversation['id']
        assert client.delete(f"/api/tasks/{task['id']}", headers=headers).status_code == 200
        assert client.get(f"/api/tasks/{task['id']}/permanent-delete-preview").json()['can_purge'] is True
        assert client.post(f"/api/tasks/{task['id']}/permanent-delete", headers=headers, json={'confirm_title': 'wrong'}).status_code == 422
        assert other.get(f"/api/tasks/{task['id']}/permanent-delete-preview").status_code == 404
        assert other.post(f"/api/tasks/{task['id']}/permanent-delete", headers=_csrf(other), json={'confirm_title': task['title']}).status_code == 404
        assert client.post(f"/api/tasks/{task['id']}/permanent-delete", json={'confirm_title': task['title']}).status_code == 403

        purged = client.post(f"/api/tasks/{task['id']}/permanent-delete", headers=headers, json={'confirm_title': task['title']})
        assert purged.status_code == 200 and purged.json()['purged'] is True
        assert purged.json()['cleanup']['conversation_links'] == 1
        assert client.get(f"/api/tasks/{task['id']}").status_code == 410
        assert client.get(f"/api/tasks/{task['id']}?include_deleted=true").status_code == 410
        assert client.post(f"/api/tasks/{task['id']}/restore", headers=headers, json={}).status_code == 410
        replay = client.post(f"/api/tasks/{task['id']}/permanent-delete", headers=headers, json={'confirm_title': task['title']})
        assert replay.status_code == 200 and replay.json()['idempotent_replay'] is True
        later = client.post('/api/tasks', headers=headers, json={'title': 'Non-reused task ID'}).json()
        assert later['id'] > task['id']
        assert client.get(f"/api/tasks/{task['id']}").status_code == 410
        assert client.get(f"/api/agent/chat/conversations/{conversation['id']}").status_code == 200
        with SessionLocal() as db:
            assert db.get(ChatConversation, conversation['id']) is not None
            assert db.scalar(select(TaskConversationLink.id).where(TaskConversationLink.task_id == task['id'])) is None
            assert db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task['id'])) is not None
        with engine.connect() as connection:
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    finally:
        client.close(); other.close()


def test_task_permanent_purge_is_blocked_by_daily_settlement_without_partial_changes():
    client, user_id = _client()
    try:
        headers = _csrf(client)
        today = client.get('/api/dashboard').json()['today_date']
        task = client.post('/api/tasks', headers=headers, json={'title': 'Settlement-linked task'}).json()
        body = {'review_date': today, 'actual_summary': 'Keep settlement facts', 'task_entries': [{'task_id': task['id'], 'report_category': 'in_progress', 'actual_progress': 'Recorded progress', 'evidence_note': 'Verified note'}]}
        assert client.post('/api/reviews/daily', headers=headers, json=body).status_code == 200
        assert client.delete(f"/api/tasks/{task['id']}", headers=headers).status_code == 200
        preview = client.get(f"/api/tasks/{task['id']}/permanent-delete-preview").json()
        assert preview['can_purge'] is False and preview['blockers'][0]['type'] == 'daily_settlement_snapshots'
        response = client.post(f"/api/tasks/{task['id']}/permanent-delete", headers=headers, json={'confirm_title': task['title']})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'TASK_PURGE_BLOCKED'
        assert client.get(f"/api/tasks/{task['id']}?include_deleted=true").json()['title'] == task['title']
        assert client.get(f"/api/reviews/daily/{today}").json()['task_entries'][0]['actual_progress'] == 'Recorded progress'
        with SessionLocal() as db:
            assert db.scalar(select(DailyTaskSnapshot.id).where(DailyTaskSnapshot.task_id == task['id'])) is not None
            assert db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task['id'])) is None
    finally:
        client.close()
