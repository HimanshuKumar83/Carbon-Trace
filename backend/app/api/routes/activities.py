from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.database.base import utc_now
from app.models import ActivityRecord, AuditLog, IngestionRun
from app.schemas.activity import ActivityReviewListResponse, ActivityReviewRequest, ActivityReviewResponse
from app.services.ingestion import validate_activity_row

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=ActivityReviewListResponse)
def list_activities(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    validation_status: str = Query(default="needs_review", pattern="^(pending|needs_review|valid|rejected)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> ActivityReviewListResponse:
    require_membership(organization_id, user, db)
    query = select(ActivityRecord).where(
        ActivityRecord.organization_id == organization_id,
        ActivityRecord.validation_status == validation_status,
    )
    count_query = select(func.count()).select_from(ActivityRecord).where(
        ActivityRecord.organization_id == organization_id,
        ActivityRecord.validation_status == validation_status,
    )
    records = db.scalars(
        query.order_by(ActivityRecord.created_at.desc(), ActivityRecord.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ActivityReviewListResponse(
        items=[ActivityReviewResponse.model_validate(record) for record in records],
        page=page,
        page_size=page_size,
        total=db.scalar(count_query) or 0,
    )


@router.patch("/{activity_id}/review", response_model=ActivityReviewResponse)
def review_activity(
    activity_id: UUID,
    organization_id: UUID,
    payload: ActivityReviewRequest,
    user: CurrentUser,
    db: DbSession,
) -> ActivityReviewResponse:
    require_membership(organization_id, user, db, {"owner", "admin", "analyst"})
    activity = db.scalar(
        select(ActivityRecord).where(
            ActivityRecord.id == activity_id,
            ActivityRecord.organization_id == organization_id,
        )
    )
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity record not found.")
    if activity.validation_status not in {"needs_review", "pending"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only pending or reviewable activity records can be reviewed.")
    previous_validation_status = activity.validation_status

    corrected_fields: list[str] = []
    if payload.decision == "accept":
        candidate = {
            "activity_category": payload.activity_category,
            "activity_date": payload.activity_date.isoformat() if payload.activity_date else None,
            "quantity": str(payload.quantity) if payload.quantity is not None else None,
            "unit": payload.unit,
            "country_code": payload.country_code,
        }
        normalized, issues = validate_activity_row(candidate)
        if issues:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Corrected activity data is still invalid.", "issues": issues},
            )
        corrected_fields = [
            field
            for field, value in candidate.items()
            if value is not None and value != activity.raw_payload.get(field)
        ]
        activity.activity_category = normalized["activity_category"]
        activity.activity_date = normalized["activity_date"]
        activity.quantity = normalized["quantity"]
        activity.unit = normalized["unit"]
        activity.country_code = normalized["country_code"]
        activity.scope = normalized["scope"]
        activity.validation_status = "valid"
        activity.validation_issues = []
    else:
        activity.validation_status = "rejected"

    activity.reviewed_by_user_id = user.id
    activity.reviewed_at = utc_now()
    db.add(
        AuditLog(
            id=uuid4(),
            organization_id=organization_id,
            actor_user_id=user.id,
            action=f"activity.review.{payload.decision}",
            resource_type="activity_record",
            resource_id=activity.id,
            metadata_json={
                "decision": payload.decision,
                "corrected_fields": corrected_fields,
                "previous_validation_status": previous_validation_status,
            },
            created_at=utc_now(),
        )
    )
    db.flush()
    ingestion = db.get(IngestionRun, activity.ingestion_run_id)
    if ingestion is None or ingestion.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The activity ingestion record is unavailable.")
    accepted_count = db.scalar(
        select(func.count()).select_from(ActivityRecord).where(
            ActivityRecord.organization_id == organization_id,
            ActivityRecord.ingestion_run_id == ingestion.id,
            ActivityRecord.validation_status == "valid",
        )
    ) or 0
    rejected_count = db.scalar(
        select(func.count()).select_from(ActivityRecord).where(
            ActivityRecord.organization_id == organization_id,
            ActivityRecord.ingestion_run_id == ingestion.id,
            ActivityRecord.validation_status.in_(("needs_review", "pending", "rejected")),
        )
    ) or 0
    ingestion.records_accepted = accepted_count
    ingestion.records_rejected = rejected_count
    ingestion.status = (
        "succeeded"
        if rejected_count == 0
        else "partial" if accepted_count > 0 else "failed"
    )
    ingestion.error_summary = {
        **(ingestion.error_summary or {}),
        "data_quality_score": round(accepted_count / ingestion.records_seen * 100, 2) if ingestion.records_seen else 0,
        "invalid_records": rejected_count,
    }
    db.commit()
    db.refresh(activity)
    return ActivityReviewResponse.model_validate(activity)
