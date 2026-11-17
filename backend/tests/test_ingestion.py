from types import SimpleNamespace


def _create_org(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "ingest@example.org",
            "password": "CarbonTracePass123",
            "display_name": "Ingestion User",
            "organization_name": "Ingestion Org",
        },
    )
    return response.json()["organization"]["id"], response.json()["access_token"]


def test_csv_ingestion_normalizes_and_flags_invalid_rows(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion.get_settings",
        lambda: SimpleNamespace(upload_directory=str(tmp_path)),
    )
    organization_id, token = _create_org(client)
    csv_content = (
        "activity_category,activity_date,quantity,unit,country_code\n"
        "electricity,2026-01-15,12000,kWh,IN\n"
        "diesel,2026-01-20,-2,L,IN\n"
    )
    response = client.post(
        "/api/v1/ingestion/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"organization_id": organization_id, "idempotency_key": "import-1"},
        files={"file": ("activities.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 201
    result = response.json()
    assert result["records_seen"] == 2
    assert result["records_accepted"] == 1
    assert result["records_rejected"] == 1
    assert result["error_summary"]["data_quality_score"] == 50.0
    assert result["status"] == "partial"

    history = client.get(
        "/api/v1/ingestion/history",
        headers={"Authorization": f"Bearer {token}"},
        params={"organization_id": organization_id},
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1


def test_upload_is_idempotent_and_conflicting_reuse_is_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion.get_settings",
        lambda: SimpleNamespace(upload_directory=str(tmp_path)),
    )
    organization_id, token = _create_org(client)
    headers = {"Authorization": f"Bearer {token}"}
    data = {"organization_id": organization_id, "idempotency_key": "same-key"}
    good_file = ("activities.csv", "activity_category,activity_date,quantity,unit\nelectricity,2026-01-15,2,kWh\n", "text/csv")
    first = client.post("/api/v1/ingestion/upload", headers=headers, data=data, files={"file": good_file})
    repeated = client.post("/api/v1/ingestion/upload", headers=headers, data=data, files={"file": good_file})
    conflicting = client.post(
        "/api/v1/ingestion/upload",
        headers=headers,
        data=data,
        files={"file": ("activities.csv", "activity_category,activity_date,quantity,unit\ndiesel,2026-01-15,2,L\n", "text/csv")},
    )
    assert first.status_code == repeated.status_code == 201
    assert first.json()["id"] == repeated.json()["id"]
    assert conflicting.status_code == 409


def test_unsupported_upload_type_is_rejected(client):
    organization_id, token = _create_org(client)
    response = client.post(
        "/api/v1/ingestion/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"organization_id": organization_id},
        files={"file": ("activities.txt", "not a spreadsheet", "text/plain")},
    )
    assert response.status_code == 415


def test_activity_review_accepts_corrected_values_and_preserves_raw_payload(client, tmp_path, monkeypatch):
    from uuid import UUID

    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models import ActivityRecord, AuditLog

    monkeypatch.setattr(
        "app.services.ingestion.get_settings",
        lambda: SimpleNamespace(upload_directory=str(tmp_path)),
    )
    organization_id, token = _create_org(client)
    headers = {"Authorization": f"Bearer {token}"}
    upload = client.post(
        "/api/v1/ingestion/upload",
        headers=headers,
        data={"organization_id": organization_id},
        files={
            "file": (
                "activities.csv",
                "activity_category,activity_date,quantity,unit,country_code\n"
                "diesel,2026-01-20,-2,L,IN\n",
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201

    review_queue = client.get(
        "/api/v1/activities",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert review_queue.status_code == 200
    item = review_queue.json()["items"][0]
    original_payload = item["raw_payload"]

    reviewed = client.patch(
        f"/api/v1/activities/{item['id']}/review",
        headers=headers,
        params={"organization_id": organization_id},
        json={
            "decision": "accept",
            "activity_category": "diesel",
            "activity_date": "2026-01-20",
            "quantity": "2",
            "unit": "L",
            "country_code": "IN",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["validation_status"] == "valid"
    assert reviewed.json()["scope"] == 1
    assert reviewed.json()["raw_payload"] == original_payload
    refreshed_ingestion = client.get(
        f"/api/v1/ingestion/{reviewed.json()['ingestion_run_id']}",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert refreshed_ingestion.status_code == 200
    assert refreshed_ingestion.json()["status"] == "succeeded"
    assert refreshed_ingestion.json()["records_accepted"] == 1
    assert refreshed_ingestion.json()["records_rejected"] == 0

    with SessionLocal() as db:
        activity_id = UUID(item["id"])
        activity = db.scalar(select(ActivityRecord).where(ActivityRecord.id == activity_id))
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.resource_type == "activity_record",
                AuditLog.resource_id == activity_id,
            )
        )
        assert activity is not None
        assert activity.raw_payload == original_payload
        assert audit is not None
        assert audit.action == "activity.review.accept"
        assert audit.metadata_json["corrected_fields"] == ["quantity"]


def test_activity_review_requires_valid_corrections_or_explicit_rejection(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion.get_settings",
        lambda: SimpleNamespace(upload_directory=str(tmp_path)),
    )
    organization_id, token = _create_org(client)
    headers = {"Authorization": f"Bearer {token}"}
    upload = client.post(
        "/api/v1/ingestion/upload",
        headers=headers,
        data={"organization_id": organization_id},
        files={
            "file": (
                "activities.csv",
                "activity_category,activity_date,quantity,unit\n"
                "unknown,not-a-date,1,widgets\n",
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201
    activity = client.get(
        "/api/v1/activities",
        headers=headers,
        params={"organization_id": organization_id},
    ).json()["items"][0]

    invalid_accept = client.patch(
        f"/api/v1/activities/{activity['id']}/review",
        headers=headers,
        params={"organization_id": organization_id},
        json={
            "decision": "accept",
            "activity_category": "electricity",
            "activity_date": "2026-01-20",
            "quantity": "1",
            "unit": "L",
        },
    )
    assert invalid_accept.status_code == 422

    rejected = client.patch(
        f"/api/v1/activities/{activity['id']}/review",
        headers=headers,
        params={"organization_id": organization_id},
        json={"decision": "reject"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["validation_status"] == "rejected"
