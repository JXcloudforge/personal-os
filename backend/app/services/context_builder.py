from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import DailyReview, StudyRecord, Task, TaskStatus, UserGoal


AGENT_SCOPE = "goals,active_tasks,recent_reviews,study_records,study_errors"
SENSITIVE_DOMAINS = ["health", "finance", "financial", "健康", "财务"]


def current_goals(db: Session, user_id: int) -> list[dict]:
    goals = db.scalars(select(UserGoal).where(UserGoal.user_id == user_id, UserGoal.enabled.is_(True), UserGoal.domain.notin_(SENSITIVE_DOMAINS)).order_by(UserGoal.priority, UserGoal.domain)).all()
    return [{"id": goal.id, "domain": goal.domain, "title": goal.title, "priority": goal.priority, "notes": goal.notes} for goal in goals]


def active_tasks(db: Session, user_id: int) -> list[dict]:
    tasks = db.scalars(select(Task).where(Task.user_id == user_id, Task.module.notin_(SENSITIVE_DOMAINS), Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED])).options(selectinload(Task.evidence)).order_by(Task.priority, Task.updated_at.desc()).limit(30)).all()
    return [{"id": task.id, "title": task.title, "module": task.module, "status": task.status.value, "priority": task.priority, "acceptance_criteria": task.acceptance_criteria, "evidence_count": len(task.evidence)} for task in tasks]


def recent_reviews(db: Session, user_id: int) -> list[dict]:
    reviews = db.scalars(select(DailyReview).where(DailyReview.user_id == user_id).order_by(DailyReview.review_date.desc()).limit(5)).all()
    return [{"date": review.review_date.isoformat(), "planned": review.planned_summary, "actual": review.actual_summary, "blockers": review.blockers, "next_actions": review.next_actions} for review in reviews]


def study_feedback(db: Session, user_id: int) -> dict:
    records = db.scalars(select(StudyRecord).where(StudyRecord.user_id == user_id).options(selectinload(StudyRecord.errors)).order_by(StudyRecord.created_at.desc()).limit(8)).all()
    return {
        "records": [{"id": record.id, "language": record.language, "topic": record.topic, "activity_type": record.activity_type, "assessment_status": record.assessment_status, "actual_response_present": bool(record.actual_response)} for record in records],
        "errors": [{"record_id": record.id, "language": record.language, "category": error.category, "question": error.question, "explanation": error.explanation} for record in records for error in record.errors[-10:]],
    }


def build_context(db: Session, user_id: int, available_minutes: int, priority_note: str | None = None) -> dict:
    return {
        "as_of": date.today().isoformat(),
        "available_minutes": available_minutes,
        "priority_note": priority_note,
        "goals": current_goals(db, user_id),
        "active_tasks": active_tasks(db, user_id),
        "recent_reviews": recent_reviews(db, user_id),
        "study_feedback": study_feedback(db, user_id),
        "health": "NOT_INCLUDED_UNAUTHORIZED",
        "finance": "NOT_INCLUDED_UNAUTHORIZED",
        "unknowns": ["语言水平只有在存在独立证据时才可判断", "健康与财务未授权给通用 Agent"],
    }
