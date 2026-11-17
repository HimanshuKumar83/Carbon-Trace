from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.base import utc_now
from app.models import ActivityRecord, CalculationResult, EmissionFactor, PipelineRun


class CarbonCalculationError(ValueError):
    """Raised when a record cannot be calculated using an applicable factor."""


class MissingEmissionFactorError(CarbonCalculationError):
    pass


class AmbiguousEmissionFactorError(CarbonCalculationError):
    pass


class IllustrativeFactorError(CarbonCalculationError):
    pass


SUPPORTED_SCOPES = {
    "electricity": 2,
    "natural_gas": 1,
    "diesel": 1,
    "petrol": 1,
    "company_vehicle_fuel": 1,
    "business_travel": 3,
    "employee_commuting": 3,
    "purchased_goods_services": 3,
}
METHOD_BY_SCOPE = {1: "direct_combustion", 2: "location_based", 3: "activity_based"}
UNIT_CONVERSIONS: dict[tuple[str, str], Decimal] = {
    ("kWh", "MWh"): Decimal("0.001"),
    ("MWh", "kWh"): Decimal("1000"),
    ("kg", "tonne"): Decimal("0.001"),
    ("tonne", "kg"): Decimal("1000"),
}


@dataclass(frozen=True)
class Calculation:
    activity_record_id: UUID
    emission_factor_id: UUID
    activity_quantity: Decimal
    factor_quantity: Decimal
    factor_value: Decimal
    activity_unit: str
    factor_activity_unit: str
    co2e_kg: Decimal
    scope: int
    method: str
    conversion_ratio: Decimal


@dataclass(frozen=True)
class CalculationBatch:
    results: tuple[CalculationResult, ...]
    failures: tuple[dict[str, str | UUID], ...]


def convert_quantity(quantity: Decimal, source_unit: str, target_unit: str) -> tuple[Decimal, Decimal]:
    if not quantity.is_finite() or quantity < 0:
        raise CarbonCalculationError("Activity quantity must be a finite non-negative number.")
    if source_unit == target_unit:
        return quantity, Decimal("1")
    ratio = UNIT_CONVERSIONS.get((source_unit, target_unit))
    if ratio is None:
        raise CarbonCalculationError(f"No documented conversion is available from {source_unit} to {target_unit}.")
    return quantity * ratio, ratio


def resolve_emission_factor(
    db: Session,
    organization_id: UUID,
    activity_category: str,
    activity_date: date,
    country_code: str | None,
    scope: int,
    method: str,
    allow_illustrative: bool = False,
) -> EmissionFactor:
    expected_scope = SUPPORTED_SCOPES.get(activity_category)
    if expected_scope is None or expected_scope != scope:
        raise CarbonCalculationError("Activity category does not have a supported, documented scope mapping.")

    geography_filter = EmissionFactor.geography_type == "global"
    if country_code:
        geography_filter = or_(
            EmissionFactor.geography_type == "global",
            (
                (EmissionFactor.geography_type == "country")
                & (EmissionFactor.country_code == country_code.upper())
            ),
        )

    candidates = db.scalars(
        select(EmissionFactor).where(
            EmissionFactor.activity_category == activity_category,
            EmissionFactor.scope == scope,
            EmissionFactor.method == method,
            EmissionFactor.is_active.is_(True),
            EmissionFactor.valid_from <= activity_date,
            or_(EmissionFactor.valid_to.is_(None), EmissionFactor.valid_to >= activity_date),
            or_(EmissionFactor.organization_id.is_(None), EmissionFactor.organization_id == organization_id),
            geography_filter,
        )
    ).all()
    if not candidates:
        raise MissingEmissionFactorError(
            f"No applicable emission factor for {activity_category}, {country_code or 'global'} "
            f"on {activity_date.isoformat()} using {method}."
        )
    authoritative = [candidate for candidate in candidates if not candidate.is_illustrative]
    if not allow_illustrative and not authoritative:
        raise IllustrativeFactorError("Only illustrative factors match; explicit demo-mode opt-in is required.")
    if not allow_illustrative:
        candidates = authoritative

    def rank(factor: EmissionFactor) -> tuple[int, int]:
        is_tenant_specific = int(factor.organization_id == organization_id)
        is_country_specific = int(factor.geography_type == "country")
        return is_tenant_specific, is_country_specific

    best_rank = max(rank(candidate) for candidate in candidates)
    best_candidates = [candidate for candidate in candidates if rank(candidate) == best_rank]
    if len(best_candidates) != 1:
        raise AmbiguousEmissionFactorError("More than one equally applicable emission factor matched; calculation was not performed.")
    factor = best_candidates[0]
    if factor.is_illustrative and not allow_illustrative:
        raise IllustrativeFactorError("Illustrative factors require explicit demo-mode opt-in.")
    return factor


