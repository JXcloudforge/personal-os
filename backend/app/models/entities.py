from datetime import date, datetime
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class TaskStatus(str, Enum):
    TODO = "TODO"
    DOING = "DOING"
    BLOCKED = "BLOCKED"
    VERIFY = "VERIFY"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    chat_history_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_max_tool_calls: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    agent_max_output_tokens: Mapped[int] = mapped_column(Integer, default=1600, server_default="1600")
    auto_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_interval_days: Mapped[int] = mapped_column(Integer, default=1)
    last_local_backup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_recovery_validation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    theme: Mapped[str] = mapped_column(String(24), default="classic", server_default="classic")
    theme_background_filename: Mapped[str | None] = mapped_column(String(80), nullable=True)
    theme_background_darkness: Mapped[int] = mapped_column(Integer, default=48, server_default="48")
    theme_background_blur: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    theme_background_opacity: Mapped[int] = mapped_column(Integer, default=48, server_default="48")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    sessions: Mapped[list["SessionToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SessionToken(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user: Mapped[User] = relationship(back_populates="sessions")


class Invitation(Base):
    """Single-use registration invite; only a one-way token digest is persisted."""

    __tablename__ = "registration_invites"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_registration_invites_token_hash"),
        CheckConstraint("issuer_kind = 'local_cli'", name="ck_registration_invites_local_issuer"),
        CheckConstraint("NOT (claimed_at IS NOT NULL AND revoked_at IS NOT NULL)", name="ck_registration_invites_claim_or_revoke"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    issuer_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="local_cli")


class AuthSecurityState(Base):
    """Hashed, short-lived state for registration throttling and CSRF challenges."""

    __tablename__ = "auth_security_state"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_code: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    module: Mapped[str] = mapped_column(String(40), default="system")
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.TODO)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    planned_due: Mapped[datetime | None] = mapped_column(DateTime)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    expected_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    evidence: Mapped[list["TaskEvidence"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    history: Mapped[list["TaskStatusHistory"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    date_history: Mapped[list["TaskDateHistory"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskEvidence(Base):
    __tablename__ = "task_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    evidence_type: Mapped[str] = mapped_column(String(40))
    verification_level: Mapped[str] = mapped_column(String(20), default="E1_SELF_REPORTED")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    task: Mapped[Task] = relationship(back_populates="evidence")


class TaskStatusHistory(Base):
    __tablename__ = "task_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    task: Mapped[Task] = relationship(back_populates="history")


class TaskDateHistory(Base):
    __tablename__ = "task_date_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    old_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    old_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="user_date_change")
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    task: Mapped[Task] = relationship(back_populates="date_history")


class StudyRecord(Base):
    __tablename__ = "study_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(8))
    topic: Mapped[str] = mapped_column(String(200))
    activity_type: Mapped[str] = mapped_column(String(40), default="reading")
    resource_url: Mapped[str | None] = mapped_column(String(500))
    source_name: Mapped[str | None] = mapped_column(String(200))
    planned_content: Mapped[str | None] = mapped_column(Text)
    actual_response: Mapped[str | None] = mapped_column(Text)
    assessment_status: Mapped[str] = mapped_column(String(30), default="BASELINE")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    errors: Mapped[list["StudyError"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class StudyError(Base):
    __tablename__ = "study_errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_record_id: Mapped[int] = mapped_column(ForeignKey("study_records.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    question: Mapped[str] = mapped_column(Text)
    user_answer: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    certainty: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    record: Mapped[StudyRecord] = relationship(back_populates="errors")


class Workout(Base):
    __tablename__ = "workouts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    training_type: Mapped[str] = mapped_column(String(80))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    exercises: Mapped[list | None] = mapped_column(JSON)
    effort_rating: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class HealthMetric(Base):
    __tablename__ = "health_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    weight: Mapped[float | None]
    sleep_hours: Mapped[float | None]
    energy_rating: Mapped[int | None]
    recovery_rating: Mapped[int | None]
    source: Mapped[str] = mapped_column(String(30), default="SELF_REPORTED")


class DailyReview(Base):
    __table_args__ = (UniqueConstraint("user_id", "review_date", name="uq_daily_reviews_user_date"),)
    __tablename__ = "daily_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    review_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    planned_summary: Mapped[str | None] = mapped_column(Text)
    actual_summary: Mapped[str | None] = mapped_column(Text)
    blockers: Mapped[str | None] = mapped_column(Text)
    next_actions: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    task_entries: Mapped[list["DailyTaskSnapshot"]] = relationship(back_populates="review", cascade="all, delete-orphan")


class DailyTaskSnapshot(Base):
    __tablename__ = "daily_task_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "review_date", "task_id", name="uq_daily_task_snapshot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("daily_reviews.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    review_date: Mapped[date] = mapped_column(Date, index=True)
    status_at_settlement: Mapped[str] = mapped_column(String(20))
    due_date_at_settlement: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_category: Mapped[str] = mapped_column(String(24))
    actual_progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    review: Mapped[DailyReview] = relationship(back_populates="task_entries")
    task: Mapped[Task] = relationship()


class AIRequest(Base):
    __tablename__ = "ai_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40))
    input_scope: Mapped[str] = mapped_column(String(200))
    consent_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIProvider(Base):
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_ai_providers_user_provider"),)
    __tablename__ = "ai_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    base_url: Mapped[str] = mapped_column(String(300))
    model: Mapped[str] = mapped_column(String(120))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="not_configured")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SearchProvider(Base):
    __tablename__ = "search_providers"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_search_providers_user_provider"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="tavily")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="not_configured")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UserGoal(Base):
    __tablename__ = "user_goals"
    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_user_goals_user_domain"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workflow: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    authorized_scope: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="started")
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30))
    arguments_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentProposal(Base):
    __tablename__ = "agent_proposals"
    __table_args__ = (UniqueConstraint("user_id", "proposal_key", name="uq_agent_proposals_user_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True)
    proposal_key: Mapped[str] = mapped_column(String(120))
    proposal_type: Mapped[str] = mapped_column(String(40))
    plan_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    title: Mapped[str] = mapped_column(String(200))
    items: Mapped[list] = mapped_column(JSON)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentAuditEvent(Base):
    __tablename__ = "agent_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("agent_proposals.id", ondelete="SET NULL"), nullable=True)
    actor: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新会话")
    domain: Mapped[str] = mapped_column(String(30), default="general", server_default="general", index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Nullable for migration compatibility; all new messages require an owned conversation in the API.
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class TaskConversationLink(Base):
    __tablename__ = "task_conversation_links"
    __table_args__ = (UniqueConstraint("user_id", "task_id", "conversation_id", "message_id", name="uq_task_chat_link"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskManagerOperation(Base):
    """Idempotency, audit linkage, and reversible task-manager writes."""
    __tablename__ = "task_manager_operations"
    __table_args__ = (UniqueConstraint("user_id", "client_operation_id", name="uq_task_manager_operation_user_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_operation_id: Mapped[str] = mapped_column(String(36))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    user_message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="started")
    mode: Mapped[str] = mapped_column(String(20), default="legacy", server_default="legacy")
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    action_details: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TaskPurgeReceipt(Base):
    """Minimal, non-restorable tombstone for an explicitly purged task."""
    __tablename__ = "task_purge_receipts"
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_task_purge_receipt_user_task"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(Integer)
    purged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AgentModelUsage(Base):
    __tablename__ = "agent_model_usage"
    __table_args__ = (UniqueConstraint("run_id", "request_index", name="uq_agent_model_usage_run_request"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    request_index: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="started")
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeCard(Base):
    __tablename__ = "knowledge_cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    knowledge_type: Mapped[str] = mapped_column(String(24), nullable=False, default="Concept")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeTag(Base):
    __tablename__ = "knowledge_tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_knowledge_tags_user_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgeCardTag(Base):
    __tablename__ = "knowledge_card_tags"
    __table_args__ = (UniqueConstraint("card_id", "tag_id", name="uq_knowledge_card_tag"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("knowledge_cards.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("knowledge_tags.id", ondelete="CASCADE"), index=True)


class KnowledgeSourceReference(Base):
    __tablename__ = "knowledge_source_references"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("knowledge_cards.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_applicable")
    retrieved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgeTaskLink(Base):
    __tablename__ = "knowledge_task_links"
    __table_args__ = (UniqueConstraint("user_id", "card_id", "task_id", name="uq_knowledge_task_link"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("knowledge_cards.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgeAuditEvent(Base):
    __tablename__ = "knowledge_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
