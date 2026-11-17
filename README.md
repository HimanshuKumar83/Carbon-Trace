# CarbonTrace

CarbonTrace is an organizational carbon accounting and sustainability intelligence platform. It provides organization-scoped authentication, activity ingestion and review, a sourced emission-factor registry, deterministic emissions calculations, pipeline history, analytics, insights, OWID benchmarking context, and PDF reporting.

## Fastest start: Docker Compose

Install Docker Desktop and make sure its engine is running. From the project root:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `SECRET_KEY` to a long random value and `POSTGRES_PASSWORD` to a strong password made of URL-safe characters. You can generate the secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then start PostgreSQL, Redis, the API, Celery worker and scheduler, and frontend:

```powershell
docker compose up --build
```

Open the app at `http://localhost:8080` and the API docs at `http://localhost:8000/docs`. The API applies database migrations on startup. Create an account in the app; the first account is the owner of its organization.

To stop the services, press `Ctrl+C`, then run `docker compose down`. Application data persists in Docker volumes. **Do not run `docker compose down -v` unless you intend to permanently delete the local database and uploaded/report files.**

## Optional Gemini assistant and RAG

The accounting engine, ML anomaly detection, analytics, and deterministic PDF reports work without an LLM key. To enable the optional Gemini assistant and document retrieval:

1. In `.env`, set `LLM_PROVIDER=gemini` and set `GEMINI_API_KEY` to a Gemini API key. You can also configure `LLM_MODEL`, `LLM_EMBEDDING_MODEL`, `LLM_REQUEST_TIMEOUT_SECONDS`, and `LLM_MAX_RETRIES`. The legacy `LLM_API_KEY` name is accepted for existing local setups.
2. Rebuild and start the services:

   ```powershell
   docker compose up --build
   ```

   Compose runs ChromaDB with a persistent volume. Its port is bound to the local machine only.
3. Sign in as an organization owner/admin. In the **AI assistant** section, upload authorized PDF, TXT, or Markdown sustainability guidance. The document is chunked and embedded with Gemini and indexed only for that organization.
4. Ask about the selected reporting period, or generate recommendations. Review citations, limitations, and the difference between verified emissions findings and general suggested actions. Generate a report from a completed pipeline to include an optional Gemini narrative alongside deterministic results.

Only upload source documents the organization is authorized to process. When enabled, relevant question, organization-period aggregates, retrieved excerpts, and report context are sent to Google Gemini. For local development outside Compose, start `chroma` with `docker compose up chroma` and use the `.env.example` host/port (`localhost:8001`).

## Local development without Docker

Requirements: Python 3.11+, Node.js 20.19+ or 22.12+, PostgreSQL, and (for asynchronous pipelines) Redis.

1. Create a PostgreSQL role and database. For example, connect with `psql -U postgres` and run:

   ```sql
   CREATE ROLE carbontrace WITH LOGIN PASSWORD 'replace-with-a-strong-url-safe-password';
   CREATE DATABASE carbontrace OWNER carbontrace;
   ```

2. Copy `.env.example` to `.env`. Set a unique `SECRET_KEY` and set `DATABASE_URL` to `postgresql+psycopg://carbontrace:<password>@localhost:5432/carbontrace`. If running Redis/Celery, set `RUN_JOBS_ASYNC=true`; otherwise keep it `false` for synchronous local pipeline runs. Do not commit `.env`.
3. Create and activate a Python environment, install dependencies, migrate, and start the API from the project root:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r backend\requirements.txt
   python -m alembic upgrade head
   python -m uvicorn app.main:app --app-dir backend --reload
   ```

4. In a separate terminal, install and start the frontend:

   ```powershell
   Set-Location frontend
   npm ci
   npm run dev
   ```

   Open `http://localhost:5173`. The default API URL is `http://localhost:8000/api/v1`; set `VITE_API_BASE_URL` before `npm run dev` if your API runs elsewhere. The backend CORS origins must include the frontend origin.

5. If you set `RUN_JOBS_ASYNC=true`, start Redis plus these commands in two additional activated Python terminals from the project root:

   ```powershell
   python -m celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
   ```

   ```powershell
   python -m celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
   ```

   If `RUN_JOBS_ASYNC=false`, pipeline runs execute synchronously in the API process and do not require a worker.

## First real-data workflow

1. Register an account and organization in the app.
2. An owner/admin adds emission factors from documented, applicable sources in **Emission factors**. Enter the published value, scope/method, geography, unit, validity dates, source URL, version, and methodology exactly as supported by the source. Factors are not pre-seeded, and a country-level OWID benchmark must never be used as an organizational factor.
3. Upload an organization activity CSV or XLSX file. Required columns are `activity_category`, `activity_date`, `quantity`, and `unit`; `country_code` is optional where factor applicability allows. Quantities must come from the organization’s source records.
4. Resolve flagged rows in **Data review** by explicitly correcting and accepting them or rejecting them. The original source row remains preserved and review decisions are audited.
5. Select the reporting period and run the pipeline. Inspect pipeline status, analytics, insights, and any factor-resolution errors; then generate/download a report from a completed run.

The benchmark dataset is refreshed weekly by Celery Beat and can also be refreshed manually by an owner/admin. The app intentionally does not seed fake organization activity or authoritative emission factors. If a valid published factor is unavailable for an activity’s category, geography, unit, method, or date, the calculation should fail visibly instead of guessing.

## Backend development

The backend tests use isolated SQLite databases; PostgreSQL is the supported persistent database.

Run backend tests from the project root:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest backend\tests -q
```

Never use the development secret in a deployed environment.

## Frontend development

Frontend verification (from `frontend`):

```powershell
npm ci
npm run lint
npm run test
npm run build
```

## Design and progress

- [Architecture and implementation plan](docs/architecture.md)
- [Database design](docs/database_design.md)
- [REST API contract](docs/api_documentation.md)

The first registration request creates a user, an organization and an owner membership. No demo account is seeded. Docker Compose and CI checks are provided, but production hosting, secret management, backups, TLS, monitoring, and production readiness still require deployment-specific work.
