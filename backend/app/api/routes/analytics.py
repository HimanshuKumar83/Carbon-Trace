from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.models import ActivityRecord, CalculationResult, IngestionRun

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _calculation_rows(db: DbSession, organization_id: UUID, start: date | None, end: date | None):
    query = (
        select(
            ActivityRecord.id.label("activity_id"),
            ActivityRecord.activity_date.label("activity_date"),
            ActivityRecord.activity_category.label("activity_category"),
            CalculationResult.scope.label("scope"),
            CalculationResult.co2e_kg.label("co2e_kg"),
            CalculationResult.id.label("calculation_id"),
            CalculationResult.created_at.label("result_created_at"),
        )
        .join(CalculationResult, CalculationResult.activity_record_id == ActivityRecord.id)
        .where(CalculationResult.organization_id == organization_id)
    )
    if start:
        query = query.where(ActivityRecord.activity_date >= start)
    if end:
        query = query.where(ActivityRecord.activity_date <= end)
    latest: dict[UUID, tuple] = {}
    for row in db.execute(query).all():
        existing = latest.get(row.activity_id)
        if existing is None or (row.result_created_at, str(row.calculation_id)) > (existing[5], str(existing[4])):
            latest[row.activity_id] = (
                row.activity_date,
                row.activity_category,
                row.scope,
                row.co2e_kg,
                row.calculation_id,
                row.result_created_at,
            )
    return [row[:4] for row in latest.values()]


def _check_period(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="The end date cannot precede the start date.")


def _serialize_period(start: date | None, end: date | None) -> dict:
    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "unit": "kg CO2e",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/overview")
def overview(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    require_membership(organization_id, user, db)
    _check_period(start, end)
    rows = _calculation_rows(db, organization_id, start, end)
    scope_totals = {scope: sum((Decimal(row[3]) for row in rows if row[2] == scope), Decimal("0")) for scope in (1, 2, 3)}
    total = sum(scope_totals.values(), Decimal("0"))
    latest_ingestion = db.scalar(
        select(IngestionRun)
        .where(IngestionRun.organization_id == organization_id)
        .order_by(IngestionRun.created_at.desc())
        .limit(1)
    )
    quality_score = latest_ingestion.error_summary.get("data_quality_score") if latest_ingestion and latest_ingestion.error_summary else None
    return {
        **_serialize_period(start, end),
        "total_kg_co2e": total,
        "total_tonnes_co2e": total / Decimal("1000"),
        "scope_1_kg_co2e": scope_totals[1],
        "scope_2_kg_co2e": scope_totals[2],
        "scope_3_kg_co2e": scope_totals[3],
        "calculation_count": len(rows),
        "data_quality_score": quality_score,
        "has_data": bool(rows),
    }


@router.get("/scope-breakdown")
def scope_breakdown(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    require_membership(organization_id, user, db)
    _check_period(start, end)
    rows = _calculation_rows(db, organization_id, start, end)
    totals = {scope: sum((Decimal(row[3]) for row in rows if row[2] == scope), Decimal("0")) for scope in (1, 2, 3)}
    return {**_serialize_period(start, end), "items": [{"scope": scope, "kg_co2e": value} for scope, value in totals.items()]}


@router.get("/category-breakdown")
def category_breakdown(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    require_membership(organization_id, user, db)
    _check_period(start, end)
    rows = _calculation_rows(db, organization_id, start, end)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row[1]:
            totals[row[1]] += Decimal(row[3])
    return {
        **_serialize_period(start, end),
        "items": [{"category": key, "kg_co2e": value} for key, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)],
    }


@router.get("/hotspots")
def hotspots(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    breakdown = category_breakdown(organization_id, user, db, start, end)
    total = sum((item["kg_co2e"] for item in breakdown["items"]), Decimal("0"))
    return {
        **_serialize_period(start, end),
        "items": [
            {
                **item,
                "share_percent": (item["kg_co2e"] / total * Decimal("100")) if total else Decimal("0"),
            }
            for item in breakdown["items"][:5]
        ],
    }


@router.get("/emissions-trend")
def emissions_trend(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    require_membership(organization_id, user, db)
    _check_period(start, end)
    rows = _calculation_rows(db, organization_id, start, end)
    monthly: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row[0] is not None:
            key = row[0].strftime("%Y-%m")
            monthly[key] += Decimal(row[3])
    return {
        **_serialize_period(start, end),
        "items": [{"month": month, "kg_co2e": value} for month, value in sorted(monthly.items())],
    }
