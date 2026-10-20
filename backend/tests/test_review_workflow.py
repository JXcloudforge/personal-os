from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import DailyReview, DailyTaskSnapshot, Task, TaskEvidence, TaskStatus, User
from app.services.task_manager import TaskSummary, task_summaries


def setup_module():
    Base.metadata.create_all(bind=engine)


def _user_and_task(*, status=TaskStatus.TODO):
    username = f"review-{uuid4().hex[:12]}"
    password = "isolated-review-test-password"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        task = Task(user_id=user.id, title="Reviewable task", module="general", status=status, priority=2)
        db.add(task)
        db.commit()
        return user.id, task.id, username, password


def _logged_in(username, password):
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return client


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def test_task_evidence_is_owned_and_written_by_task_service():
    owner_id, task_id, username, password = _user_and_task(status=TaskStatus.DOING)
    other_id, _, other_username, other_password = _user_and_task()
    with _logged_in(username, password) as owner, _logged_in(other_username, other_password) as other:
        saved = owner.post(
            f"/api/tasks/{task_id}/evidence",
            headers=_csrf(owner),
            json={"content": "I ran the isolated verification", "verification_level": "E3_TEST"},
        )
        assert saved.status_code == 200
        assert other.post(
            f"/api/tasks/{task_id}/evidence",
            headers=_csrf(other),
            json={"content": "cross-account attempt"},
        ).status_code == 404

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        evidence = db.scalar(select(TaskEvidence).where(TaskEvidence.task_id == task_id))
        assert task is not None and task.user_id == owner_id and task.status == TaskStatus.VERIFY
        assert evidence is not None and evidence.content == "I ran the isolated verification"
        assert db.scalar(select(TaskEvidence.id).where(TaskEvidence.content == "cross-account attempt")) is None
        assert db.get(User, other_id) is not None


def test_weekly_review_summarizes_only_authenticated_users_saved_records():
    owner_id, task_id, username, password = _user_and_task()
    _, _, other_username, other_password = _user_and_task()
    with SessionLocal() as db:
        review = DailyReview(
            user_id=owner_id,
            review_date=date(2026, 10, 12),
            actual_summary="Finished the first review step.",
            blockers="Waiting for a dependency.",
            next_actions="Continue tomorrow.",
        )
        db.add(review)
        db.flush()
        db.add(
            DailyTaskSnapshot(
                user_id=owner_id,
                review_id=review.id,
                task_id=task_id,
                review_date=review.review_date,
                status_at_settlement="TODO",
                report_category="blocked",
                actual_progress="Started the first step.",
                evidence_note="Saved test evidence.",
            )
        )
        db.commit()

    with _logged_in(username, password) as owner:
        response = owner.get("/api/reviews/weekly?week_start=2026-10-12")
        assert response.status_code == 200
        result = response.json()
        assert result["week_start"] == "2026-10-12"
        assert result["week_end"] == "2026-10-18"
        assert result["days_with_reviews"] == 1
        assert result["reported_categories"]["blocked"] == 1
        assert result["evidence_note_count"] == 1
        assert result["days"][0]["task_entries"][0]["task_id"] == task_id
        assert result["days"][0]["actual_summary"] == "Finished the first review step."
        assert "not an independent verification" in result["basis"]
        assert owner.get("/api/reviews/weekly?week_start=2026-10-14").status_code == 422

    with _logged_in(other_username, other_password) as other:
        result = other.get("/api/reviews/weekly?week_start=2026-10-12").json()
        assert result["days_with_reviews"] == 0
        assert result["task_entry_count"] == 0
        assert all(day["task_entries"] == [] for day in result["days"])

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/reviews/weekly").status_code == 401


def test_task_summary_facade_batches_and_omits_foreign_or_missing_tasks():
    owner_id, task_id, _, _ = _user_and_task()
    _, foreign_task_id, _, _ = _user_and_task()
    statements = []

    def record_task_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM tasks" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_task_select)
    try:
        with SessionLocal() as db:
            summaries = task_summaries(db, owner_id, [task_id, foreign_task_id, 999999, task_id])
    finally:
        event.remove(engine, "before_cursor_execute", record_task_select)

    assert len(statements) == 1
    assert set(summaries) == {task_id}
    assert summaries[task_id] == TaskSummary(id=task_id, title="Reviewable task", status="TODO")
    assert not isinstance(summaries[task_id], Task)
    with SessionLocal() as db:
        assert task_summaries(db, owner_id, []) == {}


def test_daily_review_self_report_does_not_create_formal_task_evidence():
    _, task_id, username, password = _user_and_task()
    with _logged_in(username, password) as client:
        response = client.post(
            "/api/reviews/daily",
            headers=_csrf(client),
            json={
                "review_date": "2026-10-19",
                "actual_summary": "I made progress.",
                "task_entries": [{
                    "task_id": task_id,
                    "report_category": "in_progress",
                    "actual_progress": "Self reported progress.",
                    "evidence_note": "Self reported evidence note.",
                }],
            },
        )
        assert response.status_code == 200
        assert response.json()["task_entries"][0]["evidence_note"] == "Self reported evidence note."

    with SessionLocal() as db:
        assert db.scalar(select(TaskEvidence.id).where(TaskEvidence.task_id == task_id)) is None
        snapshot = db.scalar(select(DailyTaskSnapshot).where(DailyTaskSnapshot.task_id == task_id))
        assert snapshot is not None and snapshot.evidence_note == "Self reported evidence note."


def test_weekly_review_without_records_returns_seven_empty_days():
    _, _, username, password = _user_and_task()
    with _logged_in(username, password) as client:
        response = client.get("/api/reviews/weekly?week_start=2026-10-19")
    assert response.status_code == 200
    result = response.json()
    assert result["days_with_reviews"] == 0
    assert result["task_entry_count"] == 0
    assert len(result["days"]) == 7
    assert all(not day["reviewed"] and day["task_entries"] == [] for day in result["days"])
