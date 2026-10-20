from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import AgentAuditEvent, AgentToolCall, ChatConversation, ChatMessage, DailyReview, DailyTaskSnapshot, Task, TaskConversationLink, TaskDateHistory, TaskEvidence, TaskManagerOperation, TaskPurgeReceipt, TaskStatus, TaskStatusHistory

ALLOWED_DOMAINS = {"general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "ai_engineering", "system"}


def allocate_task_id(db: Session) -> int:
    """Keep task IDs monotonic after purge receipts outlive the Task row."""
    max_task_id = db.scalar(select(func.max(Task.id))) or 0
    max_purged_id = db.scalar(select(func.max(TaskPurgeReceipt.task_id))) or 0
    return max(max_task_id, max_purged_id) + 1


def infer_task_module(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    if re.search(r"网站|产品|客户|项目|维护|部署", value):
        return "projects"
    if re.search(r"英语|德语|单词|听力|口语|词汇|语言", value):
        return "bilingual"
    if re.search(r"健康|训练|饮食|睡眠|体重", value):
        return "health"
    if re.search(r"数学|物理|电工|电子|电路|自动化", value):
        return "stem"
    if re.search(r"python|agent|软件|编程|代码|算法|人工智能|\bai\b", value):
        return "ai_engineering"
    if re.search(r"财务|收支|预算|现金", value):
        return "finance"
    return "general"


class TaskItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    module: str = Field(default="general", min_length=1, max_length=40, json_schema_extra={"enum": sorted(ALLOWED_DOMAINS)})
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    start_date: date | None = None
    due_date: date | None = None
    expected_duration_days: int | None = Field(default=None, ge=1, le=3650)
    acceptance_criteria: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def infer_missing_module(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("module") in (None, ""):
            data["module"] = infer_task_module(" ".join(str(data.get(key) or "") for key in ("title", "description")))
        return data

    @field_validator("module", mode="before")
    @classmethod
    def normalize_module_alias(cls, value):
        if not isinstance(value, str):
            return value
        normalized = unicodedata.normalize("NFKC", value).strip().casefold().replace("-", "_")
        aliases = {"website": "projects", "web": "projects", "software_engineering": "ai_engineering", "software": "ai_engineering", "programming": "ai_engineering", "language": "bilingual", "languages": "bilingual", "english": "bilingual"}
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def validate_module_dates(self):
        if self.module not in ALLOWED_DOMAINS:
            raise ValueError("Unknown task domain")
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date cannot precede start_date")
        if self.start_date and self.due_date and self.expected_duration_days is not None:
            if (self.due_date - self.start_date).days + 1 != self.expected_duration_days:
                raise ValueError("duration must match inclusive date range")
        return self


class ManualTaskInput(BaseModel):
    """Service-level input contract for the authenticated manual Task API."""

    task_code: str | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    module: str = "system"
    status: TaskStatus = TaskStatus.TODO
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    planned_due: datetime | None = None
    start_date: date | None = None
    due_date: date | None = None
    expected_duration_days: int | None = Field(default=None, ge=1, le=3650)
    acceptance_criteria: str | None = None

    @model_validator(mode="after")
    def validate_date_bounds(self):
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date cannot be earlier than start_date")
        if self.start_date and self.due_date and self.expected_duration_days is not None:
            if (self.due_date - self.start_date).days + 1 != self.expected_duration_days:
                raise ValueError("expected_duration_days must match the inclusive date range")
        return self


class CreateTasksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TaskItemInput] = Field(min_length=1, max_length=10)


class UpdateTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int = Field(ge=1)
    match_title: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    module: str | None = Field(default=None, max_length=40)
    priority: int | None = Field(default=None, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    start_date: date | None = None
    due_date: date | None = None
    expected_duration_days: int | None = Field(default=None, ge=1, le=3650)
    acceptance_criteria: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_update(self):
        if not any(getattr(self, key) is not None for key in self.model_fields_set if key not in {"task_id", "match_title"}):
            raise ValueError("At least one editable field is required")
        if "expected_duration_days" in self.model_fields_set and self.expected_duration_days is None:
            raise ValueError("Expected duration must be a positive integer")
        if self.module is not None and self.module not in ALLOWED_DOMAINS:
            raise ValueError("Unknown task domain")
        return self


class TaskDetailsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int = Field(ge=1)


class DeleteTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int = Field(ge=1)
    match_title: str = Field(min_length=1, max_length=200)


def local_today() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(settings.app_timezone)).date()


def _resolve_task_dates(start_date: date | None, due_date: date | None, duration_days: int | None, *, preserve_undated: bool = False) -> tuple[date | None, date | None, int | None]:
    if preserve_undated and start_date is None and due_date is None and duration_days is None:
        return None, None, None
    start = start_date or due_date or local_today()
    duration = duration_days or ((due_date - start).days + 1 if due_date else 1)
    due = due_date or start + timedelta(days=duration - 1)
    if due < start or (due - start).days + 1 != duration:
        raise ValueError("TASK_DATE_RANGE_INVALID")
    return start, due, duration


def _persist_task(
    db: Session,
    user_id: int,
    *,
    title: str,
    description: str | None,
    module: str,
    status: TaskStatus,
    priority: int,
    estimated_minutes: int | None,
    start_date: date | None,
    due_date: date | None,
    expected_duration_days: int | None,
    acceptance_criteria: str | None,
    task_code: str | None = None,
    planned_due: datetime | None = None,
    history_reason: str,
) -> Task:
    task = Task(
        id=allocate_task_id(db), user_id=user_id, task_code=task_code, title=title.strip(),
        description=description, module=module, status=status, priority=priority,
        estimated_minutes=estimated_minutes, start_date=start_date, due_date=due_date,
        expected_duration_days=expected_duration_days, acceptance_criteria=acceptance_criteria,
        planned_due=planned_due,
    )
    db.add(task)
    db.flush()
    db.add(TaskStatusHistory(task_id=task.id, to_status=status.value, reason=history_reason))
    db.flush()
    return task


def create_manual_task(db: Session, user_id: int, payload: ManualTaskInput, *, idempotency_key: str | None = None) -> Task:
    """Create a manually submitted task and persist an optional replay key atomically."""
    if payload.status == TaskStatus.DONE:
        raise ValueError("A new task cannot start as DONE; add verifiable evidence and submit it for verification first")
    operation_id = idempotency_key or str(uuid4())
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def replay(operation: TaskManagerOperation | None) -> Task | None:
        if operation is None:
            return None
        if operation.mode != "manual_create" or operation.request_fingerprint != fingerprint:
            raise ValueError("TASK_IDEMPOTENCY_KEY_CONFLICT")
        task_id = next((int(item["task_id"]) for item in (operation.action_details or []) if item.get("kind") == "create" and item.get("task_id") is not None), None)
        task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id)) if task_id else None
        if task is None:
            raise ValueError("TASK_IDEMPOTENCY_RESULT_UNAVAILABLE")
        return task

    existing = db.scalar(select(TaskManagerOperation).where(
        TaskManagerOperation.user_id == user_id,
        TaskManagerOperation.client_operation_id == operation_id,
    ))
    prior = replay(existing)
    if prior is not None:
        return prior

    operation = TaskManagerOperation(
        user_id=user_id, client_operation_id=operation_id, status="started", mode="manual_create",
        request_fingerprint=fingerprint, action_details=[],
    )
    db.add(operation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        prior = replay(db.scalar(select(TaskManagerOperation).where(
            TaskManagerOperation.user_id == user_id,
            TaskManagerOperation.client_operation_id == operation_id,
        )))
        if prior is not None:
            return prior
        raise

    start, due, duration = _resolve_task_dates(payload.start_date, payload.due_date, payload.expected_duration_days)
    task = _persist_task(
        db, user_id, title=payload.title, description=payload.description, module=payload.module,
        status=payload.status, priority=payload.priority, estimated_minutes=payload.estimated_minutes,
        start_date=start, due_date=due, expected_duration_days=duration,
        acceptance_criteria=payload.acceptance_criteria, task_code=payload.task_code,
        planned_due=payload.planned_due, history_reason="created",
    )
    operation.action_details = [{"kind": "create", "task_id": task.id}]
    operation.status = "applied"
    db.commit()
    db.refresh(task)
    return task


def knowledge_task_reference(db: Session, user_id: int, task_id: int) -> dict:
    """Return a stable, read-only DTO for Knowledge without exposing Task ORM."""
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id, Task.deleted_at.is_(None)))
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    return {"id": task.id, "title": task.title, "domain": task.module, "status": task.status.value, "archived": task.archived_at is not None}


