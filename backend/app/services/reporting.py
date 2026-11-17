import hashlib
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.base import utc_now
from app.models import AnomalyRecord, CalculationResult, EmissionFactor, Organization, PipelineRun, Report, SustainabilityInsight
from app.schemas.ai import ReportNarrative
from app.services.ai_persistence import store_interaction
from app.services.gemini import LLMProviderError, generate_structured_response

logger = logging.getLogger(__name__)


def generate_sustainability_report(
    db: Session,
    pipeline_run: PipelineRun,
    incomplete: bool = False,
    report_type: str = "custom",
    requested_by_user_id=None,
) -> Report:
    organization = db.get(Organization, pipeline_run.organization_id)
    if organization is None:
        raise ValueError("Report organization could not be found.")
    calculations = db.scalars(
        select(CalculationResult)
        .where(
            CalculationResult.organization_id == pipeline_run.organization_id,
            CalculationResult.pipeline_run_id == pipeline_run.id,
        )
        .order_by(CalculationResult.scope)
    ).all()
    if not calculations:
        raise ValueError("A report requires at least one persisted calculation result.")
    insights = db.scalars(
        select(SustainabilityInsight).where(
            SustainabilityInsight.organization_id == pipeline_run.organization_id,
            SustainabilityInsight.pipeline_run_id == pipeline_run.id,
        )
    ).all()
    anomalies = db.scalars(
        select(AnomalyRecord).where(
            AnomalyRecord.organization_id == pipeline_run.organization_id,
            AnomalyRecord.pipeline_run_id == pipeline_run.id,
        )
    ).all()
    total = sum((calculation.co2e_kg for calculation in calculations), start=0)
    scope_totals = {scope: sum((c.co2e_kg for c in calculations if c.scope == scope), start=0) for scope in (1, 2, 3)}
    factor_ids = sorted({calculation.emission_factor_id for calculation in calculations}, key=str)
    factors = [db.get(EmissionFactor, factor_id) for factor_id in factor_ids]
    methodology = [
        {
            "factor_id": str(factor.id),
            "factor_key": factor.factor_key,
            "source_name": factor.source_name,
            "source_url": factor.source_url,
            "publication_version": factor.publication_version,
            "is_illustrative": factor.is_illustrative,
        }
        for factor in factors
        if factor is not None
    ]
    settings = get_settings()

    report = Report(
        organization_id=pipeline_run.organization_id,
        pipeline_run_id=pipeline_run.id,
        requested_by_user_id=requested_by_user_id,
        report_type=report_type,
        period_start=pipeline_run.reporting_period_start,
        period_end=pipeline_run.reporting_period_end,
        status="generating",
        calculation_version="1.0",
        methodology_snapshot={
            "factors": methodology,
            "accounting_unit": "kg CO2e",
            "ai_narrative_status": "pending" if settings.llm_enabled else "disabled",
        },
    )
    db.add(report)
    db.flush()

    narrative: ReportNarrative | None = None
    narrative_status = "disabled"
    if settings.llm_enabled:
        evidence = {
            f"factor:{factor['factor_id']}": {
                "kind": "emission_factor_source",
                "source_name": factor["source_name"],
                "source_url": factor["source_url"],
                "publication_version": factor["publication_version"],
            }
            for factor in methodology
        }
        for insight in insights:
            evidence[f"insight:{insight.id}"] = {
                "kind": "persisted_insight",
                "title": insight.title,
                "body": insight.body,
                "evidence": insight.evidence,
            }
        for anomaly in anomalies:
            evidence[f"anomaly:{anomaly.id}"] = {
                "kind": "persisted_ml_anomaly",
                "category": anomaly.activity_category,
                "severity": anomaly.severity,
                "explanation": anomaly.explanation,
                "evidence": anomaly.evidence,
            }
        prompt = (
            "Write a concise executive narrative for this CarbonTrace report using only the supplied persisted values. "
            "Do not recalculate emissions, introduce emission factors, or claim compliance. Do not invent causes or "
            "recommendations. Cite only the allowed evidence IDs. State assumptions and limitations explicitly, "
            "including that results depend on the supplied activity data and listed factor sources.\n\n"
            f"Organization: {organization.name}\n"
            f"Reporting period: {pipeline_run.reporting_period_start} through {pipeline_run.reporting_period_end}\n"
            f"Calculated total (kg CO2e): {total}\n"
            f"Persisted scope totals (kg CO2e): {scope_totals}\n"
            f"Calculation count: {len(calculations)}\n"
            f"Factor provenance: {methodology}\n"
            f"Persisted insights and ML anomalies: "
            f"{[{'title': item.title, 'body': item.body, 'evidence': item.evidence} for item in insights] + [{'category': item.activity_category, 'explanation': item.explanation, 'evidence': item.evidence} for item in anomalies]}\n"
            f"Allowed evidence IDs: {list(evidence)}"
        )
        try:
            narrative = generate_structured_response(prompt, ReportNarrative)
            if not narrative.evidence_ids or not set(narrative.evidence_ids).issubset(evidence):
                raise LLMProviderError("Gemini returned a report narrative without valid supporting evidence.")
            narrative_status = "succeeded"
            narrative_data = narrative.model_dump(mode="json")
            report.methodology_snapshot = {
                **(report.methodology_snapshot or {}),
                "ai_narrative": narrative_data,
                "ai_narrative_status": narrative_status,
            }
            store_interaction(
                db,
                pipeline_run.organization_id,
                requested_by_user_id,
                "report_narrative",
                prompt,
                {
                    **narrative_data,
                    "citations": [
                        {"id": source_id, **evidence[source_id]}
                        for source_id in narrative.evidence_ids
                    ],
                },
                resource_id=report.id,
            )
        except LLMProviderError:
            narrative_status = "unavailable"
            report.methodology_snapshot = {
                **(report.methodology_snapshot or {}),
                "ai_narrative_status": narrative_status,
            }
            logger.exception("Optional report narrative generation failed for report %s.", report.id)

    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm)
    story = [
        Paragraph("CARBONTRACE", styles["Title"]),
        Paragraph("Organizational Carbon Footprint Report", styles["Heading2"]),
        Spacer(1, 8 * mm),
        Paragraph(f"Organization: {organization.name}", styles["BodyText"]),
        Paragraph(
            f"Reporting period: {pipeline_run.reporting_period_start.isoformat()} to {pipeline_run.reporting_period_end.isoformat()}",
            styles["BodyText"],
        ),
    ]
    if incomplete:
        story.extend([Spacer(1, 3 * mm), Paragraph("INCOMPLETE REPORT — one or more records could not be fully processed.", styles["Heading3"])])
    story.extend(
        [
            Spacer(1, 8 * mm),
            Paragraph(f"Total calculated emissions: {total.quantize(Decimal('0.01'))} kg CO2e", styles["Heading2"]),
            Spacer(1, 4 * mm),
        ]
    )
    table_data = [["Scope", "Emissions (kg CO2e)"]] + [
        [f"Scope {scope}", f"{scope_totals[scope]:,.2f}"] for scope in (1, 2, 3)
    ]
    table = Table(table_data, colWidths=[75 * mm, 75 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153f32")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5d1")),
                ("PADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([table, Spacer(1, 8 * mm), Paragraph("Methodology and data provenance", styles["Heading2"])])
    story.append(
        Paragraph(
            "Emissions are calculated deterministically as activity quantity multiplied by an applicable emission factor, "
            "with documented unit conversions. Results are stored in kg CO2e. Scope 2 results use the selected factor method. "
            "This report is not a compliance certification.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 4 * mm))
    for factor in methodology:
        story.append(
            Paragraph(
                escape(
                    f"{factor['factor_key']}: {factor['source_name']} ({factor['publication_version']}); "
                    f"{factor['source_url']}. {'DEMONSTRATION FACTOR — not authoritative.' if factor['is_illustrative'] else ''}"
                ),
                styles["BodyText"],
            )
        )
    if narrative is not None:
        story.extend(
            [
                Spacer(1, 8 * mm),
                Paragraph("AI-assisted executive narrative", styles["Heading2"]),
                Paragraph(escape(narrative.executive_summary), styles["BodyText"]),
            ]
        )
        if narrative.observations:
            story.append(Paragraph("Evidence-based observations", styles["Heading3"]))
            for observation in narrative.observations:
                story.append(Paragraph(escape(observation), styles["BodyText"]))
        if narrative.assumptions:
            story.append(Paragraph("Assumptions", styles["Heading3"]))
            for assumption in narrative.assumptions:
                story.append(Paragraph(escape(assumption), styles["BodyText"]))
        if narrative.limitations:
            story.append(Paragraph("AI narrative limitations", styles["Heading3"]))
            for limitation in narrative.limitations:
                story.append(Paragraph(escape(limitation), styles["BodyText"]))
        if narrative.evidence_ids:
            story.append(Paragraph("Narrative sources", styles["Heading3"]))
            for evidence_id in narrative.evidence_ids:
                source = evidence[evidence_id]
                label = source.get("source_name") or source.get("title") or source.get("category") or evidence_id
                source_url = source.get("source_url")
                reference = f"{label}: {source_url}" if source_url else label
                story.append(Paragraph(escape(reference), styles["BodyText"]))
    if insights:
        story.extend([Spacer(1, 8 * mm), Paragraph("Observed hotspots", styles["Heading2"])])
        for insight in insights:
            story.append(Paragraph(f"<b>{insight.title}</b><br/>{insight.body}", styles["BodyText"]))
    if anomalies:
        story.extend([Spacer(1, 8 * mm), Paragraph("Anomaly findings", styles["Heading2"])])
        for anomaly in anomalies:
            story.append(
                Paragraph(
                    f"<b>{anomaly.activity_category.replace('_', ' ').title()} — {anomaly.severity.title()}</b><br/>"
                    f"{anomaly.explanation} Method: {anomaly.method}.",
                    styles["BodyText"],
                )
            )

    document.build(story)
    pdf_content = buffer.getvalue()
    buffer.close()

    report_root = Path(settings.report_directory).resolve()
    report_directory = report_root / str(pipeline_run.organization_id)
    report_directory.mkdir(parents=True, exist_ok=True)
    target_path = report_directory / f"{report.id}.pdf"
    target_path.write_bytes(pdf_content)
    report.storage_key = str(target_path)
    report.content_sha256 = hashlib.sha256(pdf_content).hexdigest()
    report.status = "incomplete" if incomplete else "complete"
    report.updated_at = datetime.now(timezone.utc)
    db.flush()
    return report
