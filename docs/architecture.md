# CarbonTrace Architecture

## Status and scope

This document records the implemented architecture and remaining operational work. The workspace includes a functioning FastAPI backend, a React dashboard, database migrations, organization-scoped workflow APIs, optional Gemini/RAG assistance, and local container/CI configuration. Production deployment and hardening remain operator responsibilities.

## Current implementation snapshot

- Implemented: auth and organization membership, CSV/XLSX ingestion, explicit activity validation review, sourced emission-factor CRUD, deterministic calculation, persisted pipeline/agent runs, analytics, insights/anomalies, PDF reports, OWID benchmark fetch/query, optional Gemini chat and embeddings, organization-scoped Chroma RAG, AI-assisted report narratives, API-connected frontend, Docker Compose, and CI checks.
- Not included: seeded authoritative factors, ERP/utility connectors, production hosting, managed secret storage, backups, TLS termination, rate limiting, or production monitoring.
- Pipeline execution can be synchronous in local development or use the included Celery worker in Compose. Benchmark refresh is available to owners/admins and is also scheduled weekly by Celery Beat.

## Product and system goals

CarbonTrace is a tenant-aware organizational emissions application. Users import operational activity data, review validation issues, run a traceable calculation and intelligence workflow, and retrieve analytics and sustainability reports.

The initial system is a modular monolith. It has one FastAPI application and one PostgreSQL database, with Celery workers and a scheduler for work that should not block API requests. Redis is the Celery broker and result backend. The React application communicates with the REST API; it does not calculate official emissions or contain production analytics fixtures.

Core design constraints:

- Keep calculation, factor selection, scope mapping, and what-if arithmetic deterministic and independent of LLMs.
- Store raw source data and calculation provenance so results can be audited and recomputed.
- Enforce organization-level authorization at every data access boundary.
- Treat validation failures as reviewable outcomes; never silently repair or discard invalid records.
- Never use OWID country totals as organizational emission factors.
- Use only factors with documented applicability, source, validity and units. Do not silently substitute factors across countries, years or methods.
- Label synthetic activity and illustrative factors as demonstration-only.
- Make optional LLM explanations unavailable without affecting deterministic processing.
- Keep Gemini outside the accounting path: it can explain stored results but cannot calculate emissions, choose factors, or execute SQL.
- Keep knowledge documents organization-scoped in both PostgreSQL metadata and Chroma metadata filters; citations resolve to retrieved document chunks and pages.

## System context and component boundaries

```text
Browser
  React + TypeScript + Vite
       | HTTPS / JSON REST
       v
FastAPI modular monolith
  API/auth/tenant authorization
  ingestion and validation services
  factor registry and carbon engine
  deterministic intelligence and reporting services
  LangGraph workflows (carbon pipeline + grounded assistant)
       |                         |
       | SQLAlchemy / Alembic    | enqueue and execute
       v                         v
  PostgreSQL                 Redis / Celery
  tenant and audit data      worker + periodic scheduler
                                  |
                                  +--> OWID source adapter
                                  +--> CSV/Excel and report storage
       FastAPI / Celery --> ChromaDB --> Gemini API (optional)
       documents and vectors   persistent, tenant-filtered   chat + embeddings
```

### Backend modules

| Module | Responsibility |
|---|---|
| `app/api/` | Versioned REST routes, request/response validation, authentication dependencies and HTTP error mapping. |
| `app/core/` | Configuration, security primitives, logging and shared constants. |
| `app/database/` | SQLAlchemy engine/session management and Alembic integration. |
| `app/models/` | Persistent SQLAlchemy entities and relationships. |
| `app/schemas/` | Pydantic request, response and agent-state contracts. |
| `app/services/` | Application services: ingestion, normalization, factors, calculations, analytics and reporting. |
| `app/agents/` | Agent node adapters and LangGraph workflow; agents orchestrate services rather than duplicating business logic. |
| `app/tasks/` | Celery tasks, retry policy and scheduled task entry points. |
| `app/utils/` | Narrow shared helpers only; domain rules belong in services. |

### Frontend modules

`frontend/src` contains the API client, typed API contracts, dashboard screens, accessible forms, and chart components. Dashboard values and workflow history come from API responses. API errors, loading states, empty states, and role restrictions are surfaced in the UI.

## Gemini and retrieval-augmented generation

Gemini is configured by `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` (the legacy `LLM_API_KEY` environment name is accepted). `LLM_MODEL`, `LLM_EMBEDDING_MODEL`, `LLM_REQUEST_TIMEOUT_SECONDS`, and `LLM_MAX_RETRIES` configure the model and bounded requests. `LLM_PROVIDER=disabled` or a missing key leaves existing deterministic workflows available and disables only optional assistant features.

