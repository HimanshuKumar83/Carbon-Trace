from types import SimpleNamespace


def _register(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "pipeline@example.org",
            "password": "CarbonTracePass123",
            "display_name": "Pipeline Owner",
            "organization_name": "Pipeline Org",
            "country_code": "IN",
        },
    )
    return response.json()["organization"]["id"], response.json()["access_token"]


def test_complete_deterministic_pipeline_persists_agent_history_and_pdf(client, tmp_path, monkeypatch):
    from app.services import reporting

    report_settings = SimpleNamespace(report_directory=str(tmp_path / "reports"), llm_enabled=False)
    monkeypatch.setattr(
        reporting,
        "get_settings",
        lambda: report_settings,
    )
    monkeypatch.setattr(
        "app.api.routes.reports.get_settings",
        lambda: report_settings,
    )
    organization_id, token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    upload = client.post(
        "/api/v1/ingestion/upload",
        headers=headers,
        data={"organization_id": organization_id},
        files={
            "file": (
                "activities.csv",
                "activity_category,activity_date,quantity,unit,country_code\n"
                "electricity,2026-01-15,1000,kWh,IN\n",
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201
    ingestion_id = upload.json()["id"]

    factor = client.post(
        "/api/v1/emission-factors",
        headers=headers,
        params={"organization_id": organization_id},
        json={
            "factor_key": "test-in-electricity-2026",
            "activity_category": "electricity",
            "scope": 2,
            "geography_type": "country",
            "country_code": "IN",
            "activity_unit": "kWh",
            "factor_value": 0.7,
            "factor_unit": "kg CO2e/kWh",
            "method": "location_based",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "source_name": "Test methodology source",
            "source_url": "https://example.org/methodology",
            "publication_version": "test-v1",
            "methodology": "Test-only example factor used by an automated integration test.",
            "is_illustrative": True,
        },
    )
    assert factor.status_code == 201, factor.text
    what_if = client.post(
        "/api/v1/intelligence/what-if",
        headers=headers,
        json={
            "organization_id": organization_id,
            "activity_category": "electricity",
            "current_quantity": 1000,
            "scenario_quantity": 900,
            "unit": "kWh",
            "activity_date": "2026-01-15",
            "country_code": "IN",
            "allow_illustrative": True,
        },
    )
    assert what_if.status_code == 200
    assert float(what_if.json()["estimated_reduction_kg_co2e"]) == 70

    monkeypatch.setattr("app.api.routes.pipeline.get_settings", lambda: SimpleNamespace(run_jobs_async=False))
    pipeline = client.post(
        "/api/v1/pipeline/run",
        headers=headers,
        json={
            "organization_id": organization_id,
            "ingestion_run_id": ingestion_id,
            "reporting_period_start": "2026-01-01",
            "reporting_period_end": "2026-01-31",
            "idempotency_key": "pipeline-january-2026",
            "allow_illustrative": True,
            "generate_report": True,
        },
    )
    assert pipeline.status_code == 200, pipeline.text
    result = pipeline.json()["pipeline_run"]
    assert result["status"] == "succeeded"

    agents = client.get(
        f"/api/v1/pipeline/runs/{result['id']}/agents",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert agents.status_code == 200
    assert {agent["agent_name"] for agent in agents.json()} == {
        "orchestrator",
        "ingestion",
        "validation",
        "calculation",
        "intelligence",
        "reporting",
    }

    database_run = client.get(
        f"/api/v1/pipeline/runs/{result['id']}",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert database_run.json()["status"] == "succeeded"

    reports = client.get(
        "/api/v1/reports",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert reports.status_code == 200
    report_id = reports.json()["items"][0]["id"]
    download = client.get(
        f"/api/v1/reports/{report_id}/download",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")

    analytics = client.get(
        "/api/v1/analytics/overview",
        headers=headers,
        params={
            "organization_id": organization_id,
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
    )
    assert analytics.status_code == 200
    assert float(analytics.json()["total_kg_co2e"]) == 700

    monkeypatch.setattr("app.agents.sustainability_agent.retrieve_documents", lambda _db, _org, _query: [])
    monkeypatch.setattr(
        "app.agents.sustainability_agent.generate_structured_response",
        lambda _prompt, model: model(
            answer="Electricity is the only calculated category in this period.",
            evidence_ids=["analytics:current:category:electricity"],
        ),
    )
    assistant = client.post(
        "/api/v1/intelligence/ask",
        headers=headers,
        json={
            "organization_id": organization_id,
            "question": "Which category contributed most?",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        },
    )
    assert assistant.status_code == 200, assistant.text
    assert assistant.json()["citations"][0]["id"] == "analytics:current:category:electricity"
    history = client.get(
        "/api/v1/intelligence/history",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert history.status_code == 200
    assert history.json()[0]["interaction_type"] == "assistant"

    report_settings.llm_enabled = True
    report_settings.llm_provider = "gemini"
    report_settings.llm_model = "test-gemini"
    monkeypatch.setattr(
        "app.services.reporting.generate_structured_response",
        lambda _prompt, model: model(
            executive_summary="Persisted emissions were calculated for the reporting period.",
            observations=[],
            assumptions=["Uploaded activity data is complete."],
            limitations=["This narrative is AI-generated."],
            evidence_ids=[f"factor:{factor.json()['id']}"],
        ),
    )
    narrated_report = client.post(
        "/api/v1/reports/generate",
        headers=headers,
        json={
            "organization_id": organization_id,
            "pipeline_run_id": result["id"],
            "report_type": "custom",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        },
    )
    assert narrated_report.status_code == 201, narrated_report.text
    assert narrated_report.json()["methodology_snapshot"]["ai_narrative_status"] == "succeeded"
    assert narrated_report.json()["methodology_snapshot"]["ai_narrative"]["executive_summary"].startswith("Persisted")