def dashboard_task_rows(db: Session, user_id: int, today: date) -> dict:
    """Single source for dashboard task buckets; serialization stays in the API layer."""
    active = (Task.user_id == user_id, Task.deleted_at.is_(None), Task.archived_at.is_(None),
              Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]))
    counts = {status.value: db.scalar(select(func.count(Task.id)).where(
        Task.user_id == user_id, Task.status == status, Task.deleted_at.is_(None), Task.archived_at.is_(None))) for status in TaskStatus}
    today_rows = db.scalars(select(Task).where(*active, Task.start_date.is_not(None), Task.due_date.is_not(None),
        Task.start_date <= today, Task.due_date >= today).options(selectinload(Task.evidence)).order_by(
        Task.start_date, Task.due_date, Task.priority, Task.id)).all()
    overdue_rows = db.scalars(select(Task).where(*active, Task.due_date.is_not(None), Task.due_date < today).options(
        selectinload(Task.evidence)).order_by(Task.due_date, Task.priority, Task.id).limit(20)).all()
    unscheduled_filter = or_(Task.start_date.is_(None), Task.due_date.is_(None))
    unscheduled_rows = db.scalars(select(Task).where(*active, unscheduled_filter).options(selectinload(Task.evidence)).order_by(
        Task.priority, Task.updated_at.desc()).limit(20)).all()
    unscheduled_count = db.scalar(select(func.count(Task.id)).where(*active, unscheduled_filter))
    completed_rows = db.scalars(select(Task).where(Task.user_id == user_id, Task.deleted_at.is_(None),
        Task.archived_at.is_(None), Task.status == TaskStatus.DONE, Task.completed_at.is_not(None)).options(
        selectinload(Task.evidence)).order_by(Task.completed_at.desc()).limit(100)).all()
    completed_today = [task for task in completed_rows if task.completed_at and task.completed_at.date() == today]
    return {"counts": counts, "today": today_rows, "completed_today": completed_today,
            "overdue": overdue_rows, "overdue_count": db.scalar(select(func.count(Task.id)).where(
                *active, Task.due_date.is_not(None), Task.due_date < today)),
            "unscheduled": unscheduled_rows, "unscheduled_count": unscheduled_count}


