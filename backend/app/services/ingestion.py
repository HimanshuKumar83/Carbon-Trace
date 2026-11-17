import hashlib
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActivityRecord, DataSource, IngestionRun

HEADER_ALIASES = {
    "activity_category": {"activity_category", "category", "activity", "source_category"},
    "activity_date": {"activity_date", "date", "period", "transaction_date"},
    "quantity": {"quantity", "amount", "consumption", "value"},
    "unit": {"unit", "units", "activity_unit"},
    "country_code": {"country_code", "country", "geography"},
}
CATEGORY_ALIASES = {
    "electricity": "electricity",
    "purchased_electricity": "electricity",
    "natural_gas": "natural_gas",
    "gas": "natural_gas",
    "diesel": "diesel",
    "diesel_consumption": "diesel",
    "petrol": "petrol",
    "gasoline": "petrol",
    "company_owned_vehicle_fuel": "company_vehicle_fuel",
    "company_vehicle_fuel": "company_vehicle_fuel",
    "business_travel": "business_travel",
    "employee_commuting": "employee_commuting",
    "purchased_goods_and_services": "purchased_goods_services",
    "purchased_goods_services": "purchased_goods_services",
}
UNIT_ALIASES = {
    "kwh": "kWh",
    "mwh": "MWh",
    "therm": "therm",
    "therms": "therm",
    "m3": "m3",
    "l": "L",
    "liter": "L",
    "litre": "L",
    "liters": "L",
    "litres": "L",
    "gal": "gal",
    "gallon": "gal",
    "gallons": "gal",
    "km": "km",
    "passenger_km": "passenger_km",
    "kg": "kg",
    "tonne": "tonne",
    "tonnes": "tonne",
    "t": "tonne",
}
CATEGORY_UNITS = {
    "electricity": {"kWh", "MWh"},
    "natural_gas": {"kWh", "MWh", "therm", "m3"},
    "diesel": {"L", "gal"},
    "petrol": {"L", "gal"},
    "company_vehicle_fuel": {"L", "gal"},
    "business_travel": {"km", "passenger_km"},
    "employee_commuting": {"km", "passenger_km"},
    "purchased_goods_services": {"kg", "tonne"},
}
CATEGORY_SCOPE = {
    "electricity": 2,
    "natural_gas": 1,
    "diesel": 1,
    "petrol": 1,
    "company_vehicle_fuel": 1,
    "business_travel": 3,
    "employee_commuting": 3,
    "purchased_goods_services": 3,
}


def _normalized_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _read_rows(filename: str, content: bytes) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    elif suffix == ".xlsx":
        frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl")
    else:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only CSV and XLSX files are supported.")
    if frame.empty:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file contains no activity rows.")

    rename: dict[Any, str] = {}
    for column in frame.columns:
        normalized = _normalized_header(column)
        canonical = next((field for field, aliases in HEADER_ALIASES.items() if normalized in aliases), None)
        if canonical is not None:
            rename[column] = canonical
    frame = frame.rename(columns=rename)
    required = {"activity_category", "activity_date", "quantity", "unit"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Required activity columns are missing.", "missing_columns": missing},
        )

    rows = frame.to_dict(orient="records")
    return [
        {
            str(key): (None if pd.isna(value) or value == "" else str(value).strip())
            for key, value in row.items()
        }
        for row in rows
    ]


