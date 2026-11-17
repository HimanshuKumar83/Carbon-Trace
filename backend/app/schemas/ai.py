from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AskRequest(BaseModel):
    organization_id: UUID
    question: str = Field(min_length=3, max_length=4000)
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def period_is_valid(self) -> "AskRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start.")
        return self


class RecommendationRequest(BaseModel):
    organization_id: UUID
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def period_is_valid(self) -> "RecommendationRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start.")
        return self


class Citation(BaseModel):
    id: str
    kind: str
    label: str
    source_url: str | None = None
    page: int | None = None


class AskResponse(BaseModel):
    interaction_id: UUID
    answer: str
    citations: list[Citation]
    limitations: list[str]
    period_start: date
    period_end: date
    provider: str
    model: str


class RecommendationItem(BaseModel):
    title: str
    verified_finding: str
    suggested_action: str
    rationale: str
    evidence_ids: list[str]
    guidance_ids: list[str]


class RecommendationResponse(BaseModel):
    interaction_id: UUID
    recommendations: list[RecommendationItem]
    citations: list[Citation]
    limitations: list[str]
    provider: str
    model: str


class KnowledgeDocumentResponse(BaseModel):
    id: UUID
    title: str
    filename: str
    source_url: str | None
    status: str
    chunk_count: int
    error_message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDocumentListResponse(BaseModel):
    items: list[KnowledgeDocumentResponse]


class GroundedAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class RecommendationDraft(BaseModel):
    recommendations: list[RecommendationItem] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class ReportNarrative(BaseModel):
    executive_summary: str = Field(min_length=1, max_length=5000)
    observations: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class AIInteractionHistoryItem(BaseModel):
    id: UUID
    interaction_type: str
    input_text: str
    response_json: dict[str, Any]
    evidence_ids: list[str]
    provider: str
    model: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
