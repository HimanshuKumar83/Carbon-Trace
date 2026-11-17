from app.database.session import SessionLocal
from app.services.owid import fetch_owid_dataset
from app.tasks.celery_app import celery_app


@celery_app.task(name="carbontrace.refresh_owid_dataset")
def refresh_owid_dataset_task() -> dict[str, int | str]:
    with SessionLocal() as db:
        dataset = fetch_owid_dataset(db)
        return {
            "dataset_sha256": dataset.dataset_sha256,
            "row_count": dataset.row_count,
            "retrieved_at": dataset.retrieved_at.isoformat(),
        }
