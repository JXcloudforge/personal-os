from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DailyReview, DailyTaskSnapshot
from .task_manager import task_summaries


def weekly_summary(db: Session, user_id: int, week_start: date) -> dict:
    """Return a read-only weekly view of this user's saved daily reviews."""
    if week_start.weekday() != 0:
        raise ValueError("WEEK_START_MUST_BE_MONDAY")

    week_end = week_start + timedelta(days=6)
    days = {
        (week_start + timedelta(days=offset)).isoformat(): {
            "review_date": (week_start + timedelta(days=offset)).isoformat(),
            "reviewed": False,
            "actual_summary": None,
            "blockers": None,
            "next_actions": None,
            "task_entries": [],
        }
        for offset in range(7)
    }

    reviews = db.scalars(
        select(DailyReview)
        .where(
            DailyReview.user_id == user_id,
            DailyReview.review_date >= week_start,
            DailyReview.review_date <= week_end,
        )
        .order_by(DailyReview.review_date)
    ).all()
    for review in reviews:
        day = days[review.review_date.isoformat()]
        day.update(
            reviewed=True,
            actual_summary=review.actual_summary,
            blockers=review.blockers,
            next_actions=review.next_actions,
        )

    snapshots = db.scalars(
        select(DailyTaskSnapshot)
        .join(DailyReview, DailyReview.id == DailyTaskSnapshot.review_id)
        .where(
            DailyReview.user_id == user_id,
            DailyTaskSnapshot.user_id == user_id,
            DailyTaskSnapshot.review_date >= week_start,
            DailyTaskSnapshot.review_date <= week_end,
        )
        .order_by(DailyTaskSnapshot.review_date, DailyTaskSnapshot.id)
    ).all()
    summaries = task_summaries(db, user_id, [snapshot.task_id for snapshot in snapshots])
    categories = {"completed_today": 0, "in_progress": 0, "blocked": 0, "overdue": 0}
    evidence_note_count = 0
    visible_snapshots = 0
    for snapshot in snapshots:
        task = summaries.get(snapshot.task_id)
        if task is None:
            continue
        visible_snapshots += 1
        category = snapshot.report_category
        if category in categories:
            categories[category] += 1
        if snapshot.evidence_note and snapshot.evidence_note.strip():
            evidence_note_count += 1
        days[snapshot.review_date.isoformat()]["task_entries"].append(
            {
                "task_id": task.id,
                "task_title": task.title,
                "status_at_settlement": snapshot.status_at_settlement,
                "report_category": category,
                "actual_progress": snapshot.actual_progress,
                "evidence_note": snapshot.evidence_note,
            }
        )

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "days_with_reviews": len(reviews),
        "task_entry_count": visible_snapshots,
        "reported_categories": categories,
        "evidence_note_count": evidence_note_count,
        "days": list(days.values()),
        "basis": "User-entered daily reviews and task snapshots; not an independent verification of task completion.",
    }
