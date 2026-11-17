from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    organization_name: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = "UTC"

    @field_validator("password")
    @classmethod
    def password_has_character_classes(cls, value: str) -> str:
        if not any(char.islower() for char in value) or not any(char.isupper() for char in value) or not any(char.isdigit() for char in value):
            raise ValueError("Password must contain uppercase, lowercase, and numeric characters.")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_is_valid(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Timezone must be a valid IANA timezone.") from exc
        return value

    @field_validator("country_code")
    @classmethod
    def country_code_is_uppercase(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str

    model_config = ConfigDict(from_attributes=True)


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    country_code: str | None
    region: str | None
    timezone: str
    reporting_currency: str | None

    model_config = ConfigDict(from_attributes=True)


class MembershipResponse(BaseModel):
    organization: OrganizationResponse
    role: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    organization: OrganizationResponse | None = None


class CurrentUserResponse(BaseModel):
    user: UserResponse
    organizations: list[MembershipResponse]
