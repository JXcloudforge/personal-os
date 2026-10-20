from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from pydantic import ValidationError

from ..models import AgentAuditEvent, AgentProposal, DailyReview, User
from ..schemas.agent import PlanItem
from .agent_audit import audit_event
from .task_manager import TaskItemInput, create_proposal_tasks, local_today


def _validate_items(items: list[dict]) -> list[dict]:
    if not 1 <= len(items) <= 3:
        raise ValueError("A proposal must contain one to three items")
    try:
        normalized = [PlanItem.model_validate(item).model_dump() for item in items]
    except ValidationError as exc:
        raise ValueError("Proposal item does not match the required schema") from exc
    titles = [item["title"].casefold() for item in normalized]
    if len(set(titles)) != len(titles):
        raise ValueError("Proposal contains duplicate task titles")
    return normalized


def proposal_view(proposal: AgentProposal) -> dict:
    purged = proposal.purged_at is not None
    return {
        "id": proposal.id,
        "proposal_key": proposal.proposal_key,
        "proposal_type": proposal.proposal_type,
        "plan_date": proposal.plan_date,
        "status": proposal.status,
        "title": "[内容已永久清除]" if purged else proposal.title,
        "items": [] if purged else (proposal.items or []),
        "rationale": None if purged else proposal.rationale,
        "decision_note": None if purged else proposal.decision_note,
        "deleted": proposal.deleted_at is not None,
        "purged": purged,
        "run_id": proposal.run_id,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "decided_at": proposal.decided_at,
    }


def list_proposals(db: Session, user_id: int, *, include_deleted: bool = False, status: str | None = None, query: str | None = None) -> list[AgentProposal]:
    conditions = [AgentProposal.user_id == user_id]
    conditions.append(AgentProposal.deleted_at.is_not(None) if include_deleted else AgentProposal.deleted_at.is_(None))
    if status:
        conditions.append(AgentProposal.status == status)
    if query:
        conditions.append(AgentProposal.title.ilike(f"%{query[:100]}%"))
    return list(db.scalars(select(AgentProposal).where(*conditions).order_by(AgentProposal.updated_at.desc()).limit(200)).all())


def get_owned_proposal(db: Session, user_id: int, proposal_id: int) -> AgentProposal | None:
    proposal = db.get(AgentProposal, proposal_id)
    return proposal if proposal and proposal.user_id == user_id else None


def save_generated_proposal(
    db: Session,
    user_id: int,
    *,
    run_id: int,
    proposal_key: str,
    proposal_type: str,
    plan_date: date,
    title: str,
    items: list[dict],
    rationale: str,
) -> AgentProposal:
    normalized = items if proposal_type == "evening_review" else _validate_items(items)
    proposal = db.scalar(select(AgentProposal).where(AgentProposal.user_id == user_id, AgentProposal.proposal_key == proposal_key))
    if proposal and proposal.purged_at is not None:
        # A new generation must never repopulate a permanently purged record.
        proposal_key = f"{proposal_key}:{run_id}:{uuid4().hex[:8]}"
        proposal = db.scalar(select(AgentProposal).where(AgentProposal.user_id == user_id, AgentProposal.proposal_key == proposal_key))
    if proposal and proposal.status in {"PENDING", "APPROVED"}:
        return proposal
    if proposal:
        proposal.run_id = run_id
        proposal.proposal_type = proposal_type
        proposal.plan_date = plan_date
        proposal.title = title
        proposal.items = normalized
        proposal.rationale = rationale
        proposal.status = "PENDING"
        proposal.deleted_at = None
        proposal.decision_note = None
        proposal.decided_at = None
    else:
        proposal = AgentProposal(
            user_id=user_id,
            run_id=run_id,
            proposal_key=proposal_key,
            proposal_type=proposal_type,
            plan_date=plan_date,
            status="PENDING",
            title=title,
            items=normalized,
            rationale=rationale,
        )
        db.add(proposal)
        db.flush()
    audit_event(db, user_id, "proposal_created", {"proposal_type": proposal_type, "item_count": len(normalized)}, run_id=run_id, proposal_id=proposal.id)
    db.commit()
    db.refresh(proposal)
    return proposal


