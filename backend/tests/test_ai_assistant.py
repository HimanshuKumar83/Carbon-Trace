from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models import KnowledgeDocument, Organization


def _register(client, email, organization):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "CarbonTracePass123",
            "display_name": "AI Test User",
            "organization_name": organization,
        },
    )
    assert response.status_code == 201
    return response.json()["organization"]["id"], response.json()["access_token"]


def test_nonmember_cannot_query_another_organization_assistant(client, monkeypatch):
    _organization_a, token_a = _register(client, "ai-a@example.org", "AI Org A")
    organization_b, _token_b = _register(client, "ai-b@example.org", "AI Org B")
    monkeypatch.setattr(
        "app.api.routes.intelligence.run_sustainability_agent",
        lambda *_args, **_kwargs: pytest.fail("Unauthorized requests must not invoke the assistant."),
    )

    response = client.post(
        "/api/v1/intelligence/ask",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "organization_id": organization_b,
            "question": "What are our emissions?",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        },
    )

    assert response.status_code == 404


def test_disabled_gemini_provider_fails_explicitly(monkeypatch):
    from app.services.gemini import LLMUnavailableError, _client

    monkeypatch.setattr("app.services.gemini.get_settings", lambda: SimpleNamespace(llm_enabled=False))

    with pytest.raises(LLMUnavailableError, match="disabled"):
        _client()


def test_gemini_structured_response_is_parsed_into_the_requested_schema(monkeypatch):
    from app.schemas.ai import GroundedAnswer
    from app.services import gemini

    class FakeTypes:
        GenerateContentConfig = lambda **kwargs: kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["config"]["response_schema"] is GroundedAnswer
            return SimpleNamespace(
                parsed=None,
                text='{"answer":"Supported answer","evidence_ids":["analytics:current:total"],"limitations":[]}',
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(
        gemini,
        "get_settings",
        lambda: SimpleNamespace(llm_model="test-model", llm_max_retries=0),
    )
    monkeypatch.setattr(gemini, "_client", lambda: (FakeClient(), FakeTypes))

    result = gemini.generate_structured_response("prompt", GroundedAnswer)

    assert result.answer == "Supported answer"
    assert result.evidence_ids == ["analytics:current:total"]


def test_rag_retrieval_always_filters_by_organization(monkeypatch):
    from app.services import knowledge

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    class FakeCollection:
        where = None
        document_id = ""

        def query(self, **kwargs):
            self.where = kwargs["where"]
            return {
                "ids": [[f"{self.document_id}:0"]],
                "documents": [["GHG guidance excerpt"]],
                "metadatas": [[{
                    "title": "Guidance",
                    "source_url": "https://example.org/ghg",
                    "page": 4,
                    "document_id": self.document_id,
                }]],
                "distances": [[0.1]],
            }

    collection = FakeCollection()
    monkeypatch.setattr(knowledge, "generate_embeddings", lambda _texts, task_type: [[0.1, 0.2]])
    monkeypatch.setattr(knowledge, "_collection", lambda: collection)

    organization_id = UUID("c9db1cd0-12d1-45b0-921c-e1295af82d4c")
    with Session(engine) as db:
        organization = Organization(id=organization_id, name="Tenant")
        db.add(organization)
        db.flush()
        db.add(
            KnowledgeDocument(
                organization_id=organization_id,
                title="Guidance",
                filename="guidance.pdf",
                content_sha256="b" * 64,
                status="active",
                chunk_count=1,
            )
        )
        db.flush()
        document_id = db.scalar(select(KnowledgeDocument.id))
        collection.document_id = str(document_id)
        results = knowledge.retrieve_documents(
            db=db,
            organization_id=organization_id,
            query="Scope 2 accounting guidance",
        )

    assert collection.where == {
        "$and": [
            {"organization_id": "c9db1cd0-12d1-45b0-921c-e1295af82d4c"},
            {"active": True},
        ]
    }
    assert results[0]["id"] == f"knowledge:{document_id}:0"
    assert results[0]["page"] == 4
    engine.dispose()


def test_knowledge_upload_is_indexed_and_tenant_scoped(client, monkeypatch):
    from app.services import knowledge

    organization_id, token = _register(client, "knowledge-owner@example.org", "Knowledge Org")

    class FakeCollection:
        def __init__(self):
            self.items = {}

        def add(self, ids, embeddings, metadatas, documents):
            self.items.update({
                chunk_id: {"metadata": metadata, "text": text}
                for chunk_id, metadata, text in zip(ids, metadatas, documents)
            })

        def get(self, where, include):
            document_id = where.get("document_id")
            return {
                "ids": [key for key, value in self.items.items() if not document_id or value["metadata"]["document_id"] == document_id],
                "metadatas": [value["metadata"] for value in self.items.values() if not document_id or value["metadata"]["document_id"] == document_id],
            }

        def update(self, ids, metadatas):
            for chunk_id, metadata in zip(ids, metadatas):
                self.items[chunk_id]["metadata"] = metadata

        def delete(self, ids):
            for chunk_id in ids:
                self.items.pop(chunk_id, None)

    collection = FakeCollection()
    monkeypatch.setattr(knowledge, "generate_embeddings", lambda texts, task_type: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(knowledge, "_collection", lambda: collection)
    headers = {"Authorization": f"Bearer {token}"}

    uploaded = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        data={
            "organization_id": organization_id,
            "source_title": "GHG guidance",
            "source_url": "https://example.org/ghg",
        },
        files={"file": ("guidance.txt", "Use documented emission factors.", "text/plain")},
    )

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["status"] == "active"
    assert uploaded.json()["chunk_count"] == 1
    listed = client.get(
        "/api/v1/knowledge/documents",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["title"] == "GHG guidance"
