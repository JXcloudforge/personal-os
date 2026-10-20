from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


KnowledgeDomain = Literal["general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews"]
KnowledgeType = Literal["Concept", "Procedure", "Practice", "Question", "Insight"]
ReviewStatus = Literal["DRAFT", "REVIEWED"]


class KnowledgeRetrieveInput(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    domains: list[KnowledgeDomain] = Field(default_factory=list, max_length=8)
    top_k: int = Field(default=5, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value.strip()


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=1200)
    content_markdown: str = Field(min_length=1, max_length=30000)
    domain: KnowledgeDomain = "general"
    knowledge_type: KnowledgeType = "Concept"
    review_status: ReviewStatus = "DRAFT"
    tags: list[str] = Field(default_factory=list, max_length=20)
    conversation_id: str | None = Field(default=None, max_length=36)
    message_id: int | None = Field(default=None, gt=0)

    @field_validator("title", "content_markdown")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip()


class KnowledgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=1200)
    content_markdown: str | None = Field(default=None, min_length=1, max_length=30000)
    domain: KnowledgeDomain | None = None
    knowledge_type: KnowledgeType | None = None
    review_status: ReviewStatus | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    is_pinned: bool | None = None
    archived: bool | None = None

    @field_validator("title", "content_markdown")
    @classmethod
    def nonblank_if_set(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip() if value is not None else None


class KnowledgeTaskLinkInput(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class KnowledgePurgeInput(BaseModel):
    confirm_title: str = Field(min_length=1, max_length=200)


class KnowledgeRefineInput(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=36)
    message_id: int = Field(gt=0)
    selected_text: str | None = Field(default=None, min_length=1, max_length=30000)
    sensitive_data_consent: bool = False


class KnowledgeRefinement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worth_saving: bool
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    content_markdown: str = Field(min_length=1, max_length=30000)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "summary", "content_markdown")
    @classmethod
    def nonblank_refinement(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip()
