from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActivityReviewRequest(BaseModel):
    decision: Literal["accept", "reject"]
    activity_category: str | None = Field(default=None, min_length=1, max_length=64)
    activity_date: date | None = None
    quantity: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)

    @model_validator(mode="after")
    def accepted_record_is_complete(self) -> "ActivityReviewRequest":
        if self.decision == "accept" and any(
            value is None
            for value in (self.activity_category, self.activity_date, self.quantity, self.unit)
        ):
            raise ValueError("Accepting a record requires activity_category, activity_date, quantity, and unit.")
        return self


class ActivityReviewResponse(BaseModel):
    id: UUID
    ingestion_run_id: UUID
    source_record_key: str | None
    raw_payload: dict[str, Any]
    activity_category: str | None
    activity_date: date | None
    quantity: Decimal | None
    unit: str | None
    country_code: str | None
    scope: int | None
    validation_status: str
    validation_issues: list[dict[str, Any]]
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ActivityReviewListResponse(BaseModel):
    items: list[ActivityReviewResponse]
    page: int
    page_size: int
    total: int
