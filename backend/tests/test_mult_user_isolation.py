"""Cross-user API regression checks against pytest's isolated SQLite database."""

from datetime import date
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AIProvider,
    AgentProposal,
    AgentRun,
    AgentToolCall,
    ChatConversation,
    ChatMessage,
    DailyReview,
    KnowledgeCard,
    KnowledgeTaskLink,
    StudyRecord,
    Task,
    TaskConversationLink,
    TaskEvidence,
    User,
    Workout,
)
from app.services.provider_service import encrypt_api_key


def setup_module():
    Base.metadata.create_all(bind=engine)


def _login(username: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def test_core_resources_are_isolated_between_two_authenticated_users(monkeypatch):
    monkeypatch.setattr(settings, "credential_master_key", Fernet.generate_key().decode())
    suffix = uuid4().hex
    password = "Synthetic-Isolation-Password-2026!"
    username_a, username_b = f"isolation-a-{suffix}", f"isolation-b-{suffix}"

    with SessionLocal() as db:
        user_a = User(username=username_a, password_hash=hash_password(password))
        user_b = User(username=username_b, password_hash=hash_password(password))
        db.add_all([user_a, user_b])
        db.flush()
        task_a = Task(user_id=user_a.id, title=f"private-task-{suffix}", module="stem")
        card_a = KnowledgeCard(user_id=user_a.id, title=f"private-card-{suffix}", content_markdown=f"private-knowledge-marker-{suffix}", domain="stem", knowledge_type="Concept", review_status="DRAFT")
        conversation_a = ChatConversation(id=str(uuid4()), user_id=user_a.id, title=f"private-chat-{suffix}", domain="stem")
        run_a = AgentRun(user_id=user_a.id, workflow="agent_chat", mode="mock", provider="mock", model="mock", authorized_scope="user_message", status="completed")
        review_a = DailyReview(user_id=user_a.id, review_date=date(2026, 10, 19), actual_summary=f"private-review-{suffix}")
        study_a = StudyRecord(user_id=user_a.id, language="en", topic=f"private-study-{suffix}", activity_type="reading")
        workout_a = Workout(user_id=user_a.id, date=date(2026, 10, 19), training_type="private-workout")
        db.add_all([task_a, card_a, conversation_a, run_a, review_a, study_a, workout_a])
        db.flush()
        message_a = ChatMessage(user_id=user_a.id, conversation_id=conversation_a.id, run_id=run_a.id, role="assistant", content=f"private-message-{suffix}", status="completed")
        proposal_a = AgentProposal(user_id=user_a.id, run_id=run_a.id, proposal_key=f"proposal-{suffix}", proposal_type="task_plan", plan_date=date(2026, 10, 19), status="PENDING", title=f"private-proposal-{suffix}", items=[], rationale="synthetic")
        provider_a = AIProvider(user_id=user_a.id, provider="deepseek", base_url="https://api.example.invalid", model="mock", encrypted_api_key=encrypt_api_key(f"synthetic-provider-secret-{suffix}"), enabled=True)
        db.add_all([message_a, proposal_a, provider_a])
        db.flush()
        call_a = AgentToolCall(run_id=run_a.id, user_id=user_a.id, tool_name="get_active_tasks", allowed=True, status="completed", result_summary=f'{{"marker":"private-tool-{suffix}"}}')
        db.add(call_a)
        db.commit()
        ids = {
                "a_id": user_a.id, "b_id": user_b.id, "task": task_a.id, "card": card_a.id,
            "conversation": conversation_a.id, "message": message_a.id,
            "run": run_a.id, "proposal": proposal_a.id, "review_date": "2026-10-19",
            "study": study_a.id, "provider_secret": f"synthetic-provider-secret-{suffix}",
            "task_title": task_a.title, "card_title": card_a.title,
        }

    client_a, client_b = _login(username_a, password), _login(username_b, password)
    try:
        # Owner reads succeed; the other account's list/retrieval responses stay empty.
        assert client_a.get(f"/api/tasks/{ids['task']}").status_code == 200
        assert client_a.get(f"/api/knowledge/{ids['card']}").status_code == 200
        assert client_a.get(f"/api/agent/chat/conversations/{ids['conversation']}").status_code == 200
        assert client_b.get("/api/tasks").json() == []
        assert client_b.get("/api/knowledge").json() == []
        foreign_knowledge = client_b.post("/api/knowledge/retrieve", headers=_csrf(client_b), json={"query": ids["card_title"]})
        assert foreign_knowledge.status_code == 200 and "private-knowledge-marker" not in foreign_knowledge.text
        assert client_b.get(f"/api/agent/chat/conversations/{ids['conversation']}").status_code == 404

        # Foreign reads and writes use non-enumerating 404s and do not change owner data.
        assert client_b.get(f"/api/tasks/{ids['task']}").status_code == 404
        assert client_b.patch(f"/api/tasks/{ids['task']}", headers=_csrf(client_b), json={"title": "cross-user-write"}).status_code == 404
        assert client_b.delete(f"/api/tasks/{ids['task']}", headers=_csrf(client_b)).status_code == 404
        assert client_b.post(f"/api/tasks/{ids['task']}/evidence", headers=_csrf(client_b), json={"content": "cross-user-evidence", "verification_level": "E3_TEST"}).status_code == 404
        assert client_b.get(f"/api/knowledge/{ids['card']}").status_code == 404
        assert client_b.patch(f"/api/knowledge/{ids['card']}", headers=_csrf(client_b), json={"title": "cross-user-write"}).status_code == 404
        assert client_b.delete(f"/api/knowledge/{ids['card']}", headers=_csrf(client_b)).status_code == 404
        assert client_b.patch(f"/api/agent/proposals/{ids['proposal']}", headers=_csrf(client_b), json={"action": "approve"}).status_code == 404
        assert client_b.get(f"/api/agent/runs/{ids['run']}/tools").status_code == 404
        assert client_b.patch(f"/api/agent/chat/conversations/{ids['conversation']}", headers=_csrf(client_b), json={"title": "cross-user-write"}).status_code == 404
        assert client_b.delete(f"/api/agent/chat/conversations/{ids['conversation']}", headers=_csrf(client_b)).status_code == 404

        # Foreign relationship IDs must be rejected before creating links, reviews, or proposals.
        own_task = client_b.post("/api/tasks", headers=_csrf(client_b), json={"title": f"own-task-{suffix}"})
        assert own_task.status_code == 200
        own_conversation = client_b.post("/api/agent/chat/conversations", headers=_csrf(client_b), json={"domain": "stem"})
        assert own_conversation.status_code == 200
        own_card = client_b.post("/api/knowledge", headers=_csrf(client_b), json={"title": f"own-card-{suffix}", "content_markdown": "synthetic private note", "domain": "stem"})
        assert own_card.status_code == 200
        assert client_b.post(f"/api/tasks/{own_task.json()['id']}/links", headers=_csrf(client_b), json={"conversation_id": ids["conversation"], "message_id": ids["message"]}).status_code == 404
        assert client_b.post(f"/api/agent/chat/conversations/{own_conversation.json()['id']}/tasks/{ids['task']}", headers=_csrf(client_b)).status_code == 404
        assert client_b.post(f"/api/knowledge/{ids['card']}/tasks/{own_task.json()['id']}", headers=_csrf(client_b)).status_code == 404
        assert client_b.post(f"/api/knowledge/{own_card.json()['id']}/tasks/{ids['task']}", headers=_csrf(client_b)).status_code == 404
        assert client_b.post(f"/api/agent/chat/messages/{ids['message']}/task-proposal", headers=_csrf(client_b), json={"title": "cross-user-proposal", "description": "x", "module": "stem", "estimated_minutes": 5, "acceptance_criteria": "x"}).status_code == 404
        review_write = client_b.post("/api/reviews/daily", headers=_csrf(client_b), json={"review_date": ids["review_date"], "actual_summary": "attempt", "task_entries": [{"task_id": ids["task"], "report_category": "in_progress"}]})
        assert review_write.status_code == 404
        assert client_b.post(f"/api/study/records/{ids['study']}/errors", headers=_csrf(client_b), json={"category": "grammar", "question": "x", "user_answer": "x", "explanation": "x"}).status_code == 404

        assert client_b.get(f"/api/reviews/daily/{ids['review_date']}").json()["task_entries"] == []
        weekly = client_b.get("/api/reviews/weekly?week_start=2026-10-19")
        assert weekly.status_code == 200 and f"private-review-{suffix}" not in weekly.text
        assert client_b.get("/api/study/records").json() == []
        assert client_b.get("/api/health/workouts").json() == []
        providers = client_b.get("/api/settings/ai/providers")
        assert providers.status_code == 200 and ids["provider_secret"] not in providers.text
        assert client_b.get("/api/data/backups").status_code == 403
        assert client_b.post("/api/data/migration/export", headers=_csrf(client_b), json={"recovery_password": "synthetic-passphrase-2026"}).status_code == 403

        with SessionLocal() as verify:
            assert verify.get(Task, ids["task"]).title == ids["task_title"]
            assert verify.get(KnowledgeCard, ids["card"]).title == ids["card_title"]
            assert verify.get(AgentProposal, ids["proposal"]).status == "PENDING"
            assert verify.query(TaskEvidence).filter_by(task_id=ids["task"]).count() == 0
            assert verify.query(TaskConversationLink).filter_by(user_id=ids["b_id"]).count() == 0
            assert verify.query(KnowledgeTaskLink).filter_by(user_id=ids["b_id"]).count() == 0
            assert verify.query(DailyReview).filter_by(user_id=ids["a_id"], review_date=date(2026, 10, 19)).count() == 1
            assert verify.query(AgentProposal).filter_by(user_id=ids["a_id"]).count() == 1
            assert verify.query(AgentProposal).filter_by(user_id=ids["b_id"]).count() == 0
    finally:
        client_a.close()
        client_b.close()
