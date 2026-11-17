import csv
import io
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.core.config import get_settings
from app.models import IngestionRun
from app.schemas.ingestion import IngestionListResponse, IngestionRunResponse
from app.services.ingestion import ingest_activity_file

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/upload", response_model=IngestionRunResponse, status_code=status.HTTP_201_CREATED)
def upload_activity_file(
    organization_id: Annotated[UUID, Form()],
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Form()] = None,
) -> IngestionRunResponse:
    require_membership(organization_id, user, db, {"owner", "admin", "analyst"})
    max_bytes = get_settings().max_upload_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = file.file.read(min(1024 * 1024, max_bytes + 1 - total_bytes))
        if not chunk:
            break
        chunks.append(chunk)
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload exceeds the configured size limit.")
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty.")
    run, _was_duplicate = ingest_activity_file(
        db,
        organization_id,
        user.id,
        file.filename or "upload",
        content,
        idempotency_key,
    )
    return IngestionRunResponse.model_validate(run)


@router.get("/history", response_model=IngestionListResponse)
def ingestion_history(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> IngestionListResponse:
    require_membership(organization_id, user, db)
    query = select(IngestionRun).where(IngestionRun.organization_id == organization_id)
    count_query = select(func.count()).select_from(IngestionRun).where(IngestionRun.organization_id == organization_id)
    if status_filter:
        query = query.where(IngestionRun.status == status_filter)
        count_query = count_query.where(IngestionRun.status == status_filter)
    items = db.scalars(
        query.order_by(IngestionRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    total = db.scalar(count_query) or 0
    return IngestionListResponse(
        items=[IngestionRunResponse.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/templates/{file_format}")
def download_template(file_format: str) -> StreamingResponse:
    if file_format.lower() != "csv":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Only the CSV sample template is currently available.")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["activity_category", "activity_date", "quantity", "unit", "country_code", "data_status"])
    writer.writerow(["electricity", "2026-01-15", "12000", "kWh", "IN", "SYNTHETIC DEMO DATA"])
    writer.writerow(["diesel", "2026-01-20", "250", "L", "IN", "SYNTHETIC DEMO DATA"])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="carbontrace-sample-activity.csv"'},
    )


@router.get("/{ingestion_run_id}", response_model=IngestionRunResponse)
def get_ingestion_run(
    ingestion_run_id: UUID,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> IngestionRunResponse:
    require_membership(organization_id, user, db)
    run = db.scalar(
        select(IngestionRun).where(
            IngestionRun.id == ingestion_run_id,
            IngestionRun.organization_id == organization_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found.")
    return IngestionRunResponse.model_validate(run)