def get_conversation_tasks(db: Session, user_id: int, conversation_id: str) -> list[dict]:
    conversation = db.scalar(select(ChatConversation).where(ChatConversation.id == conversation_id,
        ChatConversation.user_id == user_id, ChatConversation.deleted_at.is_(None)))
    if conversation is None:
        raise ValueError("CONVERSATION_NOT_AVAILABLE")
    rows = db.scalars(select(Task).join(TaskConversationLink, TaskConversationLink.task_id == Task.id).where(
        TaskConversationLink.conversation_id == conversation.id, TaskConversationLink.user_id == user_id,
        Task.user_id == user_id, Task.deleted_at.is_(None), Task.module.notin_({"health", "finance"})).distinct().order_by(
            Task.is_pinned.desc(), Task.updated_at.desc())).all()
    return [{"id": task.id, "title": task.title, "status": task.status.value, "priority": task.priority,
             "archived": task.archived_at is not None,
             "next_action": (task.description or task.acceptance_criteria or "尚未填写下一步")[:500]} for task in rows]


def explicit_task_write_intent(message: str) -> bool:
    text = unicodedata.normalize("NFKC", message).strip().casefold()
    if re.search(r"(建议|分析|讨论|怎么|如何).{0,12}(任务|安排|计划)|不要|别(\s|.{0,5})(创建|添加|安排|修改)", text):
        return False
    return bool(re.search(r"(直接.{0,8})?(安排进任务|安排.{0,10}(?:进|到)任务|加入任务|添加(?:到)?任务|创建(?:正式)?任务|(?:帮我|请)?(?:新增|添加|创建).{0,16}(?:任务|进任务|到任务)|记(?:下来|一下).{0,10}(?:任务|这些|这个)|记到任务|记入任务|写入任务|帮我安排|安排一下|延期到|改到|修改任务|删掉任务|删除任务|移除任务|改成.{0,12}(分钟|领域|日期)|归类到)", text))


def is_task_confirmation(message: str) -> bool:
    text = unicodedata.normalize("NFKC", message).strip().casefold()
    return bool(re.fullmatch(r"(?:确认|确认创建|确认安排|创建吧|安排吧|好的?[,， ]*(?:确认|创建吧|安排吧)|就这样)[。！! ]*", text))


def should_prepare_task_candidates(message: str) -> bool:
    text = unicodedata.normalize("NFKC", message).strip().casefold()
    if explicit_task_write_intent(text) or re.search(r"建议|分析|讨论|怎么|如何|是否|要不要", text):
        return False
    # A task-like instruction/list is converted to a durable preview, not a formal write.
    activity = r"(?:学习|练习|复习|背|整理|研究|完成|做好|开发|制作|阅读|跑|测试|准备|第[一二三四五六七八九十0-9]+项|第二个任务)"
    return bool(re.search(activity, text) and len(text) >= 8)


