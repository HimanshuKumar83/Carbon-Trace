# CarbonTrace REST API Contract

## Conventions

- Base path: `/api/v1`.
- Content type: `application/json`, except multipart upload and PDF download.
- Authentication: `Authorization: Bearer <JWT>` for protected endpoints.
- Every organization-owned operation is scoped to an organization membership. The selected organization is supplied through an `organization_id` request field or an `X-Organization-ID` header; if both are accepted by implementation, they must agree. The API must never trust an organization ID without checking membership and role.
- Use ISO-8601 UTC timestamps and ISO dates (`YYYY-MM-DD`). Quantities and emissions are JSON numbers; emissions are expressed in kg CO2e unless the field states otherwise.
- List responses use `items`, `page`, `page_size`, `total`; default page size 25, maximum 100.
- Long-running operations return `202 Accepted` with a resource ID and status URL.
- FastAPI's generated OpenAPI schema is the implementation source of truth; these contracts define the intended behavior.

## Standard errors

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "One or more activity records need review.",
    "details": [
      {"field": "quantity", "code": "NEGATIVE_NOT_ALLOWED", "message": "Quantity must be non-negative."}
    ],
    "request_id": "uuid"
  }
}
```

Use HTTP semantics consistently: `400` malformed request, `401` missing/invalid authentication, `403` insufficient role, `404` resource absent or outside the caller's organization, `409` idempotency/state conflict, `413` upload too large, `415` unsupported file type, `422` schema/domain validation, `429` rate limit, `500` unexpected server failure. Do not expose stack traces, SQL, tokens, file paths or cross-tenant existence.

## Authorization roles

| Operation | Owner / admin | Analyst | Viewer |
|---|---:|---:|---:|
| View organization, analytics, pipeline and reports | Yes | Yes | Yes |
| Manage organization members/settings and data sources | Yes | No | No |
| Upload data, review records, start/retry pipeline, generate reports | Yes | Yes | No |
| Manage organization emission factors | Yes | No | No |

Registration creates the first organization owner only through the defined registration flow. Membership checks happen server-side for every organization-scoped operation.

## Authentication

### `POST /auth/register`

Create a user and initial organization. Body:

```json
{
  "email": "admin@example.org",
  "password": "use-a-strong-password",
  "display_name": "Organization Admin",
  "organization_name": "Example Organization",
  "country_code": "IN",
  "timezone": "Asia/Kolkata"
}
```

Returns `201` with `{ "access_token": "...", "token_type": "bearer", "user": {...}, "organization": {...} }`. Do not return password hashes.

### `POST /auth/login`

Body: `{ "email": "...", "password": "..." }`. Returns `200` with access token and user membership summaries. Use a generic invalid-credentials message.

### `GET /auth/me`

Returns `200` with the authenticated user and organizations/roles available to that user.

## Organizations

- `POST /organizations` — owner/admin create an organization; `201` organization.
- `GET /organizations` — list memberships for current user.
- `GET /organizations/{id}` — member read; `200` organization.
- `PUT /organizations/{id}` — owner/admin update allowed organization fields; `200` organization.

Organization response contains `id`, `name`, `country_code`, `region`, `timezone`, `reporting_preferences`, `created_at`, `updated_at`. Never return secrets or internal storage configuration.

## Ingestion and data review

### `POST /ingestion/upload`

Multipart form with `file` (CSV or XLSX), `organization_id`, and optional `idempotency_key`. The current endpoint processes the file synchronously and returns `201` with the persisted ingestion-run fields and accepted/rejected counts. Apply configured file size and file-extension checks.

Duplicate submission with the same organization/source/idempotency key returns the existing run or `409` if the payload conflicts; it must not create duplicate activities.

### `POST /ingestion/start`

Queue ingestion for a configured source. Body: `{ "organization_id": "uuid", "data_source_id": "uuid", "idempotency_key": "..." }`. Returns `202` ingestion run.

### `GET /ingestion/history`

Query: `organization_id`, optional `status`, `source_type`, `from`, `to`, `page`, `page_size`. Returns paginated ingestion summaries and accepted/rejected counts.

### `GET /ingestion/{id}`

Returns status, source metadata, timestamps, counts, validation summary and safe errors. Must be tenant scoped.

### Activity validation review

`GET /activities?organization_id=...&validation_status=needs_review&page=...&page_size=...` returns a paginated queue. `validation_status` may be `pending`, `needs_review`, `valid`, or `rejected`.

`PATCH /activities/{id}/review?organization_id=...` accepts an explicit `decision` of `accept` or `reject`. Accept requires corrected `activity_category`, `activity_date`, `quantity`, and `unit`, and may include `country_code`; the corrected row is revalidated before it can become `valid`. Reject records an explicit `rejected` status. Both decisions record the authenticated reviewer and an audit event. The original raw payload remains unchanged. Invalid records are never silently transformed into valid ones.

## Emission factors

- `GET /emission-factors?organization_id=...` — list active organization-specific and shared factors; optional `activity_category` and owner/admin-only `include_inactive`.
- `POST /emission-factors?organization_id=...` — owner/admin creates an organization-specific factor with category, scope, geography, activity unit, factor value/unit, method, validity, source name/URL, publication version and methodology. Incompatible scope/method/geography/date/unit combinations are rejected.
- `POST /emission-factors/{id}/deactivate?organization_id=...` — owner/admin deactivates an organization-specific factor without deleting historical provenance.

The UI does not seed authoritative values. Organizations are responsible for entering a verified source and factor that match activity geography, unit, date and accounting method. Illustrative factors are identified separately and cannot be used unless the pipeline request explicitly enables illustrative mode.

### Sample templates

`GET /ingestion/templates/{format}` (`format` is `csv` or `xlsx`) returns a downloadable template. Sample data is explicitly labeled synthetic and illustrative factors are not included as authoritative values.

## Pipeline

### `POST /pipeline/run`

Queue a manual pipeline. Body:

```json
{
  "organization_id": "uuid",
  "ingestion_run_id": "uuid",
  "reporting_period_start": "2026-01-01",
  "reporting_period_end": "2026-01-31",
  "generate_report": false,
  "idempotency_key": "org-period-source-operation"
}
```

In synchronous mode the endpoint returns the persisted run after execution; in async mode it returns `202` with a persisted run object and task identifier. Idempotency key uniqueness is per organization. Requests cannot reference another organization's ingestion run.

- `GET /pipeline/runs` — paginated list, filter by status and period.
- `GET /pipeline/runs/{id}` — status, timestamps, stage, safe errors and aggregate counts.
- `POST /pipeline/runs/{id}/retry` — retry a failed/retryable run; `202` new or resumed run. Reject terminal/non-retryable runs with `409`.
- `GET /pipeline/runs/{id}/agents` — ordered persisted agent execution records including attempts, statuses, durations, safe error messages and bounded summaries.

## Analytics

All analytics endpoints require `organization_id` and accept optional `from`, `to`, `period` and applicable category filters. All summaries are derived from persisted successful calculation results; no hardcoded production dashboard data.

- `GET /analytics/overview` — total kg/t CO2e, Scope 1/2/3 totals, selected period, previous-period comparison and data-quality score.
- `GET /analytics/emissions-trend` — time-bucketed totals and prior-period values.
- `GET /analytics/scope-breakdown` — scope totals and shares.
- `GET /analytics/category-breakdown` — category totals and shares.
- `GET /analytics/hotspots` — top contributing categories with measured totals, shares and period.

Responses include `period_start`, `period_end`, `unit`, `generated_at` and any data-quality/completeness notes. An empty dataset returns zero/empty aggregates with an explicit no-data indicator, not fabricated values.

## Intelligence and what-if

- `GET /intelligence/anomalies` — paginated anomaly records; filter by period/category/severity.
- `GET /intelligence/insights` — paginated insight records with evidence references and generation method.
- `POST /intelligence/what-if` — deterministic scenario estimate. Body includes organization, activity category, current quantity/unit, proposed quantity or percentage change, geography, date and optional factor ID. Response includes baseline emissions, scenario emissions, estimated delta, percent change, factor provenance and warnings. Reject missing/ambiguous/inapplicable factors. Clearly label outputs as estimates, not achieved reductions.

No endpoint uses an LLM to calculate emissions or select an unverified factor.

### Grounded Gemini assistance

Optional LLM routes require `LLM_PROVIDER=gemini` and a configured Gemini API key. They do not affect deterministic calculation endpoints.

- `POST /intelligence/ask` — member access. Body: `organization_id`, `question`, `period_start`, and `period_end`. Returns a structured answer, source citations, limitations, and interaction ID. The backend loads fixed organization-scoped analytics and retrieves only documents indexed for that organization; Gemini cannot execute SQL.
- `POST /intelligence/recommendations` — member access. Body: `organization_id`, `period_start`, and `period_end`. Returns recommendations with separate verified findings, suggested actions, evidence IDs, guidance IDs, citations, and limitations.
- `GET /intelligence/history` — member access. Returns paginated, organization-scoped persisted AI interactions.
- `POST /knowledge/documents` — owner/admin multipart upload with `organization_id`, `file` (PDF/TXT/Markdown), optional `source_title`, and optional HTTP(S) `source_url`. Documents are extracted, chunked, embedded, and stored in Chroma. Original files are not retained.
- `GET /knowledge/documents?organization_id=...` — member access; list the organization's document metadata and indexing status.
- `DELETE /knowledge/documents/{id}?organization_id=...` — owner/admin removes a document and its vector chunks.

Gemini-produced citations are resolved by the server against the supplied analytics evidence and retrieved chunks; references outside that set are rejected. Only upload documents the organization is authorized to process. When enabled, selected-period aggregates, user questions, retrieved excerpts, and report context are transmitted to Google Gemini.

## Reports

- `POST /reports/generate` — body includes organization, required completed `pipeline_run_id`, `report_type` (`monthly`, `quarterly`, `custom`), `period_start`, and `period_end`. Returns `201` with report metadata.
- `GET /reports` — paginated organization report history with period, status, type and creation time.
- `GET /reports/{id}` — report metadata and completeness/methodology status.
- `GET /reports/{id}/download` — authorize membership and return `application/pdf` with safe download headers; incomplete reports are visibly marked. Never expose a public storage key.

Reports are generated from persisted calculation and insight records. A report cannot be marked complete if prerequisite calculations failed or are absent. When Gemini is enabled, an optional cited narrative is appended; a Gemini failure is logged and does not prevent the deterministic PDF from being produced.

## OWID benchmarking

- `GET /benchmarking/countries` — country code/name list available in the cached OWID dataset.
- `GET /benchmarking/{country}/trends` — historical country-level series, with selected variables, units, source dataset version/retrieval time and missing-data notes.

Benchmarking responses are clearly labeled country-level historical context. They are never exposed by the factor registry endpoint or treated as organization emissions.

## Data sources and settings

The implemented input connector is manual CSV/XLSX upload; OWID is a separately refreshed benchmark adapter. Data-source CRUD and third-party ERP, utility, and travel integrations are not currently exposed. Any future secret-bearing configuration must be stored via an appropriate secret mechanism and redacted in responses.

## Pagination, filters and idempotency

- `page` starts at 1; `page_size` defaults to 25 and is capped at 100.
- Validate period start <= end and reject unsupported sort fields.
- Apply organization scope before pagination and aggregation.
- Use idempotency keys for ingestion, pipeline requests and scheduled job periods. A repeated matching request returns the existing resource; a conflicting payload for the same key returns `409`.
- Enqueueing returns quickly; clients poll the returned status URL. Poll responses expose persisted statuses and do not infer success from queue acknowledgment.

## OpenAPI and contract verification

FastAPI generates `/docs` and `/openapi.json` in configured non-production environments. Tests should verify representative request/response schemas, authentication, role checks, organization isolation, error shapes, pagination, upload rejection, idempotency and PDF content headers. Keep frontend TypeScript API types aligned with OpenAPI schemas rather than maintaining contradictory hand-written contracts.
