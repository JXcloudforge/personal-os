from __future__ import annotations

from datetime import date as Date, datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal

from ..models import TaskStatus


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=200)


class LoginIn(SetupIn):
    pass


class RegistrationIn(BaseModel):
    invitation_code: str = Field(min_length=20, max_length=120)
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,80}", normalized):
            raise ValueError("Username may contain letters, numbers, dot, underscore, and hyphen")
        return normalized

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        categories = sum((any(c.islower() for c in value), any(c.isupper() for c in value), any(c.isdigit() for c in value), any(not c.isalnum() and not c.isspace() for c in value)))
        if len(value) < 12 or categories < 3:
            raise ValueError("Use at least 12 characters and three character types")
        return value


class AccountProfilePatch(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("username")
    @classmethod
    def username_characters(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", value):
            raise ValueError("Username may contain letters, numbers, dot, underscore, and hyphen")
        return value


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
    revoke_other_sessions: bool = True


class UserDataSettingsPatch(BaseModel):
    chat_history_enabled: bool | None = None
    auto_backup_enabled: bool | None = None
    backup_interval_days: int | None = Field(default=None, ge=1, le=30)


class AppearancePatch(BaseModel):
    theme: Literal["classic", "cyberpunk-night"] | None = None
    background_darkness: int | None = Field(default=None, ge=0, le=90)
    background_blur: int | None = Field(default=None, ge=0, le=20)
    background_opacity: int | None = Field(default=None, ge=0, le=70)


class MigrationExportIn(BaseModel):
    recovery_password: str = Field(min_length=16, max_length=256)


class TaskIn(BaseModel):
    task_code: str | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    module: str = "system"
    status: TaskStatus = TaskStatus.TODO
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    planned_due: datetime | None = None
    start_date: Date | None = None
    due_date: Date | None = None
    expected_duration_days: int | None = Field(default=None, ge=1, le=3650)
    acceptance_criteria: str | None = None

    @model_validator(mode="after")
    def validate_task_dates(self):
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date cannot be earlier than start_date")
        if self.start_date and self.due_date and self.expected_duration_days is not None:
            actual = (self.due_date - self.start_date).days + 1
            if actual != self.expected_duration_days:
                raise ValueError("expected_duration_days must match the inclusive date range")
        return self


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    module: str | None = None
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    is_pinned: bool | None = None
    archived: bool | None = None
    planned_due: datetime | None = None
    start_date: Date | None = None
    due_date: Date | None = None
    expected_duration_days: int | None = Field(default=None, ge=1, le=3650)
    acceptance_criteria: str | None = None


class DailyTaskSnapshotIn(BaseModel):
    task_id: int = Field(ge=1)
    report_category: Literal["completed_today", "in_progress", "blocked", "overdue"]
    actual_progress: str | None = Field(default=None, max_length=4000)
    evidence_note: str | None = Field(default=None, max_length=4000)


class EvidenceIn(BaseModel):
    evidence_type: str = "self_report"
    verification_level: str = "E1_SELF_REPORTED"
    content: str = Field(min_length=1)
    source: str | None = None


class TaskConversationLinkIn(BaseModel):
    conversation_id: str = Field(min_length=36, max_length=36)
    message_id: int | None = Field(default=None, ge=1)


class ChatTaskProposalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    module: str = Field(default="system", min_length=1, max_length=40)
    estimated_minutes: int = Field(ge=1, le=1440)
    acceptance_criteria: str = Field(min_length=1, max_length=1000)


class UsageBudgetPatch(BaseModel):
    max_tool_calls: int = Field(ge=1, le=8)
    max_output_tokens: int = Field(ge=256, le=8192)


class StudyIn(BaseModel):
    language: str = Field(pattern="^(en|de)$")
    topic: str = Field(min_length=1, max_length=200)
    activity_type: str = "reading"
    resource_url: str | None = None
    source_name: str | None = None
    planned_content: str | None = None
    actual_response: str | None = None
    assessment_status: str = "BASELINE"


class StudyErrorIn(BaseModel):
    category: str
    question: str
    user_answer: str
    expected_answer: str | None = None
    explanation: str | None = None
    certainty: str = "CONFIRMED"


class WorkoutIn(BaseModel):
    date: Date | None = None
    training_type: str
    duration_minutes: int | None = Field(default=None, ge=0, le=1440)
    exercises: list | None = None
    effort_rating: int | None = Field(default=None, ge=1, le=10)
    notes: str | None = None


class HealthMetricIn(BaseModel):
    weight: float | None = Field(default=None, ge=0, le=500)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)
    energy_rating: int | None = Field(default=None, ge=1, le=10)
    recovery_rating: int | None = Field(default=None, ge=1, le=10)


class ReviewIn(BaseModel):
    review_date: Date | None = None
    planned_summary: str | None = None
    actual_summary: str | None = None
    blockers: str | None = None
    next_actions: str | None = None
    task_entries: list[DailyTaskSnapshotIn] | None = Field(default=None, max_length=200)


class AIIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    purpose: str = Field(default="assistant", max_length=60)
    consent_scope: str = Field(default="task_summary", max_length=200)
    consent_granted: bool = False


class AIProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