def task_manager_required_tools(message: str, mode: str = "auto") -> list[str]:
    """Pick a bounded native tool-call path from the user's own instruction."""
    text = unicodedata.normalize("NFKC", message).strip().casefold()
    if mode == "plan":
        return ["get_active_tasks"]
    if mode == "create":
        return ["get_active_tasks"] if re.search(r"(删除|删掉|移除).{0,20}任务", text) else ["get_active_tasks", "create_tasks"]
    if re.search(r"(删除|删掉|移除).{0,20}任务", text):
        return ["get_active_tasks", "request_delete_task"]
    if mode == "modify":
        return ["get_active_tasks", "update_task"]
    if re.search(r"(修改|改成|延期|改到|归类到|归类).{0,60}(任务|分钟|日期|领域|下周|周[一二三四五六日天])", text):
        return ["get_active_tasks", "update_task"]
    if explicit_task_write_intent(text):
        return ["get_active_tasks", "create_tasks"]
    if should_prepare_task_candidates(text):
        return ["get_active_tasks", "prepare_tasks"]
    return []


def requested_task_count(message: str) -> int | None:
    text = unicodedata.normalize("NFKC", message)
    numeral = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    match = re.search(r"(?:创建|新增|添加|安排).{0,12}(一|两|二|三|四|五|六|七|八|九|十|\d{1,2})\s*(?:项|个|条)\s*任务", text)
    if match:
        raw = match.group(1)
        value = int(raw) if raw.isdigit() else numeral[raw]
        return value if 1 <= value <= 10 else None
    numbered = re.findall(r"(?:^|[：:\n])\s*(\d{1,2})[.、)]\s*", text)
    if len(numbered) >= 2 and [int(item) for item in numbered[:len(numbered)]] == list(range(1, len(numbered) + 1)):
        return len(numbered) if len(numbered) <= 10 else None
    return None


def _owned_task(db: Session, user_id: int, task_id: int) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id, Task.deleted_at.is_(None)))
    if task is None:
        raise ValueError("TASK_NOT_FOUND")
    return task


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """Small read-only task contract for other business services."""

    id: int
    title: str
    status: str


def task_summaries(db: Session, user_id: int, task_ids: list[int]) -> dict[int, TaskSummary]:
    """Read owned task summaries in one query; absent and foreign IDs are omitted."""
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(Task.id, Task.title, Task.status).where(Task.user_id == user_id, Task.id.in_(ids))
    ).all()
    return {row.id: TaskSummary(id=row.id, title=row.title, status=row.status.value) for row in rows}


def add_task_evidence(
    db: Session,
    user_id: int,
    task_id: int,
    *,
    content: str,
    evidence_type: str = "self_report",
    verification_level: str = "E1_SELF_REPORTED",
    source: str | None = None,
) -> TaskEvidence:
    """Record user evidence for an owned task; the caller owns the transaction."""
    task = _owned_task(db, user_id, task_id)
    evidence = TaskEvidence(
        task_id=task.id,
        content=content,
        evidence_type=evidence_type,
        verification_level=verification_level,
        source=source,
    )
    db.add(evidence)
    if task.status == TaskStatus.DOING:
        task.status = TaskStatus.VERIFY
        db.add(TaskStatusHistory(task_id=task.id, from_status="DOING", to_status="VERIFY", reason="evidence added"))
    db.flush()
    return evidence


def _operation(db: Session, user_id: int, operation_id: str) -> TaskManagerOperation:
    row = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user_id, TaskManagerOperation.client_operation_id == operation_id))
    if row is None or row.status not in {"started", "applied"}:
        raise ValueError("TASK_OPERATION_NOT_AUTHORIZED")
    return row