Owners/admins may upload authorized PDF, Markdown, or plain-text sustainability guidance. The API extracts text, chunks it, requests Gemini retrieval embeddings, and stores vectors in the internal persistent Chroma service. PostgreSQL stores organization-scoped document metadata and checksums; Chroma chunk metadata carries the organization and document IDs. Retrieval always filters by the authenticated organization. Documents are never shared across organizations. A document can be deleted by an owner/admin, which removes its vectors.

The LangGraph sustainability assistant has three bounded stages: load persisted organization analytics, retrieve organization-specific documentation, and generate a Pydantic-validated response. Its data access uses fixed SQLAlchemy queries scoped to the authorized organization; Gemini receives no SQL execution path. Responses cite only server-supplied evidence IDs. Recommendations distinguish verified findings from proposed actions and can cite retrieved guidance. Report narratives use persisted totals, insights, anomaly evidence, and factor provenance; the deterministic PDF tables and calculation results remain unchanged. When Gemini is unavailable, the PDF still completes without the optional narrative.

RAG answers are evidence-grounded but should still be reviewed as AI-generated text. Only upload documents the organization is authorized to process. Gemini API use transmits the question, selected-period aggregates, retrieved document excerpts, or report context to Google when enabled; operators should assess applicable data-processing and retention requirements.

## AI data model and API

- `knowledge_documents` stores tenant ID, title, source URL, filename, content checksum, indexing status, chunk count, and uploader. Original files are not retained by this feature.
- `ai_interactions` stores tenant ID, user, interaction type, input text, structured output, provider/model, status, resource reference, and cited evidence IDs.
- `POST /api/v1/knowledge/documents` and `GET/DELETE /api/v1/knowledge/documents` manage organization-specific sources (upload/delete require owner/admin).
- `POST /api/v1/intelligence/ask` and `/recommendations` produce grounded analytics answers and recommendations. `GET /api/v1/intelligence/history` lists only the caller's organization interactions.

## Workflow and agent boundaries

The six named agents are workflow components with explicit contracts and persisted execution records, not independent autonomous services. LangGraph coordinates them. Each agent node calls deterministic application services where possible and records its input/output summary, status, timestamps, attempt number and any structured error in `agent_runs`.

The workflow state is a typed Pydantic model (or a `TypedDict` whose nested payloads are Pydantic models) with at least:

```text
pipeline_run_id, organization_id, requested_by_user_id
ingestion_run_id, source_record_ids, normalized_record_ids
validation_summary, records_requiring_review
calculation_result_ids, intelligence_result_ids, report_id
current_stage, status, attempt, started_at, completed_at
errors[], warnings[]
```

The state must not carry credentials or full uploaded files. Use database identifiers and bounded summaries. Workers reload authorized records by ID.

| Agent | Input | Output | Responsibility and failure behavior |
|---|---|---|---|
| Ingestion | Organization/source IDs, upload or configured source reference, idempotency key | Ingestion run ID, raw record IDs, accepted/rejected counts, source fingerprint | Parse CSV/XLSX with explicit size/type limits; preserve source provenance; reject duplicate import safely; OWID fetch is a separately configured benchmark operation. |
| Validation | Ingestion run ID and raw record IDs | Per-record issues, valid normalized candidates, review queue IDs, quality score | Validate required fields, types, units, dates, duplicates, quantity sign/range and supported categories. Invalid data remains visible for review and is excluded from calculation. |
| Calculation | Valid normalized activity IDs, organization/reporting period, factor registry version | Calculation result IDs and totals with factor/method provenance | Resolve an applicable factor and mapping; fail that record with a clear reason if none exists. Arithmetic and unit conversion are deterministic. |
| Intelligence | Persisted calculation IDs and comparison period; optional benchmark dataset version | Hotspots, anomalies, evidence-linked insights and recommendation records | Compare real persisted observations; use a robust statistical baseline and require sufficient history for model-based methods. OWID is context only. |
| Reporting | Completed pipeline or explicit reporting period and persisted results | Report ID, status, storage key and included result version | Produce PDF only from persisted, successfully calculated results; mark any explicitly allowed partial report incomplete. |
| Orchestrator | Organization, requested operation, period and optional ingestion/source IDs | Pipeline run ID, state/status and linked agent-run IDs | LangGraph controls transitions, retries and resumability. Do not schedule downstream calculation/report nodes when prerequisites fail. |

Agent run status values: `pending`, `running`, `succeeded`, `failed`, `retrying`, `skipped`, `needs_review`. Pipeline statuses: `queued`, `running`, `needs_review`, `succeeded`, `partial`, `failed`, `cancelled`. Persist structured error code, safe message and retryability; do not hide failures behind empty/success-shaped outputs.

### Workflow transitions

