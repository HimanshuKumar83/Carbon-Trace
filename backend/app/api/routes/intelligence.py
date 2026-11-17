import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.agents.sustainability_agent import run_sustainability_agent
from app.api.dependencies import CurrentUser, DbSession, require_membership
from app.models import AIInteraction, AnomalyRecord, SustainabilityInsight
from app.schemas.ai import (
    AIInteractionHistoryItem,
    AskRequest,
    AskResponse,
    RecommendationRequest,
    RecommendationResponse,
)
from app.schemas.intelligence import AnomalyResponse, InsightResponse, WhatIfRequest
from app.services.carbon import CarbonCalculationError, calculate_what_if
from app.services.ai_persistence import store_interaction
from app.services.gemini import LLMProviderError, LLMUnavailableError
from app.services.knowledge import KnowledgeStoreError

router = APIRouter(prefix="/intelligence", tags=["intelligence"])
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=AskResponse)
def ask_intelligence(payload: AskRequest, user: CurrentUser, db: DbSession) -> AskResponse:
    require_membership(payload.organization_id, user, db)
    try:
        output = run_sustainability_agent(
            db,
            payload.organization_id,
            payload.question,
            payload.period_start,
            payload.period_end,
        )
    except LLMUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KnowledgeStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LLMProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        logger.exception("Grounded assistant returned an invalid evidence reference.")
        raise HTTPException(status_code=502, detail="The assistant could not produce a grounded response.") from error
    interaction = store_interaction(
        db,
        payload.organization_id,
        user.id,
        "assistant",
        payload.question,
        output,
    )
    db.commit()
    return AskResponse(
        interaction_id=interaction.id,
        answer=output["answer"],
        citations=output["citations"],
        limitations=output["limitations"],
        period_start=payload.period_start,
        period_end=payload.period_end,
        provider=interaction.provider,
        model=interaction.model,
    )


@router.post("/recommendations", response_model=RecommendationResponse)
def generate_recommendations(
    payload: RecommendationRequest,
    user: CurrentUser,
    db: DbSession,
) -> RecommendationResponse:
    require_membership(payload.organization_id, user, db)
    question = (
        "Provide evidence-based sustainability recommendations for the organization's persisted emissions, "
        "including relevant energy, scope, and category guidance."
    )
    try:
        output = run_sustainability_agent(
            db,
            payload.organization_id,
            question,
            payload.period_start,
            payload.period_end,
            mode="recommendations",
        )
    except LLMUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KnowledgeStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except LLMProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        logger.exception("Recommendation agent returned an invalid evidence reference.")
        raise HTTPException(status_code=502, detail="The agent could not produce evidence-linked recommendations.") from error
    interaction = store_interaction(
        db,
        payload.organization_id,
        user.id,
        "recommendations",
        question,
        output,
    )
    db.commit()
    return RecommendationResponse(
        interaction_id=interaction.id,
        recommendations=output["recommendations"],
        citations=output["citations"],
        limitations=output["limitations"],
        provider=interaction.provider,
        model=interaction.model,
    )


@router.get("/history", response_model=list[AIInteractionHistoryItem])
def list_ai_interactions(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> list[AIInteractionHistoryItem]:
    require_membership(organization_id, user, db)
    records = db.scalars(
        select(AIInteraction)
        .where(AIInteraction.organization_id == organization_id)
        .order_by(AIInteraction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [AIInteractionHistoryItem.model_validate(record) for record in records]


@router.get("/anomalies", response_model=list[AnomalyResponse])
def list_anomalies(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    start: date | None = None,
    end: date | None = None,
    severity: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> list[AnomalyResponse]:
    require_membership(organization_id, user, db)
    query = select(AnomalyRecord).where(AnomalyRecord.organization_id == organization_id)
    if start:
        query = query.where(AnomalyRecord.period_end >= start)
    if end:
        query = query.where(AnomalyRecord.period_start <= end)
    if severity:
        if severity not in {"low", "medium", "high"}:
            raise HTTPException(status_code=422, detail="Unsupported anomaly severity.")
        query = query.where(AnomalyRecord.severity == severity)
    records = db.scalars(
        query.order_by(AnomalyRecord.period_start.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [AnomalyResponse.model_validate(record) for record in records]


@router.get("/insights", response_model=list[InsightResponse])
def list_insights(
    organization_id: UUID,
    user: CurrentUser,
    db: DbSession,
    insight_type: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> list[InsightResponse]:
    require_membership(organization_id, user, db)
    query = select(SustainabilityInsight).where(SustainabilityInsight.organization_id == organization_id)
    if insight_type:
        query = query.where(SustainabilityInsight.insight_type == insight_type)
    records = db.scalars(
        query.order_by(SustainabilityInsight.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [InsightResponse.model_validate(record) for record in records]


@router.post("/what-if")
def what_if(payload: WhatIfRequest, user: CurrentUser, db: DbSession) -> dict:
    require_membership(payload.organization_id, user, db)
    if payload.country_code:
        payload.country_code = payload.country_code.upper()
    try:
        estimate = calculate_what_if(
            db=db,
            organization_id=payload.organization_id,
            activity_category=payload.activity_category,
            current_quantity=payload.current_quantity,
            scenario_quantity=payload.scenario_quantity,
            unit=payload.unit,
            activity_date=payload.activity_date,
            country_code=payload.country_code,
            allow_illustrative=payload.allow_illustrative,
        )
    except CarbonCalculationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"scenario_type": "estimate", **estimate}
