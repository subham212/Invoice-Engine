<div align="center">

# Zamp AP Invoice Engine

### Intelligent invoice processing with explainable decisions and human oversight

<a href="https://invoice-engine.pages.dev"><strong>Open the live application ↗</strong></a>

<br />

<a href="https://react.dev"><img alt="React" src="https://img.shields.io/badge/React-19-149eca?logo=react&logoColor=white"></a>
<a href="https://www.typescriptlang.org"><img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-6-3178c6?logo=typescript&logoColor=white"></a>
<a href="https://fastapi.tiangolo.com"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white"></a>
<a href="https://developers.cloudflare.com/d1/"><img alt="Cloudflare D1" src="https://img.shields.io/badge/Cloudflare-D1-f38020?logo=cloudflare&logoColor=white"></a>
<a href="https://developers.cloudflare.com/workers-ai/"><img alt="Workers AI" src="https://img.shields.io/badge/Cloudflare-Workers_AI-f38020?logo=cloudflare&logoColor=white"></a>

</div>

---

Zamp AP processes supplier invoices through extraction, financial validation, purchase order matching, and human review. Each run retains its decision, validation results, extraction provenance, and audit trace. The application is built with a React and TypeScript frontend, a FastAPI service, Cloudflare D1, and Cloudflare Workers AI. Supabase Storage is optional for retrieving documents by object key.

## Product behavior

The service accepts PDF, PNG, JPEG, and WEBP invoices up to 10 MB. PDF processing extracts text from the first two pages; scanned PDFs and image uploads use the configured Workers AI vision models. Digital PDF text extraction remains available as a deterministic fallback.

Invoice checks include arithmetic, vendor status, duplicate detection, PO currency and line matching, and remaining PO capacity. Runs that require human judgment are assigned `MANUAL_REVIEW`; a reviewer can record one approval or rejection with a name and audit note. Duplicate invoices and PO hard-cap violations are rejected.

| Decision | Operational meaning |
|---|---|
| `APPROVED` | Required checks passed. |
| `APPROVED_WITH_WARNING` | Checks passed with a warning that should be reviewed. |
| `MANUAL_REVIEW` | AP review is required before payment. |
| `REJECTED` | A blocking vendor, duplicate, or PO condition prevents payment. |

Dashboard totals are grouped by currency. The combined total is informational and does not convert currencies.

## Architecture

```text
React + TypeScript (Cloudflare Pages)
              │ REST + Server-Sent Events
              ▼
FastAPI service (Render)
   ├── Cloudflare D1       Runs, vendors, purchase orders, audit history
   ├── Cloudflare Workers AI  Structured text and vision extraction
   └── Supabase Storage    Optional document retrieval by object key
```

The backend owns credentials for D1, Workers AI, and optional Supabase access. The frontend receives only the public API origin through `VITE_API_BASE_URL`.

## Repository layout

```text
backend/app/api/       HTTP endpoints, streaming, persistence, and review
backend/app/core/      Runtime configuration and external service clients
backend/app/engine/    Extraction, validation, matching, and decision logic
backend/app/models/    API and domain schemas
backend/app/data/      Built-in scenario definitions
backend/generated_samples/  Scenario invoice PDFs
backend/tests/         Deterministic pipeline and edge-case tests
migrations/            D1 schema and seed SQL
frontend/src/           React application and API client
```

## Local development

### Requirements

- Python 3.12 or newer
- Node.js and npm compatible with `frontend/package.json`
- Cloudflare D1 credentials for persisted processing
- Workers AI credentials for AI extraction
- Supabase credentials only when processing storage object keys

### Configure the backend

From the repository root, create a virtual environment and install the backend requirements:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set the required backend values in `backend/.env`, then start the API from the `backend` directory:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API health endpoint is `http://127.0.0.1:8000/api/health`; interactive API documentation is available at `/docs`.

### Configure the frontend

In another terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
```

Set `VITE_API_BASE_URL=http://127.0.0.1:8000` in `frontend/.env`, then run:

```powershell
npm run dev
```

Vite prints the local application URL when the development server starts.

## Configuration

Backend settings are defined in `backend/.env.example` and `backend/app/core/config.py`.

