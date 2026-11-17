from uuid import UUID

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.database.base import utc_now
from app.models import AuditLog, EmissionFactor
from app.schemas.emission_factor import EmissionFactorCreate, EmissionFactorResponse

router = APIRouter(prefix="/emission-factors", tags=["emission factors"])


@router.post("", response_model=EmissionFactorResponse, status_code=status.HTTP_201_CREATED)
def create_emission_factor(
    organization_id: UUID,
    payload: EmissionFactorCreate,
    user: CurrentUser,
    db: DbSession,
) -> EmissionFactorResponse:
    require_membership(organization_id, user, db, {"owner", "admin"})
    factor_data = payload.model_dump(exclude={"source_url"})
    factor = EmissionFactor(
        organization_id=organization_id,
        **factor_data,
        source_url=str(payload.source_url),
        is_active=True,
    )
    db.add(factor)
    db.flush()
    db.add(
        AuditLog(
            id=uuid4(),
            organization_id=organization_id,
            actor_user_id=user.id,
            action="emission_factor.created",
            resource_type="emission_factor",
            resource_id=factor.id,
            metadata_json={
                "factor_key": factor.factor_key,
                "activity_category": factor.activity_category,
                "source_name": factor.source_name,
            },
            created_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(factor)
    return EmissionFactorResponse.model_validate(factor)


@router.get("", response_model=list[EmissionFactorResponse])
def list_emission_factors(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    activity_category: str | None = None,
    include_inactive: bool = False,
) -> list[EmissionFactorResponse]:
    membership = require_membership(organization_id, user, db)
    if include_inactive and membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient organization permissions.")
    query = select(EmissionFactor).where(
        (EmissionFactor.organization_id == organization_id) | EmissionFactor.organization_id.is_(None)
    )
    if activity_category:
        query = query.where(EmissionFactor.activity_category == activity_category)
    if not include_inactive:
        query = query.where(EmissionFactor.is_active.is_(True))
    factors = db.scalars(query.order_by(EmissionFactor.activity_category, EmissionFactor.valid_from.desc())).all()
    return [EmissionFactorResponse.model_validate(factor) for factor in factors]


@router.post("/{factor_id}/deactivate", response_model=EmissionFactorResponse)
def deactivate_emission_factor(
    factor_id: UUID,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> EmissionFactorResponse:
    require_membership(organization_id, user, db, {"owner", "admin"})
    factor = db.scalar(
        select(EmissionFactor).where(
            EmissionFactor.id == factor_id,
            EmissionFactor.organization_id == organization_id,
        )
    )
    if factor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emission factor not found.")
    factor.is_active = False
    db.add(
        AuditLog(
            id=uuid4(),
            organization_id=organization_id,
            actor_user_id=user.id,
            action="emission_factor.deactivated",
            resource_type="emission_factor",
            resource_id=factor.id,
            metadata_json={"factor_key": factor.factor_key},
            created_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(factor)
    return EmissionFactorResponse.model_validate(factor)