def prepare_tasks(db: Session, user_id: int, run_id: int, operation_id: str, items: list[TaskItemInput]) -> dict:
    op = _operation(db, user_id, operation_id)
    if op.conversation_id is None:
        raise ValueError("TASK_PREVIEW_REQUIRES_PERSISTENT_CONVERSATION")
    if any(item.get("kind") == "prepared_tasks" for item in (op.action_details or [])):
        raise ValueError("TASK_CANDIDATES_ALREADY_PREPARED")
    keys = [(item.module, unicodedata.normalize("NFKC", item.title).strip().casefold()) for item in items]
    if len(set(keys)) != len(keys):
        raise ValueError("DUPLICATE_TASKS_IN_CANDIDATE_BATCH")
    existing = db.scalars(select(Task).where(Task.user_id == user_id, Task.deleted_at.is_(None))).all()
    existing_titles = {(task.module, unicodedata.normalize("NFKC", task.title).strip().casefold()): task.id for task in existing}
    knowledge_proposal = next((item for item in (op.action_details or []) if item.get("kind") == "knowledge_task_proposal"), None)
    candidates = [item.model_dump(mode="json") for item in items]
    if knowledge_proposal:
        # Model-chosen dates are not user authorization. Dates can be added in the editable preview.
        for candidate in candidates:
            candidate["start_date"] = None
            candidate["due_date"] = None
            candidate["expected_duration_days"] = None
    likely_duplicates = [{"task_id": existing_titles[key], "title": candidates[index]["title"]}
                         for index, key in enumerate(keys) if key in existing_titles]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    op.action_details = [*op.action_details, {"kind": "prepared_tasks", "items": candidates, "likely_duplicates": likely_duplicates}]
    op.status = "awaiting_user_confirmation"
    op.run_id = run_id
    op.expires_at = now + timedelta(hours=24)
    db.add(AgentAuditEvent(user_id=user_id, run_id=run_id, actor="agent", event_type="task_candidates_prepared", detail=f"count={len(candidates)}"))
    db.flush()
    return {"operation_id": operation_id, "status": op.status, "expires_at": op.expires_at.isoformat(),
            "items": candidates, "likely_duplicates": likely_duplicates, "formal_tasks_created": False}


def _add_audit(db: Session, user_id: int, run_id: int | None, kind: str, task_ids: list[int]):
    db.add(AgentAuditEvent(user_id=user_id, run_id=run_id, actor="agent", event_type=kind, detail=f"task_ids={','.join(map(str, task_ids))}"[:500]))


def create_tasks(db: Session, user_id: int, run_id: int | None, operation_id: str, items: list[TaskItemInput]) -> dict:
    op = _operation(db, user_id, operation_id)
    existing = db.scalars(select(Task).where(Task.user_id == user_id, Task.deleted_at.is_(None))).all()
    existing_titles = {(task.module, unicodedata.normalize("NFKC", task.title).strip().casefold()): task.id for task in existing}
    if not op.action_details:
        op.action_details = []
    created = []
    duplicates = []
    with db.begin_nested():
        for item in items:
            key = (item.module, unicodedata.normalize("NFKC", item.title).strip().casefold())
            if key in existing_titles:
                duplicate_id = existing_titles[key]
                duplicates.append({"task_id": duplicate_id, "title": item.title})
                op.action_details = [*op.action_details, {"kind": "duplicate", "task_id": duplicate_id}]
                continue
            knowledge_proposal = any(action.get("kind") == "knowledge_task_proposal" for action in (op.action_details or []))
            start, due, duration = _resolve_task_dates(
                item.start_date, item.due_date, item.expected_duration_days,
                preserve_undated=knowledge_proposal,
            )
            task = _persist_task(
                db, user_id, title=item.title, description=item.description, module=item.module,
                status=TaskStatus.TODO, priority=item.priority, estimated_minutes=item.estimated_minutes,
                start_date=start, due_date=due, expected_duration_days=duration,
                acceptance_criteria=item.acceptance_criteria, history_reason="created_by_task_manager",
            )
            if op.conversation_id:
                db.add(TaskConversationLink(user_id=user_id, task_id=task.id, conversation_id=op.conversation_id, message_id=op.user_message_id))
            op.action_details = [*op.action_details, {"kind": "create", "task_id": task.id}]
            created.append({"id": task.id, "title": task.title, "module": task.module, "status": task.status.value, "start_date": str(start), "due_date": str(due), "estimated_minutes": task.estimated_minutes})
            existing_titles[key] = task.id
        if created:
            op.status = "applied"
            op.run_id = run_id
            op.expires_at = None
            _add_audit(db, user_id, run_id, "task_manager_created", [item["id"] for item in created])
        db.flush()
    return {"created": created, "duplicates": duplicates, "operation_id": operation_id}


