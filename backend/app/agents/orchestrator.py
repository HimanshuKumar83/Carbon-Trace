from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from typing_extensions import NotRequired

from app.database.base import utc_now
from app.database.session import SessionLocal
from app.models import (
    ActivityRecord,
    AgentRun,
    IngestionRun,
    PipelineRun,
)
from app.services.carbon import calculate_pipeline_activities
from app.services.intelligence import create_insights, detect_pipeline_anomalies
from app.services.reporting import generate_sustainability_report


class PipelineState(TypedDict):
    pipeline_run_id: str
    organization_id: str
    ingestion_run_id: str
    allow_illustrative: bool
    generate_report: bool
    halt: bool
    incomplete_reasons: list[str]
    calculation_failures: list[dict[str, str]]
    calculation_result_ids: list[str]
    insight_ids: list[str]
    anomaly_ids: list[str]
    anomaly_warnings: list[str]
    report_id: NotRequired[str | None]
    orchestrator_agent_run_id: NotRequired[str]


def _start_agent(db, pipeline: PipelineRun, agent_name: str, inputs: dict) -> AgentRun:
    run = AgentRun(
        id=uuid4(),
        organization_id=pipeline.organization_id,
        pipeline_run_id=pipeline.id,
        agent_name=agent_name,
        status="running",
        attempt=max(pipeline.attempt, 1),
        input_summary=inputs,
        output_summary=None,
        is_retryable=False,
        started_at=utc_now(),
        created_at=utc_now(),
    )
    db.add(run)
    db.commit()
    return run


def _finish_agent(db, run: AgentRun, status: str, output: dict) -> None:
    run.status = status
    run.output_summary = output
    run.completed_at = utc_now()
    db.commit()


def _load_pipeline(db, state: PipelineState) -> PipelineRun | None:
    return db.scalar(
        select(PipelineRun).where(
            PipelineRun.id == UUID(state["pipeline_run_id"]),
            PipelineRun.organization_id == UUID(state["organization_id"]),
        )
    )


def _set_stage(db, pipeline: PipelineRun, stage: str) -> None:
    pipeline.current_stage = stage
    pipeline.updated_at = datetime.now(timezone.utc)
    db.commit()


def _orchestrator_start(state: PipelineState) -> dict:
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        pipeline.status = "running"
        pipeline.started_at = utc_now()
        pipeline.attempt += 1
        _set_stage(db, pipeline, "orchestrator")
        agent_run = _start_agent(db, pipeline, "orchestrator", {"ingestion_run_id": state["ingestion_run_id"]})
        return {"orchestrator_agent_run_id": str(agent_run.id)}


def _ingestion_node(state: PipelineState) -> dict:
    if state["halt"]:
        return {}
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        _set_stage(db, pipeline, "ingestion")
        run = _start_agent(db, pipeline, "ingestion", {"ingestion_run_id": state["ingestion_run_id"]})
        ingestion = db.scalar(
            select(IngestionRun).where(
                IngestionRun.id == UUID(state["ingestion_run_id"]),
                IngestionRun.organization_id == pipeline.organization_id,
            )
        )
        if ingestion is None or ingestion.status not in {"succeeded", "partial"}:
            message = "A completed ingestion run for this organization is required."
            _finish_agent(db, run, "failed", {"error": message})
            return {"halt": True, "incomplete_reasons": [message]}
        _finish_agent(db, run, "succeeded", {"ingestion_run_id": str(ingestion.id), "records_seen": ingestion.records_seen})
        if ingestion.status == "partial":
            return {"incomplete_reasons": state["incomplete_reasons"] + ["The ingestion contains records requiring review."]}
        return {}


def _validation_node(state: PipelineState) -> dict:
    if state["halt"]:
        return {}
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        _set_stage(db, pipeline, "validation")
        run = _start_agent(db, pipeline, "validation", {"ingestion_run_id": state["ingestion_run_id"]})
        records = db.scalars(
            select(ActivityRecord).where(
                ActivityRecord.organization_id == pipeline.organization_id,
                ActivityRecord.ingestion_run_id == UUID(state["ingestion_run_id"]),
            )
        ).all()
        valid_count = sum(record.validation_status == "valid" for record in records)
        review_count = len(records) - valid_count
        if valid_count == 0:
            message = "No valid activity records are available for calculation."
            _finish_agent(db, run, "failed", {"record_count": len(records), "review_count": review_count})
            return {"halt": True, "incomplete_reasons": state["incomplete_reasons"] + [message]}
        status = "needs_review" if review_count else "succeeded"
        _finish_agent(db, run, status, {"record_count": len(records), "valid_count": valid_count, "review_count": review_count})
        if review_count:
            return {"incomplete_reasons": state["incomplete_reasons"] + [f"{review_count} activity records need human review."]}
        return {}


