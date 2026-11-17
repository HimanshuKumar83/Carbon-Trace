from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PipelineRunRequest(BaseModel):
    organization_id: UUID
    ingestion_run_id: UUID
    reporting_period_start: date
    reporting_period_end: date
    idempotency_key: str = Field(min_length=1, max_length=200)
    allow_illustrative: bool = False
    generate_report: bool = False

    @model_validator(mode="after")
    def period_is_valid(self) -> "PipelineRunRequest":
        if self.reporting_period_end < self.reporting_period_start:
            raise ValueError("reporting_period_end cannot precede reporting_period_start.")
        return self


class PipelineRunResponse(BaseModel):
    id: UUID
    organization_id: UUID
    trigger_type: str
    reporting_period_start: date
    reporting_period_end: date
    status: str
    current_stage: str | None
    error_summary: dict[str, Any] | None
    attempt: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentRunResponse(BaseModel):
    id: UUID
    agent_name: str
    status: str
    attempt: int
    input_summary: dict[str, Any] | None
    output_summary: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    is_retryable: bool
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PipelineListResponse(BaseModel):
    items: list[PipelineRunResponse]
    page: int
    page_size: int
    total: int
