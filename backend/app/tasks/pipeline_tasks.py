from uuid import UUID

from app.agents.orchestrator import execute_pipeline
from app.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="carbontrace.execute_pipeline",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def execute_pipeline_task(
    self,
    pipeline_run_id: str,
    ingestion_run_id: str,
    allow_illustrative: bool = False,
    generate_report: bool = False,
) -> dict:
    return execute_pipeline(
        UUID(pipeline_run_id),
        UUID(ingestion_run_id),
        allow_illustrative=allow_illustrative,
        generate_report=generate_report,
    )
