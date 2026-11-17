from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WhatIfRequest(BaseModel):
    organization_id: UUID
    activity_category: str = Field(min_length=1, max_length=64)
    current_quantity: Decimal = Field(ge=0)
    scenario_quantity: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)
    activity_date: date
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    allow_illustrative: bool = False


class AnomalyResponse(BaseModel):
    id: UUID
    activity_category: str
    period_start: date
    period_end: date
    observed_value: Decimal
    baseline_value: Decimal | None
    deviation: Decimal | None
    method: str
    severity: str
    explanation: str
    evidence: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class InsightResponse(BaseModel):
    id: UUID
    insight_type: str
    title: str
    body: str
    evidence: dict[str, Any]
    confidence: Decimal | None
    generation_method: str

    model_config = ConfigDict(from_attributes=True)