```text
queued -> ingestion -> validation
                         | invalid/reviewable records
                         +--> needs_review (valid records may proceed only if policy permits)
                         |
                         +--> normalization -> calculation -> intelligence
                                                        -> optional report -> succeeded
Any retryable node failure -> retrying -> same node
Any non-retryable prerequisite failure -> failed
```

Pipeline retry resumes from a failed, idempotent stage when its inputs are unchanged. Re-running with changed inputs or factor versions creates a distinct auditable execution. A report is not represented as complete if required calculation results are missing.

## Persistence, tenancy and provenance

- PostgreSQL is the source of truth for users, organizations, operational records, factors, workflow history, analytics results, reports and audit events.
- Every organization-owned row carries `organization_id`; database relationships and service-level query helpers must prevent cross-tenant references. Authorization is checked on both reads and writes, not just route entry.
- Shared emission factors may have a null organization owner; organization-specific factors are tenant-scoped. A custom factor cannot override a shared factor without explicit selection and provenance.
- Raw import rows are retained as JSONB with a source fingerprint and ingestion association. Store uploaded files using generated opaque names outside static web roots; persist only the storage key, original safe display name, hash and metadata.
- Calculation results retain the activity record, chosen factor/version, units, conversion, formula inputs, output in kg CO2e, scope, method and calculation version. Historical results are append-only for recalculation purposes.
- Audit events record actor, organization, action, resource type/ID, timestamp and safe metadata. Never log passwords, tokens, full raw files or secrets.
- Use Alembic for all schema changes. UUID identifiers and UTC timestamps are used throughout; organization timezone is used to interpret schedule boundaries.

## Emissions methodology and source boundaries

The carbon engine will perform `activity quantity × applicable emission factor`, after an explicit unit conversion, and store results in kg CO2e. Display tonnes CO2e as kg divided by 1,000. Keep precision through calculation and round only at presentation boundaries.

The registry requires factor ID, category, geography, unit, value, validity period, source/reference, publication/version, method, data quality/uncertainty when available and Scope mapping. Factor lookup rejects ambiguous or inapplicable matches. Scope 2 location-based accounting is the initial supported method; market-based accounting requires suitable contractual instrument data and factors. Scope 3 is limited to categories with documented mapping and suitable activity data.

OWID is a separately versioned country-level benchmarking dataset. Before implementation, verify the upstream repository's current published dataset and schema; cache retrieval metadata and avoid downloading it on every pipeline run. It is not an organizational factor source.

## Background processing and scheduling

API requests that enqueue long-running pipelines return `202 Accepted` and a pipeline ID. Celery handles upload parsing, pipeline execution, report rendering and configured refresh jobs. Redis is not the durable source of execution history; PostgreSQL run records remain authoritative.

Tasks are idempotent, have bounded retries for transient errors, and store each attempt. Periodic runs use organization timezones and a unique organization/period/job key to prevent duplicate scheduled work. Celery Beat triggers due work; manual requests use the same service/workflow path. Health endpoints distinguish API liveness from database/worker readiness.

## Security baseline

- Password hashes use a modern password-hashing library; plaintext passwords are never stored.
- JWT secrets, database URLs, CORS origins, upload limits, LLM provider configuration and storage paths come from environment configuration.
- Authenticate with JWT and authorize using organization membership and roles (`owner`, `admin`, `analyst`, `viewer`); enforce organization scope in every query.
- Validate upload extension, MIME/content and size; generate storage names server-side; never trust a user-supplied path.
- Use SQLAlchemy parameterized operations, structured safe errors, restricted CORS and audit logging.
- Do not commit credentials. `.env.example` contains placeholders only.
- Apply rate limits to authentication and other abuse-sensitive routes when the app deployment has a reliable shared rate-limit backend.

## Current validation and remaining operator steps

The application test suite covers deterministic pipeline behavior, organization authorization, AI evidence references, knowledge-document indexing, persisted interactions, and optional report narratives. The frontend has lint, unit-test, and production-build checks. Docker Compose includes the persistent Chroma service and Alembic applies the schema migration on API startup.

Before demonstrating live Gemini behavior:

1. Configure `LLM_PROVIDER=gemini` and a valid `GEMINI_API_KEY` in the local `.env`; do not commit or paste the key.
2. Rebuild/start with `docker compose up --build` so the schema migration and Chroma service are applied.
3. Sign in as an owner/admin, upload an authorized text-based sustainability source, then ask a question or generate recommendations.
4. Verify source citations and the saved AI interaction; generate a PDF from a completed pipeline to check the optional narrative.

Live provider calls require the user's Gemini API access and were not part of automated tests. Source factors, document authorization, production secret handling, backups, TLS, rate limiting, and operational monitoring still require deployment-specific verification.