def validate_activity_row(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    category_source = str(raw.get("activity_category") or "").strip().lower()
    category_key = _normalized_header(category_source)
    category = CATEGORY_ALIASES.get(category_key)
    if category is None:
        issues.append({"field": "activity_category", "code": "UNSUPPORTED_CATEGORY", "message": "Activity category is not supported."})

    parsed_date = pd.to_datetime(raw.get("activity_date"), errors="coerce", utc=True)
    activity_date = None if pd.isna(parsed_date) else parsed_date.date()
    if activity_date is None:
        issues.append({"field": "activity_date", "code": "INVALID_DATE", "message": "A valid activity date is required."})

    try:
        quantity = Decimal(str(raw.get("quantity", "")).strip())
        if not quantity.is_finite():
            raise InvalidOperation
        if quantity.adjusted() >= 12 or quantity.as_tuple().exponent < -8:
            raise InvalidOperation
        if quantity < 0:
            issues.append({"field": "quantity", "code": "NEGATIVE_QUANTITY", "message": "Activity quantity cannot be negative."})
    except (InvalidOperation, ValueError):
        quantity = None
        issues.append({"field": "quantity", "code": "INVALID_QUANTITY", "message": "Quantity must be a finite number."})

    unit_raw = str(raw.get("unit") or "").strip().lower()
    unit = UNIT_ALIASES.get(unit_raw)
    if unit is None:
        issues.append({"field": "unit", "code": "UNSUPPORTED_UNIT", "message": "Activity unit is not supported."})
    elif category is not None and unit not in CATEGORY_UNITS[category]:
        issues.append({"field": "unit", "code": "UNIT_CATEGORY_MISMATCH", "message": "Unit does not apply to this activity category."})

    country = raw.get("country_code")
    country_code = str(country).strip().upper() if country else None
    if country_code and not re.fullmatch(r"[A-Z]{2}", country_code):
        issues.append({"field": "country_code", "code": "INVALID_COUNTRY_CODE", "message": "Country must be a two-letter ISO code."})

    return {
        "activity_category": category,
        "activity_date": activity_date,
        "quantity": quantity,
        "unit": unit,
        "country_code": country_code,
        "scope": CATEGORY_SCOPE.get(category) if category else None,
    }, issues


def ingest_activity_file(
    db: Session,
    organization_id: UUID,
    user_id: UUID,
    filename: str,
    content: bytes,
    idempotency_key: str | None,
) -> tuple[IngestionRun, bool]:
    source_digest = hashlib.sha256(content).hexdigest()
    source_type = "excel_upload" if Path(filename).suffix.lower() == ".xlsx" else "csv_upload"
    key = idempotency_key or source_digest
    rows = _read_rows(filename, content)
    source = db.scalar(
        select(DataSource).where(
            DataSource.organization_id == organization_id,
            DataSource.source_type == source_type,
            DataSource.is_enabled.is_(True),
        )
    )
    if source is None:
        source = DataSource(
            organization_id=organization_id,
            name="Manual Excel uploads" if source_type == "excel_upload" else "Manual CSV uploads",
            source_type=source_type,
            configuration={},
            is_enabled=True,
            created_by_user_id=user_id,
        )
        db.add(source)
        db.flush()

    existing = db.scalar(
        select(IngestionRun).where(
            IngestionRun.organization_id == organization_id,
            IngestionRun.data_source_id == source.id,
            IngestionRun.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.source_sha256 != source_digest:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was already used for a different file.")
        return existing, True

    settings = get_settings()
    safe_filename = f"{uuid4().hex}{Path(filename).suffix.lower()}"
    organization_directory = Path(settings.upload_directory).resolve() / str(organization_id)
    organization_directory.mkdir(parents=True, exist_ok=True)
    storage_path = organization_directory / safe_filename
    storage_path.write_bytes(content)

    run = IngestionRun(
        organization_id=organization_id,
        data_source_id=source.id,
        requested_by_user_id=user_id,
        status="running",
        idempotency_key=key,
        source_name=Path(filename).name[:255],
        source_sha256=source_digest,
        storage_key=str(storage_path),
        started_at=datetime.now(timezone.utc),
        records_seen=len(rows),
    )
    db.add(run)
    db.flush()

    duplicates: set[str] = set()
    accepted = 0
    rejected = 0
    for row_number, raw in enumerate(rows, start=1):
        normalized, issues = validate_activity_row(raw)
        content_fingerprint = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if content_fingerprint in duplicates:
            issues.append({"field": "record", "code": "DUPLICATE_ROW", "message": "This activity row duplicates an earlier row in the same file."})
        duplicates.add(content_fingerprint)

        fingerprint = hashlib.sha256(f"{run.id}:{row_number}:{content_fingerprint}".encode("utf-8")).hexdigest()
        valid = not issues
        activity = ActivityRecord(
            organization_id=organization_id,
            ingestion_run_id=run.id,
            source_record_key=str(row_number),
            record_fingerprint=fingerprint,
            raw_payload=raw,
            activity_category=normalized["activity_category"],
            activity_date=normalized["activity_date"],
            quantity=normalized["quantity"],
            unit=normalized["unit"],
            country_code=normalized["country_code"],
            scope=normalized["scope"],
            validation_status="valid" if valid else "needs_review",
            validation_issues=issues,
        )
        db.add(activity)
        if valid:
            accepted += 1
        else:
            rejected += 1

    run.records_accepted = accepted
    run.records_rejected = rejected
    run.status = "succeeded" if rejected == 0 else ("partial" if accepted > 0 else "failed")
    run.completed_at = datetime.now(timezone.utc)
    run.error_summary = {
        "data_quality_score": round(accepted / len(rows) * 100, 2),
        "invalid_records": rejected,
    }
    db.commit()
    db.refresh(run)
    return run, False