def _create_tasks_from_proposal(db: Session, user: User, proposal: AgentProposal) -> tuple[list[int], list[int]]:
    from . import knowledge_service

    task_items: list[TaskItemInput] = []
    source_refs: list[tuple[str | None, int | None]] = []
    card_ids_by_item: list[list[int]] = []
    knowledge_proposal = any(item.get("knowledge_card_ids") for item in (proposal.items or []))
    today = local_today()
    for item in proposal.items or []:
        card_ids = [int(card_id) for card_id in item.get("knowledge_card_ids", [])]
        if knowledge_proposal:
            if not card_ids:
                raise ValueError("Knowledge task proposals require card references on every item")
            knowledge_service.get_active_cards(db, user.id, card_ids)
        description = (
            f"{item['action']}\n\n预计时间：{item['estimated_minutes']} 分钟\n\n"
            f"{'为什么：' if knowledge_proposal else '为什么今天：'}{item['why_today']}\n\n"
            f"所需证据：{item['evidence_required']}"
        )
        if not knowledge_proposal:
            description += "\n\n来源：" + "\n".join(item.get("sources", []))
        task_items.append(TaskItemInput(
            title=item["title"], description=description, module=item["domain"], priority=2,
            estimated_minutes=item.get("estimated_minutes"),
            start_date=None if knowledge_proposal else today,
            due_date=None if knowledge_proposal else today,
            expected_duration_days=None if knowledge_proposal else 1,
            acceptance_criteria=item["acceptance_criteria"],
        ))
        source_refs.append((item.get("source_conversation_id"), item.get("source_message_id")))
        card_ids_by_item.append(card_ids if knowledge_proposal else [])
    return create_proposal_tasks(
        db, user.id, proposal.run_id, proposal.id, task_items,
        source_refs=source_refs, knowledge_card_ids=card_ids_by_item,
    )


def apply_proposal_action(
    db: Session,
    user: User,
    proposal: AgentProposal,
    *,
    action: str,
    items: list[dict] | None,
    note: str | None,
) -> tuple[AgentProposal, list[int], list[int]]:
    if proposal.user_id != user.id:
        raise ValueError("Proposal not found")
    if action == "edit":
        if proposal.status != "PENDING":
            raise ValueError("Only pending proposals can be edited")
        if items is None:
            raise ValueError("Edited items are required")
        proposal.items = _validate_items(items)
        proposal.decision_note = note
        audit_event(db, user.id, "proposal_edited", {"item_count": len(proposal.items)}, proposal_id=proposal.id, actor="user")
        db.commit()
        db.refresh(proposal)
        return proposal, [], []
    if action == "reject":
        if proposal.status == "APPROVED":
            raise ValueError("Approved proposals cannot be rejected")
        if proposal.status != "REJECTED":
            proposal.status = "REJECTED"
            proposal.decision_note = note
            proposal.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
            audit_event(db, user.id, "proposal_rejected", {"note": note}, proposal_id=proposal.id, actor="user")
            db.commit()
        return proposal, [], []
    if action == "approve":
        if proposal.status == "APPROVED":
            return proposal, [], []
        if proposal.status != "PENDING":
            raise ValueError("Only pending proposals can be approved")
        # Claim the pending row atomically. A concurrent approval cannot create
        # a second set of tasks; the claim and task writes share one transaction.
        claimed = db.execute(update(AgentProposal).where(AgentProposal.id == proposal.id, AgentProposal.user_id == user.id, AgentProposal.status == "PENDING").values(status="APPROVING").execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            db.rollback()
            db.refresh(proposal)
            if proposal.status == "APPROVED":
                return proposal, [], []
            raise ValueError("Proposal is already being decided; refresh before retrying")
        try:
            if items is not None and proposal.proposal_type != "evening_review":
                proposal.items = _validate_items(items)
                audit_event(db, user.id, "proposal_edited", {"item_count": len(proposal.items)}, proposal_id=proposal.id, actor="user")
            if proposal.proposal_type == "evening_review":
                save_evening_review(db, user.id, proposal)
                created, skipped = [], []
            else:
                created, skipped = _create_tasks_from_proposal(db, user, proposal)
            proposal.status = "APPROVED"
            proposal.decision_note = note
            proposal.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
            audit_event(db, user.id, "proposal_approved", {"created_task_ids": created, "skipped_task_ids": skipped}, proposal_id=proposal.id, actor="user")
            db.commit()
            db.refresh(proposal)
            return proposal, created, skipped
        except Exception:
            db.rollback()
            raise
    raise ValueError("Unsupported proposal action")


def save_evening_review(db: Session, user_id: int, proposal: AgentProposal) -> DailyReview:
    item = (proposal.items or [{}])[0]
    review = db.scalar(select(DailyReview).where(DailyReview.user_id == user_id, DailyReview.review_date == proposal.plan_date))
    values = {
        "planned_summary": item.get("planned_summary"),
        "actual_summary": item.get("actual_summary"),
        "blockers": item.get("blockers"),
        "next_actions": item.get("next_actions"),
    }
    if not review:
        review = DailyReview(user_id=user_id, review_date=proposal.plan_date, **values)
        db.add(review)
    else:
        for key, value in values.items():
            setattr(review, key, value)
    db.flush()
    return review
