from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "carbontrace",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
    include=["app.tasks.pipeline_tasks", "app.tasks.benchmark_tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "refresh-owid-benchmark-weekly": {
            "task": "carbontrace.refresh_owid_dataset",
            "schedule": crontab(day_of_week="sun", hour=2, minute=0),
        },
    },
)
