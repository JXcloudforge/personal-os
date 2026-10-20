from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class GoalIn(BaseModel):
    domain: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=2, ge=1, le=3)
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=1000)


class GoalPatch(BaseModel):
    domain: str | None = Field(default=None, min_length=1, max_length=40)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: int | None = Field(default=None, ge=1, le=3)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


class PlanRequest(BaseModel):
    available_minutes: int = Field(default=60, ge=5, le=720)
    priority_note: str | None = Field(default=None, max_length=500)
    mode: Literal["deepseek", "mock"] = "deepseek"
    consent_granted: bool = False


class PlanItem(BaseModel):
    domain: str = Field(min_length=1, max_length=40)
    goal_id: int | None = None
    existing_task_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=1000)
    estimated_minutes: int = Field(ge=5, le=240)
    why_today: str = Field(min_length=1, max_length=1000)
    acceptance_criteria: str = Field(min_length=1, max_length=1000)
    evidence_required: str = Field(min_length=1, max_length=1000)
    source_task_ids: list[int] = Field(default_factory=list, max_length=20)
    sources: list[str] = Field(default_factory=list, max_length=5)
    knowledge_card_ids: list[int] = Field(default_factory=list, max_length=5)
    source_conversation_id: str | None = Field(default=None, max_length=36)
    source_message_id: int | None = Field(default=None, ge=1)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values):
        for value in values:
            parsed = urlparse(value)
            if len(value) > 2000 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Sources must be credential-free HTTPS URLs")
        return values

    @field_validator("knowledge_card_ids")
    @classmethod
    def validate_knowledge_card_ids(cls, values):
        if any(value < 1 for value in values) or len(set(values)) != len(values):
            raise ValueError("Knowledge card IDs must be positive and unique")
        return values

    @field_validator("goal_id", "existing_task_id", mode="before")
    @classmethod
    def normalize_unknown_ids(cls, value):
        if value is None or (isinstance(value, str) and value.strip().casefold() in {"unknown", "none", "null", ""}):
            return None
        return value


class ProposalActionIn(BaseModel):
    action: Literal["edit", "approve", "reject"]
    items: list[PlanItem] | None = Field(default=None, max_length=3)
    note: str | None = Field(default=None, max_length=1000)


class EveningReviewRequest(BaseModel):
    mode: Literal["deepseek", "mock"] = "mock"
    consent_granted: bool = False


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=36)
    domain: Literal["general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "tasks"] = "general"
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    consent_granted: bool = False
    include_personal_context: bool = False
    enable_web_tools: bool = False
    use_knowledge_base: bool = False
    knowledge_domains: list[Literal["general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews"]] = Field(default_factory=list, max_length=8)


class ChatConversationCreate(BaseModel):
    domain: Literal["general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "tasks"] = "general"


class ChatConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    domain: Literal["general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "tasks"] | None = None
    is_pinned: bool | None = None
    archived: bool | None = None


class TaskManagerChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    operation_id: str = Field(min_length=36, max_length=36)
    conversation_id: str | None = Field(default=None, min_length=36, max_length=36)
    # auto is retained for previously shipped clients; the current UI always sends one of the three explicit modes.
    mode: Literal["create", "plan", "modify", "auto"] = "auto"
    knowledge_card_ids: list[int] = Field(default_factory=list, max_length=5)
    knowledge_consent_granted: bool = False
    candidate_tasks: list[dict] | None = Field(default=None, max_length=10)


class PurgeContentRequest(BaseModel):
    confirm_title: str = Field(min_length=1, max_length=200)


class SearchCredentialIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=1000)