def link_task_source(db: Session, user_id: int, task_id: int, conversation_id: str, message_id: int | None) -> bool:
    """Link a task to an owned conversation/message; return whether a row was added."""
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id, Task.deleted_at.is_(None)))
    conversation = db.scalar(select(ChatConversation).where(
        ChatConversation.id == conversation_id, ChatConversation.user_id == user_id,
        ChatConversation.deleted_at.is_(None),
    ))
    if task is None or conversation is None:
        raise ValueError("TASK_SOURCE_NOT_AVAILABLE")
    if message_id is not None:
        message = db.scalar(select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.user_id == user_id,
            ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant",
        ))
        if message is None:
            raise ValueError("TASK_SOURCE_NOT_AVAILABLE")
    existing = db.scalar(select(TaskConversationLink).where(
        TaskConversationLink.user_id == user_id, TaskConversationLink.task_id == task_id,
        TaskConversationLink.conversation_id == conversation_id,
        TaskConversationLink.message_id == message_id,
    ))
    if existing:
        return False
    db.add(TaskConversationLink(user_id=user_id, task_id=task_id, conversation_id=conversation_id, message_id=message_id))
    db.flush()
    return True


def create_proposal_tasks(
    db: Session,
    user_id: int,
    run_id: int | None,
    proposal_id: int,
    items: list[TaskItemInput],
    *,
    source_refs: list[tuple[str | None, int | None]],
    knowledge_card_ids: list[list[int]],
) -> tuple[list[int], list[int]]:
    """Create approved proposal tasks through the Task Service's idempotent path."""
    if len(items) != len(source_refs) or len(items) != len(knowledge_card_ids):
        raise ValueError("TASK_PROPOSAL_INPUT_MISMATCH")
    created_ids: list[int] = []
    skipped_ids: list[int] = []
    for index, (item, source, card_ids) in enumerate(zip(items, source_refs, knowledge_card_ids), start=1):
        operation_id = str(uuid5(NAMESPACE_URL, f"personal-os:proposal:{proposal_id}:item:{index}"))
        operation = db.scalar(select(TaskManagerOperation).where(
            TaskManagerOperation.user_id == user_id,
            TaskManagerOperation.client_operation_id == operation_id,
        ))
        if operation is None:
            details = ([{"kind": "knowledge_task_proposal", "source_card_ids": card_ids, "preserve_undated": True}]
                       if card_ids else [{"kind": "approved_proposal", "proposal_id": proposal_id}])
            operation = TaskManagerOperation(
                user_id=user_id, client_operation_id=operation_id, status="started", mode="create",
                action_details=details,
            )
            db.add(operation)
            db.flush()
        result = create_tasks(db, user_id, run_id, operation_id, [item])
        task_ids = [int(row["id"]) for row in result["created"]]
        duplicate_ids = [int(row["task_id"]) for row in result["duplicates"]]
        if task_ids:
            task = db.get(Task, task_ids[0])
            if task is not None:
                task.task_code = f"ai-{proposal_id}-{index}"
            conversation_id, message_id = source
            if conversation_id:
                link_task_source(db, user_id, task_ids[0], conversation_id, message_id)
            created_ids.extend(task_ids)
        if card_ids and task_ids:
            from . import knowledge_service
            knowledge_service.link_tasks_to_cards(db, user_id, card_ids, task_ids)
        skipped_ids.extend(duplicate_ids)
    return created_ids, skipped_ids


