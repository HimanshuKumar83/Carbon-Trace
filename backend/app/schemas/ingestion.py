from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IngestionRunResponse(BaseModel):
    id: UUID
    status: str
    source_name: str | None
    source_sha256: str | None
    records_seen: int
    records_accepted: int
    records_rejected: int
    error_summary: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IngestionListResponse(BaseModel):
    items: list[IngestionRunResponse]
    page: int
    page_size: int
    total: int