def calculate_emissions(activity: ActivityRecord, factor: EmissionFactor) -> Calculation:
    if activity.validation_status != "valid":
        raise CarbonCalculationError("Only validated activity records can be calculated.")
    if activity.activity_category != factor.activity_category:
        raise CarbonCalculationError("Emission factor category does not match the activity.")
    expected_scope = SUPPORTED_SCOPES.get(activity.activity_category or "")
    if expected_scope is None or activity.scope != expected_scope or factor.scope != expected_scope:
        raise CarbonCalculationError("Activity and emission factor do not share a supported scope mapping.")
    if activity.activity_date is None or not factor.valid_from <= activity.activity_date:
        raise CarbonCalculationError("Activity date is outside the emission factor validity period.")
    if factor.valid_to is not None and activity.activity_date > factor.valid_to:
        raise CarbonCalculationError("Activity date is outside the emission factor validity period.")
    if factor.geography_type == "country" and factor.country_code != activity.country_code:
        raise CarbonCalculationError("Emission factor geography does not match the activity country.")
    if factor.factor_value < 0:
        raise CarbonCalculationError("Emission factor must be non-negative.")
    if activity.quantity is None or activity.unit is None:
        raise CarbonCalculationError("Validated activity record is missing quantity or unit.")

    factor_unit = factor.factor_unit.strip().lower().replace("co₂e", "co2e").replace(" per ", "/").replace(" ", "")
    expected_factor_unit = f"kgco2e/{factor.activity_unit}".lower()
    if factor_unit != expected_factor_unit:
        raise CarbonCalculationError(
            f"Emission factor unit {factor.factor_unit!r} is not a supported kg CO2e per activity unit."
        )
    converted_quantity, ratio = convert_quantity(
        Decimal(activity.quantity),
        activity.unit,
        factor.activity_unit,
    )
    result = converted_quantity * Decimal(factor.factor_value)
    return Calculation(
        activity_record_id=activity.id,
        emission_factor_id=factor.id,
        activity_quantity=Decimal(activity.quantity),
        factor_quantity=converted_quantity,
        factor_value=Decimal(factor.factor_value),
        activity_unit=activity.unit,
        factor_activity_unit=factor.activity_unit,
        co2e_kg=result,
        scope=expected_scope,
        method=factor.method,
        conversion_ratio=ratio,
    )


