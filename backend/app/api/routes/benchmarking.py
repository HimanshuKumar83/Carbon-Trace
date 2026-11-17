from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.models import BenchmarkDataset
from app.services.owid import (
    BenchmarkDataUnavailableError,
    fetch_owid_dataset,
    get_country_trends,
    list_benchmark_countries,
)

router = APIRouter(prefix="/benchmarking", tags=["benchmarking"])


@router.post("/refresh", status_code=status.HTTP_201_CREATED)
def refresh_dataset(organization_id: UUID, user: CurrentUser, db: DbSession) -> dict:
    require_membership(organization_id, user, db, {"owner", "admin"})
    try:
        dataset = fetch_owid_dataset(db)
    except BenchmarkDataUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return {
        "id": str(dataset.id),
        "dataset_sha256": dataset.dataset_sha256,
        "row_count": dataset.row_count,
        "retrieved_at": dataset.retrieved_at.isoformat(),
        "source_url": dataset.source_url,
    }


@router.get("/countries")
def countries(organization_id: UUID, user: CurrentUser, db: DbSession) -> dict:
    require_membership(organization_id, user, db)
    try:
        dataset, country_items = list_benchmark_countries(db)
    except BenchmarkDataUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    return {
        "source_repository": "https://github.com/owid/co2-data",
        "dataset_sha256": dataset.dataset_sha256,
        "retrieved_at": dataset.retrieved_at.isoformat(),
        "items": country_items,
    }


@router.get("/{country}/trends")
def country_trends(
    country: str,
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    year_start: int | None = Query(default=None, ge=1750, le=2200),
    year_end: int | None = Query(default=None, ge=1750, le=2200),
) -> dict:
    require_membership(organization_id, user, db)
    if year_start is not None and year_end is not None and year_end < year_start:
        raise HTTPException(status_code=422, detail="year_end cannot precede year_start.")
    try:
        return get_country_trends(db, country, year_start, year_end)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except BenchmarkDataUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
