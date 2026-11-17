from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import activities, analytics, auth, benchmarking, emission_factors, ingestion, intelligence, knowledge, organizations, pipeline, reports
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Organizational carbon accounting and sustainability intelligence.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Organization-ID"],
)

api_router_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_router_prefix)
app.include_router(organizations.router, prefix=api_router_prefix)
app.include_router(ingestion.router, prefix=api_router_prefix)
app.include_router(activities.router, prefix=api_router_prefix)
app.include_router(emission_factors.router, prefix=api_router_prefix)
app.include_router(pipeline.router, prefix=api_router_prefix)
app.include_router(reports.router, prefix=api_router_prefix)
app.include_router(analytics.router, prefix=api_router_prefix)
app.include_router(benchmarking.router, prefix=api_router_prefix)
app.include_router(intelligence.router, prefix=api_router_prefix)
app.include_router(knowledge.router, prefix=api_router_prefix)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    from sqlalchemy import text

    from app.database.session import engine

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