def update_task(db: Session, user_id: int, run_id: int, operation_id: str, payload: UpdateTaskInput) -> dict:
    op = _operation(db, user_id, operation_id)
    task = _owned_task(db, user_id, payload.task_id)
    if unicodedata.normalize("NFKC", task.title).strip().casefold() != unicodedata.normalize("NFKC", payload.match_title).strip().casefold():
        raise ValueError("TASK_TITLE_CONFIRMATION_MISMATCH")
    matches = db.scalars(select(Task.id).where(Task.user_id == user_id, Task.deleted_at.is_(None), Task.title == task.title).limit(2)).all()
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_TASK_TITLE_CONFIRM_TARGET")
    changes = payload.model_dump(exclude_unset=True, exclude={"task_id", "match_title"})
    before = {key: (str(getattr(task, key)) if getattr(task, key) is not None else None) for key in changes}
    if {"start_date", "due_date", "expected_duration_days"}.intersection(changes):
        for key in ("start_date", "due_date", "expected_duration_days"):
            value = getattr(task, key)
            before[key] = str(value) if value is not None else None
    start = changes.get("start_date", task.start_date)
    due = changes.get("due_date", task.due_date)
    duration = changes.get("expected_duration_days", task.expected_duration_days)
    if "expected_duration_days" in changes:
        start = start or local_today()
        due = start + timedelta(days=duration - 1)
    elif "start_date" in changes and "due_date" not in changes:
        due = start + timedelta(days=(duration or 1) - 1) if start else None
    elif "due_date" in changes and "start_date" not in changes:
        duration = (due - start).days + 1 if start and due else None
    elif start and due:
        duration = (due - start).days + 1
    if start and due and (due < start or (duration is not None and (due - start).days + 1 != duration)):
        raise ValueError("TASK_DATE_RANGE_INVALID")
    if {"start_date", "due_date", "expected_duration_days"}.intersection(changes):
        changes.update(start_date=start, due_date=due, expected_duration_days=duration)
        if (start, due) != (task.start_date, task.due_date):
            db.add(TaskDateHistory(user_id=user_id, task_id=task.id, old_start_date=task.start_date, new_start_date=start, old_due_date=task.due_date, new_due_date=due, reason="task_manager_reschedule"))
    for key, value in changes.items():
        setattr(task, key, value)
    op.action_details = [*op.action_details, {"kind": "update", "task_id": task.id, "before": before}]
    op.status = "applied"
    op.run_id = run_id
    _add_audit(db, user_id, run_id, "task_manager_updated", [task.id])
    db.flush()
    return {"updated": {"id": task.id, "title": task.title, "module": task.module, "status": task.status.value, "start_date": str(task.start_date), "due_date": str(task.due_date), "estimated_minutes": task.estimated_minutes}, "operation_id": operation_id}


def request_delete_task(db: Session, user_id: int, run_id: int, operation_id: str, payload: DeleteTaskInput) -> dict:
    op = _operation(db, user_id, operation_id)
    task = _owned_task(db, user_id, payload.task_id)
    if unicodedata.normalize("NFKC", task.title).strip().casefold() != unicodedata.normalize("NFKC", payload.match_title).strip().casefold():
        raise ValueError("TASK_TITLE_CONFIRMATION_MISMATCH")
    matches = db.scalars(select(Task.id).where(Task.user_id == user_id, Task.deleted_at.is_(None), Task.title == task.title).limit(2)).all()
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_TASK_TITLE_CONFIRM_TARGET")
    op.action_details = [*op.action_details, {"kind": "delete_pending", "task_id": task.id}]
    op.status = "awaiting_confirmation"
    op.run_id = run_id
    db.add(AgentAuditEvent(user_id=user_id, run_id=run_id, actor="agent", event_type="task_delete_confirmation_requested", detail=f"task_id={task.id}"))
    db.flush()
    return {"confirmation_required": True, "task_id": task.id, "title": task.title, "operation_id": operation_id}


class TaskPurgeBlockedError(ValueError):
    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        super().__init__("该任务仍被不可变历史记录引用，暂不能彻底删除。")


def task_purge_preview(db: Session, user_id: int, task_id: int) -> dict:
    receipt = db.scalar(select(TaskPurgeReceipt).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task_id))
    if receipt:
        return {"task_id": task_id, "purged": True, "can_purge": False, "idempotent_replay": True, "purged_at": receipt.purged_at}
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.deleted_at is None:
        return {"task_id": task_id, "purged": False, "can_purge": False, "code": "TASK_MUST_BE_TRASHED", "message": "请先将任务移入最近删除。"}

    snapshot_count = db.scalar(select(func.count(DailyTaskSnapshot.id)).where(DailyTaskSnapshot.task_id == task_id)) or 0
    blockers = []
    if snapshot_count:
        blockers.append({"type": "daily_settlement_snapshots", "count": snapshot_count, "message": "已有每日结算记录引用此任务；为保留结算事实，不能彻底删除。"})

    active_operations = db.scalars(select(TaskManagerOperation).where(
        TaskManagerOperation.user_id == user_id,
        TaskManagerOperation.status.in_(["started", "running", "awaiting_confirmation", "awaiting_user_confirmation"]),
    )).all()
    for operation in active_operations:
        if any(action.get("task_id") == task_id for action in (operation.action_details or []) if isinstance(action, dict)):
            blockers.append({"type": "task_operation_in_progress", "count": 1, "message": "仍有任务操作正在处理此任务；完成或取消该操作后再试。"})
            break

    return {
        "task_id": task.id,
        "title": task.title,
        "purged": False,
        "can_purge": not blockers,
        "blockers": blockers,
        "cleanup": {
            "evidence": db.scalar(select(func.count(TaskEvidence.id)).where(TaskEvidence.task_id == task_id)) or 0,
            "status_history": db.scalar(select(func.count(TaskStatusHistory.id)).where(TaskStatusHistory.task_id == task_id)) or 0,
            "date_history": db.scalar(select(func.count(TaskDateHistory.id)).where(TaskDateHistory.task_id == task_id)) or 0,
            "conversation_links": db.scalar(select(func.count(TaskConversationLink.id)).where(TaskConversationLink.task_id == task_id)) or 0,
            "daily_settlements_preserved": snapshot_count,
        },
        "independent_conversations_preserved": True,
        "independent_proposals_preserved": True,
        "historical_backups_unchanged": True,
    }


