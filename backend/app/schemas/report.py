from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportGenerateRequest(BaseModel):
    organization_id: UUID
    pipeline_run_id: UUID
    report_type: str = Field(pattern="^(monthly|quarterly|custom)$")
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def period_is_valid(self) -> "ReportGenerateRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start.")
        return self


class ReportResponse(BaseModel):
    id: UUID
    organization_id: UUID
    pipeline_run_id: UUID
    report_type: str
    period_start: date
    period_end: date
    status: str
    content_sha256: str | None
    methodology_snapshot: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReportListResponse(BaseModel):
    items: list[ReportResponse]
    page: int
    page_size: int
    total: int