| Variable | Purpose |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account that owns D1 and Workers AI. |
| `CLOUDFLARE_API_TOKEN` | D1 API credential; can also serve as the AI credential. |
| `D1_DATABASE_ID` | Target D1 database. |
| `CLOUDFLARE_AI_API_TOKEN` | Optional separate Workers AI credential. |
| `CLOUDFLARE_API_KEY` | Compatibility fallback for Workers AI authentication. |
| `CF_AI_VISION_MODEL` | Primary vision model. |
| `CF_AI_FALLBACK_VISION_MODEL` | Vision fallback model. |
| `CF_AI_TEXT_MODEL` | Text extraction model. |
| `ALLOWED_ORIGINS` | Comma-separated browser origins accepted by the API. |
| `ENVIRONMENT` | Runtime environment label. |
| `SUPABASE_URL` | Supabase project URL, when storage retrieval is enabled. |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side Supabase Storage credential. |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket; defaults to `invoices`. |

The Workers AI credential lookup order is `CLOUDFLARE_AI_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, then `CLOUDFLARE_API_KEY`. The Cloudflare token used by D1 needs D1 edit access; Workers AI use requires the appropriate AI permission. Restrict credentials to the required account and resources. Never add backend credentials to frontend variables or source control.

`/api/health` reports whether AI credentials are configured; it does not make a Workers AI request.

## API surface

All application routes are under `/api`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service and AI configuration status. |
| `GET` | `/api/scenarios` | List available sample scenarios. |
| `GET` | `/api/scenarios/{scenario_key}/pdf` | Retrieve a scenario invoice PDF. |
| `POST` | `/api/process` | Process an upload or scenario and stream workflow events over SSE. |
| `POST` | `/api/upload` | Process an uploaded invoice and return the result. |
| `POST` | `/api/run` | Process a document referenced by Supabase object key. |
| `GET` | `/api/history` | List persisted invoice runs. |
| `GET` | `/api/history/{run_id}` | Retrieve one persisted run. |
| `POST` | `/api/runs/{run_id}/review` | Record a one-time AP review decision. |
| `GET` | `/api/audit/{run_id}` | Retrieve the run's audit trace. |
| `GET` | `/api/stats` and `/api/dashboard` | Return dashboard counts and currency-grouped totals. |
| `GET` | `/api/vendors` | List vendors. |
| `GET` | `/api/purchase-orders` | List purchase orders and line items. |
| `POST` | `/api/reset` | Reset demo runs and restore seeded PO balances. |

Review requests require `action` (`APPROVE` or `REJECT`), `reviewer_name`, and a non-empty `reviewer_note`. Only runs awaiting manual review can be overridden, and a run can be reviewed once.

## Database lifecycle

`migrations/0001_schema.sql` defines the baseline schema, `0002_seed.sql` supplies reference data, and `0003_hybrid_and_hitl.sql` records the extraction and human-review additions. The backend startup routine applies compatible additive changes to an existing D1 database.

For a new database, apply the SQL migrations in order with Wrangler or the Cloudflare D1 console. Back up production data before schema changes. The startup compatibility routine is idempotent; it is not a substitute for backups or migration review.

## Verification

Run the deterministic backend suite from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Optional live Workers AI smoke tests run only when the required credentials are present. They make external provider requests; the normal test suite uses deterministic tests and mocks.

Check and build the frontend:

```powershell
cd frontend
npx tsc --noEmit
npm run build
```

## Deployment

### Backend on Render

Configure the Render service to use `backend/Dockerfile` with `backend` as its root directory. Add the backend environment variables in the Render service settings, including D1 credentials and `ALLOWED_ORIGINS` containing the deployed Pages origin. Add Workers AI and Supabase settings only when those services are enabled. Deploy the service and verify `/api/health` before directing production traffic to it.

### Frontend on Cloudflare Pages

Configure the Pages project with `frontend` as its root directory and `npm run build` as the build command. Set `VITE_API_BASE_URL` to the public Render API origin in the Pages build environment. Deploy the Pages project, then verify the live page, API health, a sample scenario, an invoice upload, dashboard history, and the review flow.

Apply required database changes before releasing code that depends on new fields. Keep all service credentials in backend or hosting environment settings; only the public API origin belongs in the frontend build configuration.

## Security and operational boundaries

Reviewer names and notes are supplied by the user and are not authenticated identities. The demo reset endpoint is intended for controlled demonstration use. Production deployments should enforce authenticated roles and approval limits, restrict reset access, and retain audit data according to the organization's retention policy.

## License

Private. All rights reserved.