def _redact_task_text(value, sensitive: list[str]):
    if isinstance(value, str):
        result = value
        for fragment in sensitive:
            if len(fragment.strip()) >= 3:
                result = result.replace(fragment, "[任务内容已清除]")
            elif result == fragment:
                return "[任务内容已清除]"
        return result
    if isinstance(value, list):
        return [_redact_task_text(item, sensitive) for item in value]
    if isinstance(value, dict):
        return {key: _redact_task_text(item, sensitive) for key, item in value.items()}
    return value


def purge_task(db: Session, user_id: int, task_id: int, confirm_title: str) -> dict:
    """Purge a trashed task and its private children in one transaction."""
    receipt = db.scalar(select(TaskPurgeReceipt).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task_id))
    if receipt:
        return {"task_id": task_id, "purged": True, "idempotent_replay": True, "purged_at": receipt.purged_at}
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise LookupError("TASK_NOT_FOUND")
    if task.deleted_at is None:
        raise ValueError("TASK_MUST_BE_TRASHED")
    if confirm_title.strip() != task.title:
        raise ValueError("CONFIRM_TITLE_MISMATCH")

    preview = task_purge_preview(db, user_id, task_id)
    if not preview["can_purge"]:
        raise TaskPurgeBlockedError(preview["blockers"])

    evidence = db.scalars(select(TaskEvidence).where(TaskEvidence.task_id == task_id)).all()
    sensitive = [task.title, task.description or "", task.acceptance_criteria or ""]
    sensitive.extend(item.content for item in evidence)
    sensitive.extend(item.source or "" for item in evidence)
    sensitive = [item for item in sensitive if item]

    # Keep operation identity and action IDs, but remove field snapshots that could recreate task text.
    for operation in db.scalars(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user_id)).all():
        if operation.action_details:
            operation.action_details = _redact_task_text(operation.action_details, sensitive)

    # Retain run/tool/status/time metadata while removing tool payload copies of the purged task.
    for call in db.scalars(select(AgentToolCall).where(AgentToolCall.user_id == user_id)).all():
        for attribute in ("arguments_summary", "result_summary"):
            raw = getattr(call, attribute)
            if not raw:
                continue
            try:
                redacted = json.dumps(_redact_task_text(json.loads(raw), sensitive), ensure_ascii=False)
            except (TypeError, ValueError):
                redacted = _redact_task_text(raw, sensitive)
            if redacted != raw:
                setattr(call, attribute, redacted)

    db.add(AgentAuditEvent(user_id=user_id, actor="user", event_type="task_content_purged",
                           detail=f"task_id={task_id}; evidence={len(evidence)}; snapshots=0; independent_conversations_and_proposals_preserved; backups_unchanged"))
    db.add(TaskPurgeReceipt(user_id=user_id, task_id=task_id))
    db.delete(task)
    try:
        db.commit()
    except Exception:
        db.rollback()
        replay = db.scalar(select(TaskPurgeReceipt).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task_id))
        if replay:
            return {"task_id": task_id, "purged": True, "idempotent_replay": True, "purged_at": replay.purged_at}
        if db.scalar(select(func.count(DailyTaskSnapshot.id)).where(DailyTaskSnapshot.task_id == task_id)):
            raise TaskPurgeBlockedError([{"type": "daily_settlement_snapshots", "count": 1, "message": "清理期间出现每日结算引用；事务已回滚，请保留任务。"}])
        raise
    receipt = db.scalar(select(TaskPurgeReceipt).where(TaskPurgeReceipt.user_id == user_id, TaskPurgeReceipt.task_id == task_id))
    return {"task_id": task_id, "purged": True, "idempotent_replay": False,
            "receipt_id": receipt.id if receipt else None, "cleanup": preview["cleanup"],
            "independent_conversations_preserved": True, "independent_proposals_preserved": True,
            "backup_notice": "独立历史备份不受影响，可能仍保留删除前的数据。"}
