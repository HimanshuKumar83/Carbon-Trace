from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Organization, OrganizationMember, User
from app.schemas.auth import (
    AuthResponse,
    CurrentUserResponse,
    LoginRequest,
    MembershipResponse,
    OrganizationResponse,
    RegisterRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> AuthResponse:
    normalized_email = str(payload.email).strip().lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    user = User(
        email=normalized_email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    organization = Organization(
        name=payload.organization_name.strip(),
        country_code=payload.country_code,
        timezone=payload.timezone,
    )
    db.add_all([user, organization])
    try:
        db.flush()
        db.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role="owner", status="active"))
        db.commit()
        db.refresh(user)
        db.refresh(organization)
    except IntegrityError:
        db.rollback()
        existing_user = db.scalar(select(User).where(User.email == normalized_email))
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            ) from None
        raise

    return AuthResponse(
        access_token=create_access_token(user.id),
        user=UserResponse.model_validate(user),
        organization=OrganizationResponse.model_validate(organization),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: DbSession) -> AuthResponse:
    normalized_email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    return AuthResponse(access_token=create_access_token(user.id), user=UserResponse.model_validate(user))


@router.get("/me", response_model=CurrentUserResponse)
def current_user(user: CurrentUser, db: DbSession) -> CurrentUserResponse:
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
            organizations.append(
                MembershipResponse(organization=OrganizationResponse.model_validate(organization), role=membership.role)
            )
    return CurrentUserResponse(user=UserResponse.model_validate(user), organizations=organizations)
