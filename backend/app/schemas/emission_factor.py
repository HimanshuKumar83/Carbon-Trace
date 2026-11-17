from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ACTIVITY_UNITS = {
    "electricity": {"kWh", "MWh"},
    "natural_gas": {"kWh", "MWh", "therm", "m3"},
    "diesel": {"L", "gal"},
    "petrol": {"L", "gal"},
    "company_vehicle_fuel": {"L", "gal"},
    "business_travel": {"km", "passenger_km"},
    "employee_commuting": {"km", "passenger_km"},
    "purchased_goods_services": {"kg", "tonne"},
}


class EmissionFactorCreate(BaseModel):
    factor_key: str = Field(min_length=1, max_length=120)
    activity_category: str = Field(min_length=1, max_length=64)
    scope: int = Field(ge=1, le=3)
    geography_type: str
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region_code: str | None = Field(default=None, max_length=80)
    activity_unit: str = Field(min_length=1, max_length=32)
    factor_value: Decimal = Field(ge=0)
    factor_unit: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=32)
    valid_from: date
    valid_to: date | None = None
    source_name: str = Field(min_length=1)
    source_url: HttpUrl
    publication_version: str = Field(min_length=1, max_length=120)
    methodology: str = Field(min_length=1)
    quality_metadata: dict[str, Any] | None = None
    is_illustrative: bool = False

    @field_validator("country_code")
    @classmethod
    def country_code_is_uppercase(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @model_validator(mode="after")
    def validate_applicability(self) -> "EmissionFactorCreate":
        if self.geography_type not in {"country", "global"}:
            raise ValueError("geography_type must be country or global.")
        if (self.geography_type == "country") != (self.country_code is not None):
            raise ValueError("Country factors require a country_code; global factors must not set one.")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from.")
        if self.activity_category not in {
            "electricity",
            "natural_gas",
            "diesel",
            "petrol",
            "company_vehicle_fuel",
            "business_travel",
            "employee_commuting",
            "purchased_goods_services",
        }:
            raise ValueError("Unsupported activity category.")
        if self.activity_unit not in ACTIVITY_UNITS[self.activity_category]:
            raise ValueError("activity_unit is not supported for this activity category.")
        expected_factor_unit = f"kgco2e/{self.activity_unit}".lower()
        normalized_factor_unit = (
            self.factor_unit.strip().lower().replace("co₂e", "co2e").replace(" per ", "/").replace(" ", "")
        )
        if normalized_factor_unit != expected_factor_unit:
            raise ValueError("factor_unit must be expressed as kg CO2e per activity_unit.")
        if self.activity_category == "electricity" and self.scope != 2:
            raise ValueError("Purchased electricity must use Scope 2.")
        if self.activity_category in {"natural_gas", "diesel", "petrol", "company_vehicle_fuel"} and self.scope != 1:
            raise ValueError("Direct fuel combustion must use Scope 1.")
        if self.activity_category in {"business_travel", "employee_commuting", "purchased_goods_services"} and self.scope != 3:
            raise ValueError("This configured activity category must use Scope 3.")
        expected_method = {1: "direct_combustion", 2: "location_based", 3: "activity_based"}[self.scope]
        if self.method != expected_method:
            raise ValueError(f"{self.activity_category} currently requires the {expected_method} method.")
        return self


class EmissionFactorResponse(BaseModel):
    id: UUID
    organization_id: UUID | None
    factor_key: str
    activity_category: str
    scope: int
    geography_type: str
    country_code: str | None
    activity_unit: str
    factor_value: Decimal
    factor_unit: str
    method: str
    valid_from: date
    valid_to: date | None
    source_name: str
    source_url: str
    publication_version: str
    methodology: str
    quality_metadata: dict[str, Any] | None
    is_illustrative: bool
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