def calculate_pipeline_activities(
    db: Session,
    pipeline_run: PipelineRun,
    allow_illustrative: bool = False,
    ingestion_run_id: UUID | None = None,
) -> CalculationBatch:
    query = select(ActivityRecord).where(
        ActivityRecord.organization_id == pipeline_run.organization_id,
        ActivityRecord.validation_status == "valid",
        ActivityRecord.activity_date >= pipeline_run.reporting_period_start,
        ActivityRecord.activity_date <= pipeline_run.reporting_period_end,
    )
    if ingestion_run_id is not None:
        query = query.where(ActivityRecord.ingestion_run_id == ingestion_run_id)
    activities = db.scalars(query).all()
    results: list[CalculationResult] = []
    failures: list[dict[str, str | UUID]] = []

    for activity in activities:
        try:
            if activity.activity_category is None or activity.activity_date is None or activity.scope is None:
                raise CarbonCalculationError("Validated activity record is missing category, date or scope.")
            factor = resolve_emission_factor(
                db=db,
                organization_id=pipeline_run.organization_id,
                activity_category=activity.activity_category,
                activity_date=activity.activity_date,
                country_code=activity.country_code,
                scope=activity.scope,
                method=METHOD_BY_SCOPE[activity.scope],
                allow_illustrative=allow_illustrative,
            )
            calculation = calculate_emissions(activity, factor)
        except CarbonCalculationError as error:
            failures.append({"activity_record_id": activity.id, "message": str(error)})
            continue

        existing = db.scalar(
            select(CalculationResult).where(
                CalculationResult.pipeline_run_id == pipeline_run.id,
                CalculationResult.activity_record_id == activity.id,
                CalculationResult.emission_factor_id == factor.id,
                CalculationResult.calculation_version == "1.0",
            )
        )
        if existing is not None:
            results.append(existing)
            continue

        result = CalculationResult(
            id=uuid4(),
            organization_id=pipeline_run.organization_id,
            pipeline_run_id=pipeline_run.id,
            activity_record_id=activity.id,
            emission_factor_id=factor.id,
            calculation_version="1.0",
            activity_quantity=calculation.activity_quantity,
            activity_unit=calculation.activity_unit,
            factor_value=calculation.factor_value,
            factor_unit=factor.factor_unit,
            conversion={
                "from_unit": calculation.activity_unit,
                "to_unit": calculation.factor_activity_unit,
                "ratio": str(calculation.conversion_ratio),
            },
            co2e_kg=calculation.co2e_kg,
            scope=calculation.scope,
            method=calculation.method,
            calculation_breakdown={
                "formula": "activity_quantity_after_conversion * emission_factor",
                "factor_key": factor.factor_key,
                "factor_source": factor.source_name,
                "factor_source_url": factor.source_url,
                "factor_publication_version": factor.publication_version,
                "factor_valid_from": factor.valid_from.isoformat(),
                "factor_valid_to": factor.valid_to.isoformat() if factor.valid_to else None,
                "is_illustrative": factor.is_illustrative,
                "warning": "Demonstration factor; not suitable for organizational reporting." if factor.is_illustrative else None,
            },
            created_at=utc_now(),
        )
        db.add(result)
        db.flush()
        results.append(result)

    return CalculationBatch(results=tuple(results), failures=tuple(failures))


def calculate_what_if(
    db: Session,
    organization_id: UUID,
    activity_category: str,
    current_quantity: Decimal,
    scenario_quantity: Decimal,
    unit: str,
    activity_date: date,
    country_code: str | None,
    allow_illustrative: bool = False,
) -> dict:
    scope = SUPPORTED_SCOPES.get(activity_category)
    if scope is None:
        raise CarbonCalculationError("Activity category does not have a supported scope mapping.")
    factor = resolve_emission_factor(
        db,
        organization_id,
        activity_category,
        activity_date,
        country_code,
        scope,
        METHOD_BY_SCOPE[scope],
        allow_illustrative=allow_illustrative,
    )
    current = ActivityRecord(
        id=uuid4(),
        organization_id=organization_id,
        ingestion_run_id=uuid4(),
        record_fingerprint="what-if-current",
        raw_payload={},
        activity_category=activity_category,
        activity_date=activity_date,
        quantity=current_quantity,
        unit=unit,
        country_code=country_code,
        scope=scope,
        validation_status="valid",
        validation_issues=[],
    )
    scenario = ActivityRecord(
        id=uuid4(),
        organization_id=organization_id,
        ingestion_run_id=uuid4(),
        record_fingerprint="what-if-scenario",
        raw_payload={},
        activity_category=activity_category,
        activity_date=activity_date,
        quantity=scenario_quantity,
        unit=unit,
        country_code=country_code,
        scope=scope,
        validation_status="valid",
        validation_issues=[],
    )
    current_result = calculate_emissions(current, factor)
    scenario_result = calculate_emissions(scenario, factor)
    reduction = current_result.co2e_kg - scenario_result.co2e_kg
    percent_change = (
        (scenario_result.co2e_kg - current_result.co2e_kg) / current_result.co2e_kg * Decimal("100")
        if current_result.co2e_kg
        else Decimal("0")
    )
    return {
        "current_emissions_kg_co2e": current_result.co2e_kg,
        "scenario_emissions_kg_co2e": scenario_result.co2e_kg,
        "estimated_reduction_kg_co2e": reduction,
        "percentage_change": percent_change,
        "factor_id": factor.id,
        "factor_value": factor.factor_value,
        "factor_unit": factor.factor_unit,
        "factor_method": factor.method,
        "factor_source": factor.source_name,
        "factor_source_url": factor.source_url,
        "is_illustrative": factor.is_illustrative,
        "warning": "Scenario estimate only; not an achieved reduction."
        + (" Illustrative factor used." if factor.is_illustrative else ""),
    }