def _calculation_node(state: PipelineState) -> dict:
    if state["halt"]:
        return {}
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        _set_stage(db, pipeline, "calculation")
        run = _start_agent(db, pipeline, "calculation", {"period_start": pipeline.reporting_period_start.isoformat(), "period_end": pipeline.reporting_period_end.isoformat()})
        batch = calculate_pipeline_activities(
            db,
            pipeline,
            allow_illustrative=state["allow_illustrative"],
            ingestion_run_id=UUID(state["ingestion_run_id"]),
        )
        db.commit()
        ids = [str(result.id) for result in batch.results]
        failures = [
            {"activity_record_id": str(failure["activity_record_id"]), "message": str(failure["message"])}
            for failure in batch.failures
        ]
        if not ids:
            _finish_agent(db, run, "failed", {"calculated_records": 0, "failures": failures})
            return {
                "halt": True,
                "calculation_failures": failures,
                "incomplete_reasons": state["incomplete_reasons"] + ["No activity records could be calculated with applicable factors."],
            }
        _finish_agent(db, run, "needs_review" if failures else "succeeded", {"calculated_records": len(ids), "failures": failures})
        reasons = state["incomplete_reasons"]
        if failures:
            reasons = reasons + [f"{len(failures)} activity records could not be calculated."]
        return {"calculation_result_ids": ids, "calculation_failures": failures, "incomplete_reasons": reasons}


def _intelligence_node(state: PipelineState) -> dict:
    if state["halt"]:
        return {}
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        _set_stage(db, pipeline, "intelligence")
        run = _start_agent(db, pipeline, "intelligence", {"calculation_count": len(state["calculation_result_ids"])})
        insights = create_insights(db, pipeline)
        anomalies, warnings = detect_pipeline_anomalies(db, pipeline)
        _finish_agent(
            db,
            run,
            "succeeded",
            {"insight_count": len(insights), "anomaly_count": len(anomalies), "anomaly_warnings": warnings},
        )
        return {
            "insight_ids": [str(insight.id) for insight in insights],
            "anomaly_ids": [str(anomaly.id) for anomaly in anomalies],
            "anomaly_warnings": warnings,
        }


def _reporting_node(state: PipelineState) -> dict:
    if state["halt"]:
        return {}
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {"halt": True, "incomplete_reasons": ["Pipeline run was not found."]}
        _set_stage(db, pipeline, "reporting")
        run = _start_agent(db, pipeline, "reporting", {"generate_report": state["generate_report"]})
        if not state["generate_report"]:
            _finish_agent(db, run, "skipped", {"reason": "Report generation was not requested."})
            return {}
        report = generate_sustainability_report(
            db,
            pipeline,
            incomplete=bool(state["incomplete_reasons"]),
        )
        db.commit()
        _finish_agent(db, run, "succeeded", {"report_id": str(report.id), "status": report.status})
        return {"report_id": str(report.id)}


def _finalize_node(state: PipelineState) -> dict:
    with SessionLocal() as db:
        pipeline = _load_pipeline(db, state)
        if pipeline is None:
            return {}
        if state["halt"]:
            pipeline.status = "failed"
        elif state["incomplete_reasons"]:
            pipeline.status = "partial"
        else:
            pipeline.status = "succeeded"
        pipeline.error_summary = {"issues": state["incomplete_reasons"]} if state["incomplete_reasons"] else None
        pipeline.current_stage = "complete"
        pipeline.completed_at = utc_now()
        db.commit()
        orchestrator_run_id = state.get("orchestrator_agent_run_id")
        orchestrator_run = db.get(AgentRun, UUID(orchestrator_run_id)) if orchestrator_run_id else None
        if orchestrator_run is not None:
            _finish_agent(
                db,
                orchestrator_run,
                "failed" if state["halt"] else ("needs_review" if state["incomplete_reasons"] else "succeeded"),
                {"pipeline_status": pipeline.status, "issues": state["incomplete_reasons"]},
            )
        return {}


def build_pipeline_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("orchestrator_start", _orchestrator_start)
    graph.add_node("ingestion", _ingestion_node)
    graph.add_node("validation", _validation_node)
    graph.add_node("calculation", _calculation_node)
    graph.add_node("intelligence", _intelligence_node)
    graph.add_node("reporting", _reporting_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, "orchestrator_start")
    graph.add_edge("orchestrator_start", "ingestion")
    graph.add_edge("ingestion", "validation")
    graph.add_edge("validation", "calculation")
    graph.add_edge("calculation", "intelligence")
    graph.add_edge("intelligence", "reporting")
    graph.add_edge("reporting", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


PIPELINE_GRAPH = build_pipeline_graph()


def execute_pipeline(pipeline_run_id: UUID, ingestion_run_id: UUID, allow_illustrative: bool = False, generate_report: bool = False) -> dict:
    with SessionLocal() as db:
        pipeline = db.get(PipelineRun, pipeline_run_id)
        if pipeline is None:
            raise ValueError("Pipeline run does not exist.")
        initial_state: PipelineState = {
            "pipeline_run_id": str(pipeline_run_id),
            "organization_id": str(pipeline.organization_id),
            "ingestion_run_id": str(ingestion_run_id),
            "allow_illustrative": allow_illustrative,
            "generate_report": generate_report,
            "halt": False,
            "incomplete_reasons": [],
            "calculation_failures": [],
            "calculation_result_ids": [],
            "insight_ids": [],
            "anomaly_ids": [],
            "anomaly_warnings": [],
        }
    return PIPELINE_GRAPH.invoke(initial_state)
