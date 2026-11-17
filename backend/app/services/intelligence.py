from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import uuid4

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session
from sklearn.ensemble import IsolationForest

from app.database.base import utc_now
from app.models import ActivityRecord, AnomalyRecord, CalculationResult, PipelineRun, SustainabilityInsight


def create_insights(db: Session, pipeline_run: PipelineRun) -> list[SustainabilityInsight]:
    rows = db.execute(
        select(ActivityRecord.activity_category, CalculationResult.co2e_kg, CalculationResult.id)
        .join(CalculationResult, CalculationResult.activity_record_id == ActivityRecord.id)
        .where(
            CalculationResult.pipeline_run_id == pipeline_run.id,
            CalculationResult.organization_id == pipeline_run.organization_id,
        )
    ).all()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    evidence_ids: dict[str, list[str]] = defaultdict(list)
    for category, emissions, result_id in rows:
        if category is not None:
            totals[category] += Decimal(emissions)
            evidence_ids[category].append(str(result_id))
    overall = sum(totals.values(), Decimal("0"))
    if overall <= 0:
        return []

    insights: list[SustainabilityInsight] = []
    for category, emissions in sorted(totals.items(), key=lambda entry: entry[1], reverse=True)[:3]:
        share = emissions / overall * Decimal("100")
        title = f"{category.replace('_', ' ').title()} emissions hotspot"
        body = (
            f"{category.replace('_', ' ').title()} contributed {share.quantize(Decimal('0.1'))}% "
            f"({emissions.quantize(Decimal('0.01'))} kg CO2e) of calculated emissions in this pipeline. "
            "Review the underlying activity records and operational drivers before selecting a reduction action."
        )
        insight = SustainabilityInsight(
            id=uuid4(),
            organization_id=pipeline_run.organization_id,
            pipeline_run_id=pipeline_run.id,
            insight_type="hotspot",
            title=title,
            body=body,
            evidence={
                "activity_category": category,
                "emissions_kg_co2e": str(emissions),
                "share_percent": str(share),
                "calculation_result_ids": evidence_ids[category],
            },
            confidence=None,
            generation_method="deterministic",
            created_at=utc_now(),
        )
        db.add(insight)
        insights.append(insight)
    db.flush()
    return insights


def detect_pipeline_anomalies(db: Session, pipeline_run: PipelineRun) -> tuple[list[AnomalyRecord], list[str]]:
    current_rows = db.execute(
        select(ActivityRecord.activity_category, ActivityRecord.activity_date, CalculationResult.co2e_kg)
        .join(CalculationResult, CalculationResult.activity_record_id == ActivityRecord.id)
        .where(
            CalculationResult.pipeline_run_id == pipeline_run.id,
            CalculationResult.organization_id == pipeline_run.organization_id,
        )
    ).all()
    observed: dict[str, Decimal] = defaultdict(Decimal)
    for category, activity_date, value in current_rows:
        if category and activity_date:
            observed[category] += Decimal(value)
    if not observed:
        return [], ["No current-period calculation results are available for anomaly detection."]
    if (pipeline_run.reporting_period_end - pipeline_run.reporting_period_start).days > 35:
        return [], ["Anomaly detection currently compares monthly reporting periods only."]

    history_rows = db.execute(
        select(
            ActivityRecord.id,
            ActivityRecord.activity_category,
            ActivityRecord.activity_date,
            CalculationResult.co2e_kg,
            CalculationResult.created_at,
        )
        .join(CalculationResult, CalculationResult.activity_record_id == ActivityRecord.id)
        .where(
            CalculationResult.organization_id == pipeline_run.organization_id,
            ActivityRecord.activity_date < pipeline_run.reporting_period_start,
        )
        .order_by(CalculationResult.created_at.desc())
    ).all()
    latest_by_activity: dict[str, tuple[str, date, Decimal]] = {}
    for record_id, category, activity_date, value, _created_at in history_rows:
        latest_by_activity.setdefault(
            str(record_id),
            (category, activity_date, Decimal(value)),
        )
    monthly_totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for category, activity_date, amount in latest_by_activity.values():
        if category and activity_date:
            monthly_totals[category][activity_date.strftime("%Y-%m")] += amount

    records: list[AnomalyRecord] = []
    insufficient: list[str] = []
    period_start = pipeline_run.reporting_period_start
    for category, value in observed.items():
        historic_months = monthly_totals.get(category, {})
        history = [
            amount
            for month, amount in sorted(historic_months.items())
            if month < period_start.strftime("%Y-%m")
        ]
        if len(history) < 6:
            insufficient.append(f"{category}: requires six prior monthly observations; found {len(history)}.")
            continue

        history_array = np.asarray([float(amount) for amount in history], dtype=float)
        median = float(np.median(history_array))
        mad = float(np.median(np.abs(history_array - median)))
        observed_float = float(value)
        robust_z = (0.6745 * (observed_float - median) / mad) if mad > 0 else None
        method = "median_mad"
        is_anomaly = robust_z is not None and abs(robust_z) >= 3.5
        if len(history) >= 12:
            model = IsolationForest(random_state=42, contamination="auto", n_estimators=100)
            model.fit(history_array.reshape(-1, 1))
            model_prediction = int(model.predict(np.asarray([[observed_float]]))[0])
            if model_prediction == -1:
                method = "isolation_forest"
                is_anomaly = True
        if not is_anomaly:
            continue

        deviation = observed_float - median
        relative_change = abs(deviation) / max(abs(median), 1e-9)
        severity = "high" if relative_change >= 1 else ("medium" if relative_change >= 0.5 else "low")
        direction = "increased" if deviation > 0 else "decreased"
        existing = db.scalar(
            select(AnomalyRecord).where(
                AnomalyRecord.pipeline_run_id == pipeline_run.id,
                AnomalyRecord.organization_id == pipeline_run.organization_id,
                AnomalyRecord.activity_category == category,
            )
        )
        if existing is not None:
            records.append(existing)
            continue
        record = AnomalyRecord(
            id=uuid4(),
            organization_id=pipeline_run.organization_id,
            pipeline_run_id=pipeline_run.id,
            activity_category=category,
            period_start=pipeline_run.reporting_period_start,
            period_end=pipeline_run.reporting_period_end,
            observed_value=value,
            baseline_value=Decimal(str(median)),
            deviation=Decimal(str(deviation)),
            method=method,
            severity=severity,
            explanation=(
                f"{category.replace('_', ' ').title()} emissions {direction} by "
                f"{abs(deviation):.2f} kg CO2e compared with the historical median "
                f"of {median:.2f} kg CO2e across {len(history)} prior monthly observations."
            ),
            evidence={
                "historical_month_count": len(history),
                "historical_median_kg_co2e": median,
                "historical_mad_kg_co2e": mad,
                "robust_z_score": robust_z,
                "detection_method": method,
            },
            created_at=utc_now(),
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records, insufficient
