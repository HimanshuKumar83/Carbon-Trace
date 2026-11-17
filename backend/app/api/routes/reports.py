from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.core.config import get_settings
from app.models import CalculationResult, PipelineRun, Report
from app.schemas.report import ReportGenerateRequest, ReportListResponse, ReportResponse
from app.services.reporting import generate_sustainability_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def generate_report(
    payload: ReportGenerateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ReportResponse:
    require_membership(payload.organization_id, user, db, {"owner", "admin", "analyst"})
    pipeline = db.scalar(
        select(PipelineRun).where(
            PipelineRun.id == payload.pipeline_run_id,
            PipelineRun.organization_id == payload.organization_id,
        )
    )
    if pipeline is None or pipeline.status not in {"succeeded", "partial"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A completed or partial pipeline run is required.")
    if (
        pipeline.reporting_period_start != payload.period_start
        or pipeline.reporting_period_end != payload.period_end
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Report period must match the selected pipeline run.")
    calculation_count = db.scalar(
        select(func.count()).select_from(CalculationResult).where(
            CalculationResult.organization_id == payload.organization_id,
            CalculationResult.pipeline_run_id == pipeline.id,
        )
    )
    if not calculation_count:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The pipeline has no persisted calculation results.")
    report = generate_sustainability_report(
        db,
        pipeline,
        incomplete=pipeline.status != "succeeded",
        report_type=payload.report_type,
        requested_by_user_id=user.id,
    )
    db.commit()
    db.refresh(report)
    return ReportResponse.model_validate(report)


@router.get("", response_model=ReportListResponse)
def list_reports(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> ReportListResponse:
    require_membership(organization_id, user, db)
    query = select(Report).where(Report.organization_id == organization_id)
    count_query = select(func.count()).select_from(Report).where(Report.organization_id == organization_id)
    reports = db.scalars(
        query.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return ReportListResponse(
        items=[ReportResponse.model_validate(report) for report in reports],
        page=page,
        page_size=page_size,
        total=db.scalar(count_query) or 0,
    )


@router.get("/{report_id}", response_model=ReportResponse)
def get_report(report_id: UUID, organization_id: UUID, user: CurrentUser, db: DbSession) -> ReportResponse:
    require_membership(organization_id, user, db)
    report = db.scalar(select(Report).where(Report.id == report_id, Report.organization_id == organization_id))
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return ReportResponse.model_validate(report)


@router.get("/{report_id}/download")
def download_report(report_id: UUID, organization_id: UUID, user: CurrentUser, db: DbSession) -> FileResponse:
    require_membership(organization_id, user, db)
    report = db.scalar(select(Report).where(Report.id == report_id, Report.organization_id == organization_id))
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    if report.status not in {"complete", "incomplete"} or not report.storage_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Report file is not available.")
    report_root = (Path(get_settings().report_directory).resolve() / str(organization_id)).resolve()
    file_path = Path(report.storage_key).resolve()
    if not file_path.is_relative_to(report_root) or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report file not found.")
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=f"carbontrace-{report.id}.pdf",
        headers={"X-Content-Type-Options": "nosniff"},
    )
