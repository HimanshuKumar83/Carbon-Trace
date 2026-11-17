from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.models import AuditLog, Organization, OrganizationMember
from app.schemas.organization import OrganizationCreate, OrganizationResponse, OrganizationUpdate
from app.database.base import utc_now
from uuid import uuid4

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(payload: OrganizationCreate, user: CurrentUser, db: DbSession) -> OrganizationResponse:
    organization = Organization(
        name=payload.name.strip(),
        country_code=payload.country_code.upper() if payload.country_code else None,
        region=payload.region,
        timezone=payload.timezone,
    )
    db.add(organization)
    db.flush()
    db.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role="owner", status="active"))
    db.add(
        AuditLog(
            id=uuid4(),
            organization_id=organization.id,
            actor_user_id=user.id,
            action="organization.created",
            resource_type="organization",
            resource_id=organization.id,
            metadata_json={},
            created_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(organization)
    return OrganizationResponse.model_validate(organization)


@router.get("", response_model=list[OrganizationResponse])
def list_organizations(user: CurrentUser, db: DbSession) -> list[OrganizationResponse]:
    memberships = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == "active",
        )
    ).all()
    organizations = []
    for membership in memberships:
        organization = db.get(Organization, membership.organization_id)
        if organization is not None and organization.is_active:
            organizations.append(OrganizationResponse.model_validate(organization))
    return organizations


@router.get("/{organization_id}", response_model=OrganizationResponse)
def get_organization(organization_id: UUID, user: CurrentUser, db: DbSession) -> OrganizationResponse:
    require_membership(organization_id, user, db)
    organization = db.get(Organization, organization_id)
    if organization is None or not organization.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return OrganizationResponse.model_validate(organization)


@router.put("/{organization_id}", response_model=OrganizationResponse)
def update_organization(
    organization_id: UUID,
    payload: OrganizationUpdate,
    user: CurrentUser,
    db: DbSession,
) -> OrganizationResponse:
    require_membership(organization_id, user, db, {"owner", "admin"})
    organization = db.get(Organization, organization_id)
    if organization is None or not organization.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "country_code" and value:
            value = value.upper()
        setattr(organization, field, value)
    db.add(
        AuditLog(
            id=uuid4(),
            organization_id=organization.id,
            actor_user_id=user.id,
            action="organization.updated",
            resource_type="organization",
            resource_id=organization.id,
            metadata_json={"fields": list(payload.model_fields_set)},
            created_at=utc_now(),
        )
    )
    db.commit()
    db.refresh(organization)
    return OrganizationResponse.model_validate(organization)
