from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base, utc_now
from app.models import (
    ActivityRecord,
    CalculationResult,
    DataSource,
    EmissionFactor,
    IngestionRun,
    Organization,
    PipelineRun,
)
from app.services.intelligence import detect_pipeline_anomalies


def test_isolation_forest_flags_outlier_only_after_sufficient_history():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="History Org", timezone="UTC")
        db.add(organization)
        db.flush()
        source = DataSource(
            organization_id=organization.id,
            name="test",
            source_type="csv_upload",
            configuration={},
            is_enabled=True,
        )
        db.add(source)
        db.flush()
        ingestion = IngestionRun(
            organization_id=organization.id,
            data_source_id=source.id,
            status="succeeded",
            idempotency_key="history",
            records_seen=13,
            records_accepted=13,
            records_rejected=0,
        )
        factor = EmissionFactor(
            factor_key="electricity-test",
            activity_category="electricity",
            scope=2,
            geography_type="global",
            activity_unit="kWh",
            factor_value=Decimal("1"),
            factor_unit="kg CO2e/kWh",
            method="location_based",
            valid_from=date(2020, 1, 1),
            source_name="test",
            source_url="https://example.org",
            publication_version="test",
            methodology="Test methodology.",
        )
        db.add_all([ingestion, factor])
        db.flush()

        for month in range(1, 13):
            year = 2024 if month <= 6 else 2025
            month_number = month + 6 if month <= 6 else month - 6
            period_start = date(year, month_number, 1)
            period_end = date(year, month_number, 28)
            pipeline = PipelineRun(
                organization_id=organization.id,
                trigger_type="manual",
                reporting_period_start=period_start,
                reporting_period_end=period_end,
                status="succeeded",
                idempotency_key=f"history-{month}",
                state_summary={},
            )
            db.add(pipeline)
            db.flush()
            activity = ActivityRecord(
                organization_id=organization.id,
                ingestion_run_id=ingestion.id,
                record_fingerprint=f"history-{month}",
                raw_payload={},
                activity_category="electricity",
                activity_date=period_start,
                quantity=Decimal(str(95 + month)),
                unit="kWh",
                country_code="IN",
                scope=2,
                validation_status="valid",
                validation_issues=[],
            )
            db.add(activity)
            db.flush()
            db.add(
                CalculationResult(
                    id=uuid4(),
                    organization_id=organization.id,
                    pipeline_run_id=pipeline.id,
                    activity_record_id=activity.id,
                    emission_factor_id=factor.id,
                    calculation_version="1.0",
                    activity_quantity=Decimal(str(95 + month)),
                    activity_unit="kWh",
                    factor_value=Decimal("1"),
                    factor_unit="kg CO2e/kWh",
                    co2e_kg=Decimal(str(95 + month)),
                    scope=2,
                    method="location_based",
                    calculation_breakdown={},
                    created_at=utc_now(),
                )
            )

        current_pipeline = PipelineRun(
            organization_id=organization.id,
            trigger_type="manual",
            reporting_period_start=date(2025, 7, 1),
            reporting_period_end=date(2025, 7, 31),
            status="running",
            idempotency_key="current",
            state_summary={},
        )
        db.add(current_pipeline)
        db.flush()
        current_activity = ActivityRecord(
            organization_id=organization.id,
            ingestion_run_id=ingestion.id,
            record_fingerprint="current",
            raw_payload={},
            activity_category="electricity",
            activity_date=date(2025, 7, 15),
            quantity=Decimal("1000"),
            unit="kWh",
            country_code="IN",
            scope=2,
            validation_status="valid",
            validation_issues=[],
        )
        db.add(current_activity)
        db.flush()
        db.add(
            CalculationResult(
                id=uuid4(),
                organization_id=organization.id,
                pipeline_run_id=current_pipeline.id,
                activity_record_id=current_activity.id,
                emission_factor_id=factor.id,
                calculation_version="1.0",
                activity_quantity=Decimal("1000"),
                activity_unit="kWh",
                factor_value=Decimal("1"),
                factor_unit="kg CO2e/kWh",
                co2e_kg=Decimal("1000"),
                scope=2,
                method="location_based",
                calculation_breakdown={},
                created_at=utc_now(),
            )
        )
        db.flush()

        anomalies, warnings = detect_pipeline_anomalies(db, current_pipeline)
        assert not warnings
        assert len(anomalies) == 1
        assert anomalies[0].method == "isolation_forest"
        assert anomalies[0].severity == "high"
    engine.dispose()
