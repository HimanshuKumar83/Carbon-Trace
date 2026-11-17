from datetime import date
from decimal import Decimal

import pytest

from app.models import ActivityRecord, EmissionFactor, Organization
from app.services.carbon import (
    AmbiguousEmissionFactorError,
    CarbonCalculationError,
    IllustrativeFactorError,
    MissingEmissionFactorError,
    calculate_emissions,
    convert_quantity,
    resolve_emission_factor,
)


def _factor(organization_id=None, country="IN", factor_value="0.7", valid_from=date(2025, 1, 1), valid_to=None):
    return EmissionFactor(
        factor_key="electricity-demo",
        organization_id=organization_id,
        activity_category="electricity",
        scope=2,
        geography_type="country" if country else "global",
        country_code=country,
        activity_unit="kWh",
        factor_value=Decimal(factor_value),
        factor_unit="kg CO2e/kWh",
        method="location_based",
        valid_from=valid_from,
        valid_to=valid_to,
        source_name="Test source",
        source_url="https://example.org/factor-methodology",
        publication_version="test-1",
        methodology="Test-only factor for unit tests.",
        is_illustrative=False,
        is_active=True,
    )


def _activity(organization_id, quantity="1000", unit="kWh", activity_date=date(2026, 1, 1)):
    return ActivityRecord(
        organization_id=organization_id,
        ingestion_run_id=None,
        record_fingerprint="fingerprint",
        raw_payload={},
        activity_category="electricity",
        activity_date=activity_date,
        quantity=Decimal(quantity),
        unit=unit,
        country_code="IN",
        scope=2,
        validation_status="valid",
        validation_issues=[],
    )


def test_calculation_is_exact_and_unit_conversion_is_explicit():
    from uuid import uuid4

    activity = _activity(uuid4(), quantity="1", unit="MWh")
    activity.id = uuid4()
    factor = _factor(factor_value="0.7")
    factor.id = uuid4()
    result = calculate_emissions(activity, factor)
    assert result.factor_quantity == Decimal("1000")
    assert result.co2e_kg == Decimal("700.0")
    assert result.conversion_ratio == Decimal("1000")


def test_missing_wrong_period_and_ambiguous_factors(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.database.base import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Test org", timezone="UTC")
        db.add(organization)
        db.flush()
        with pytest.raises(MissingEmissionFactorError):
            resolve_emission_factor(db, organization.id, "electricity", date(2026, 1, 1), "IN", 2, "location_based")

        old_factor = _factor(valid_from=date(2020, 1, 1), valid_to=date(2021, 12, 31))
        db.add(old_factor)
        db.flush()
        with pytest.raises(MissingEmissionFactorError):
            resolve_emission_factor(db, organization.id, "electricity", date(2026, 1, 1), "IN", 2, "location_based")

        db.add_all([_factor(factor_value="0.4"), _factor(factor_value="0.5")])
        db.flush()
        with pytest.raises(AmbiguousEmissionFactorError):
            resolve_emission_factor(db, organization.id, "electricity", date(2026, 1, 1), "IN", 2, "location_based")
    engine.dispose()


def test_scope_and_unit_mismatches_fail_safely():
    from uuid import uuid4

    activity = _activity(uuid4(), unit="gal")
    activity.id = uuid4()
    factor = _factor()
    factor.id = uuid4()
    with pytest.raises(CarbonCalculationError):
        calculate_emissions(activity, factor)
    with pytest.raises(CarbonCalculationError):
        convert_quantity(Decimal("10"), "gal", "L")


def test_illustrative_factors_require_explicit_opt_in():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.database.base import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Demo org", timezone="UTC")
        db.add(organization)
        db.flush()
        factor = _factor()
        factor.is_illustrative = True
        db.add(factor)
        db.flush()
        with pytest.raises(IllustrativeFactorError, match="demo-mode opt-in"):
            resolve_emission_factor(db, organization.id, "electricity", date(2026, 1, 1), "IN", 2, "location_based")
        selected = resolve_emission_factor(
            db, organization.id, "electricity", date(2026, 1, 1), "IN", 2, "location_based", allow_illustrative=True
        )
        assert selected.id == factor.id
    engine.dispose()


def test_organization_factor_can_be_created_listed_and_deactivated(client):
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": "factor-admin@example.org",
            "password": "CarbonTracePass123",
            "display_name": "Factor Admin",
            "organization_name": "Factor Org",
        },
    )
    organization_id = registration.json()["organization"]["id"]
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    factor_payload = {
        "factor_key": "verified-grid-factor-2026",
        "activity_category": "electricity",
        "scope": 2,
        "geography_type": "country",
        "country_code": "IN",
        "activity_unit": "kWh",
        "factor_value": "0.71",
        "factor_unit": "kg CO2e/kWh",
        "method": "location_based",
        "valid_from": "2026-01-01",
        "valid_to": None,
        "source_name": "Published grid inventory",
        "source_url": "https://example.org/grid-inventory",
        "publication_version": "2026",
        "methodology": "Verified source methodology.",
        "is_illustrative": False,
    }

    invalid_unit = dict(factor_payload, factor_unit="kg CO2e/L")
    invalid = client.post(
        "/api/v1/emission-factors",
        headers=headers,
        params={"organization_id": organization_id},
        json=invalid_unit,
    )
    assert invalid.status_code == 422

    created = client.post(
        "/api/v1/emission-factors",
        headers=headers,
        params={"organization_id": organization_id},
        json=factor_payload,
    )
    assert created.status_code == 201
    factor = created.json()
    assert factor["organization_id"] == organization_id

    listed = client.get(
        "/api/v1/emission-factors",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert listed.status_code == 200
    assert any(item["id"] == factor["id"] for item in listed.json())

    deactivated = client.post(
        f"/api/v1/emission-factors/{factor['id']}/deactivate",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_factor_rejects_activity_unit_mismatch():
    from pydantic import ValidationError

    from app.schemas.emission_factor import EmissionFactorCreate

    with pytest.raises(ValidationError, match="activity_unit is not supported"):
        EmissionFactorCreate(
            factor_key="invalid-electricity-unit",
            activity_category="electricity",
            scope=2,
            geography_type="global",
            activity_unit="L",
            factor_value="0.5",
            factor_unit="kg CO2e/L",
            method="location_based",
            valid_from=date(2026, 1, 1),
            source_name="Source",
            source_url="https://example.org/factor",
            publication_version="2026",
            methodology="Method",
        )
