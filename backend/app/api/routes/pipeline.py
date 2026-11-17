import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from kombu.exceptions import OperationalError

from app.agents.orchestrator import execute_pipeline
from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.core.config import get_settings
from app.database.base import utc_now
from app.models import AgentRun, IngestionRun, PipelineRun
from app.schemas.pipeline import AgentRunResponse, PipelineListResponse, PipelineRunRequest, PipelineRunResponse
from app.tasks.pipeline_tasks import execute_pipeline_task

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
logger = logging.getLogger(__name__)


@router.post("/run")
def start_pipeline(
    payload: PipelineRunRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    require_membership(payload.organization_id, user, db, {"owner", "admin", "analyst"})
    ingestion = db.scalar(
        select(IngestionRun).where(
            IngestionRun.id == payload.ingestion_run_id,
            IngestionRun.organization_id == payload.organization_id,
        )
    )
    if ingestion is None or ingestion.status not in {"succeeded", "partial"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A completed organization ingestion run is required.")

    existing = db.scalar(
        select(PipelineRun).where(
            PipelineRun.organization_id == payload.organization_id,
            PipelineRun.idempotency_key == payload.idempotency_key,
        )
    )
    expected_state = {
        "ingestion_run_id": str(payload.ingestion_run_id),
        "allow_illustrative": payload.allow_illustrative,
        "generate_report": payload.generate_report,
    }
    if existing is not None:
        if (
            existing.reporting_period_start != payload.reporting_period_start
            or existing.reporting_period_end != payload.reporting_period_end
            or existing.state_summary != expected_state
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was already used for a different pipeline request.")
        return {"pipeline_run": PipelineRunResponse.model_validate(existing).model_dump(mode="json"), "reused": True}

    pipeline = PipelineRun(
        organization_id=payload.organization_id,
        requested_by_user_id=user.id,
        trigger_type="manual",
        reporting_period_start=payload.reporting_period_start,
        reporting_period_end=payload.reporting_period_end,
        status="queued",
        idempotency_key=payload.idempotency_key,
        state_summary=expected_state,
        attempt=0,
    )
    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)

    if get_settings().run_jobs_async:
        try:
            task = execute_pipeline_task.delay(
                str(pipeline.id),
                str(payload.ingestion_run_id),
                payload.allow_illustrative,
                payload.generate_report,
            )
        except OperationalError as error:
            pipeline.status = "failed"
            pipeline.current_stage = "queue"
            pipeline.error_summary = {"code": "TASK_QUEUE_UNAVAILABLE", "message": "Pipeline could not be queued."}
            pipeline.completed_at = utc_now()
            db.commit()
            logger.exception("Could not enqueue pipeline run %s", pipeline.id)
            raise HTTPException(status_code=503, detail="Pipeline task queue is unavailable.") from error
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pipeline_run": PipelineRunResponse.model_validate(pipeline).model_dump(mode="json"),
                "task_id": task.id,
                "reused": False,
            },
        )

    execute_pipeline(
        pipeline.id,
        payload.ingestion_run_id,
        allow_illustrative=payload.allow_illustrative,
        generate_report=payload.generate_report,
    )
    db.refresh(pipeline)
    return {"pipeline_run": PipelineRunResponse.model_validate(pipeline).model_dump(mode="json"), "reused": False}


@router.get("/runs", response_model=PipelineListResponse)
def list_pipeline_runs(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> PipelineListResponse:
    require_membership(organization_id, user, db)
    query = select(PipelineRun).where(PipelineRun.organization_id == organization_id)
    count_query = select(func.count()).select_from(PipelineRun).where(PipelineRun.organization_id == organization_id)
    if status_filter:
        query = query.where(PipelineRun.status == status_filter)
        count_query = count_query.where(PipelineRun.status == status_filter)
    items = db.scalars(
        query.order_by(PipelineRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return PipelineListResponse(
        items=[PipelineRunResponse.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=db.scalar(count_query) or 0,
    )


@router.get("/runs/{pipeline_run_id}", response_model=PipelineRunResponse)
def get_pipeline_run(
    pipeline_run_id: UUID,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> PipelineRunResponse:
    require_membership(organization_id, user, db)
    pipeline = db.scalar(
        select(PipelineRun).where(
            PipelineRun.id == pipeline_run_id,
            PipelineRun.organization_id == organization_id,
        )
    )
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found.")
    return PipelineRunResponse.model_validate(pipeline)


@router.get("/runs/{pipeline_run_id}/agents", response_model=list[AgentRunResponse])
def list_agent_runs(
    pipeline_run_id: UUID,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[AgentRunResponse]:
    require_membership(organization_id, user, db)
    pipeline = db.scalar(
        select(PipelineRun).where(
            PipelineRun.id == pipeline_run_id,
            PipelineRun.organization_id == organization_id,
        )
    )
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found.")
    runs = db.scalars(
        select(AgentRun)
        .where(AgentRun.organization_id == organization_id, AgentRun.pipeline_run_id == pipeline_run_id)
        .order_by(AgentRun.created_at)
    ).all()
    return [AgentRunResponse.model_validate(run) for run in runs]


@router.post("/runs/{pipeline_run_id}/retry")
def retry_pipeline(
    pipeline_run_id: UUID,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    require_membership(organization_id, user, db, {"owner", "admin", "analyst"})
    previous = db.scalar(
        select(PipelineRun).where(
            PipelineRun.id == pipeline_run_id,
            PipelineRun.organization_id == organization_id,
        )
    )
    if previous is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found.")
    if previous.status not in {"failed", "partial"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only failed or partial pipeline runs can be retried.")
    ingestion_id = previous.state_summary.get("ingestion_run_id")
    if not ingestion_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pipeline run has no recoverable ingestion reference.")
    retried = PipelineRun(
        organization_id=organization_id,
        requested_by_user_id=user.id,
        trigger_type="retry",
        reporting_period_start=previous.reporting_period_start,
        reporting_period_end=previous.reporting_period_end,
        status="queued",
        idempotency_key=f"retry-{previous.id}-{uuid4()}",
        state_summary=previous.state_summary,
        attempt=0,
    )
    db.add(retried)
    db.commit()
    db.refresh(retried)
    allow_illustrative = bool(retried.state_summary.get("allow_illustrative", False))
    generate_report = bool(retried.state_summary.get("generate_report", False))
    if get_settings().run_jobs_async:
        try:
            task = execute_pipeline_task.delay(str(retried.id), ingestion_id, allow_illustrative, generate_report)
        except OperationalError as error:
            retried.status = "failed"
            retried.current_stage = "queue"
            retried.error_summary = {"code": "TASK_QUEUE_UNAVAILABLE", "message": "Pipeline could not be queued."}
            retried.completed_at = utc_now()
            db.commit()
            logger.exception("Could not enqueue retry pipeline run %s", retried.id)
            raise HTTPException(status_code=503, detail="Pipeline task queue is unavailable.") from error
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"pipeline_run": PipelineRunResponse.model_validate(retried).model_dump(mode="json"), "task_id": task.id},
        )
    execute_pipeline(retried.id, UUID(ingestion_id), allow_illustrative, generate_report)
    db.refresh(retried)
    return {"pipeline_run": PipelineRunResponse.model_validate(retried).model_dump(mode="json")}
