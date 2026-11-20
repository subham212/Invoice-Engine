# Zamp AP Invoice Intelligence Engine

Invoice intake, extraction, validation, purchase order reconciliation, and review backed by an auditable decision trace.

The engine extracts invoice fields from text PDFs and scanned documents, checks them against vendor, duplicate, and purchase order records, then returns a deterministic decision. Cloudflare Workers AI is used for structured extraction when configured; local PDF text parsing and deterministic normalization remain the fallback path.

## Capabilities

- Upload PDF, PNG, JPG/JPEG, or WEBP invoices (10 MB maximum), or process built-in scenarios.
- Extract invoice identifiers, vendor details, dates, currency, totals, tax, and line items, with field confidence and extraction provenance.
- Validate arithmetic and vendor status, detect duplicates, reconcile invoice lines and cumulative PO utilization, and record workflow stages.
- Route uncertain or exceptional cases to `MANUAL_REVIEW`; an AP reviewer can approve or reject a pending run with a required name and note.
- Persist run history, audit traces, extraction metadata, review decisions, and PO utilization in Cloudflare D1.
- View amounts grouped by currency in dashboard statistics; the combined total is informational because it does not convert currencies.

### Decisions

| Decision | Meaning |
|---|---|
| `APPROVED` | Deterministic checks passed. |
| `APPROVED_WITH_WARNING` | Processing passed with a recorded warning, such as partial billing. |
| `MANUAL_REVIEW` | A human decision is required. |
| `REJECTED` | A hard validation, vendor, duplicate, or PO limit failure occurred. |

## System layout

```text
React + Vite frontend
       │ REST / Server-Sent Events
       ▼
FastAPI backend ───── Cloudflare Workers AI (optional extraction provider)
       ├───────────── Cloudflare D1 (runs, vendors, purchase orders, audit)
       └───────────── Supabase Storage (optional source-file retrieval)
```

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Recharts, and PDF.js.
- **Backend:** Python 3.12+, FastAPI, Pydantic, pdfplumber, pypdfium2, Pillow, and httpx.
- **Database:** Cloudflare D1 using its REST API. Startup applies the additive/idempotent compatibility changes in `backend/app/core/d1_client.py`; SQL migration history is in `migrations/`.
- **Storage:** Supabase Storage is used for processing by object key. Direct uploads are processed from the request body.

## Repository map

```text
backend/
  app/api/routes.py                 HTTP, upload, persistence, dashboard, and review routes
  app/core/config.py                Settings and environment configuration
  app/core/d1_client.py             D1 REST access and schema compatibility
  app/core/supabase_storage.py      Supabase Storage access
  app/engine/extractor.py           Text and scanned-document extraction
  app/engine/pipeline.py            Processing orchestration and trace creation
  app/engine/                       Vendor, duplicate, PO, arithmetic, and decision rules
  app/models/models.py              API, domain, and extraction schemas
  app/data/scenarios.py             Built-in scenario definitions
  tests/test_pipeline.py            Pipeline, persistence, review, and extraction tests
  scripts/                          Sample generation and upload utilities
frontend/src/
  App.tsx                            Main application UI
  services.ts                       API calls and shared frontend types
migrations/                          D1 schema and seed SQL
```

## Scenario tabs

Scenario definitions are in `backend/app/data/scenarios.py`; sample PDFs live in `backend/generated_samples/` and can be regenerated with the backend script.

| Tab | Expected result |
|---|---|
| Happy path | `APPROVED` |
| Partial billing | `APPROVED_WITH_WARNING` |
| Price creep | `MANUAL_REVIEW` |
| Duplicate invoice | `REJECTED` |
| Missing PO | `MANUAL_REVIEW` |
| Scanned PDF | `APPROVED_WITH_WARNING` |
| Upload Invoice | User-supplied PDF or image; result follows the invoice checks |

The scanned sample is an image-only PDF. Vision extraction defaults to Gemma 4 (`@cf/google/gemma-4-26b-a4b-it`) with Llama 4 Scout (`@cf/meta/llama-4-scout-17b-16e-instruct`) as fallback. The SSE upload stream sends a keepalive every 10 seconds during long extraction calls.

## Local setup

### Requirements

- Python 3.12 or newer
- Node.js and npm compatible with the versions in `frontend/package.json`
- A Cloudflare account and D1 database for persisted application use
- Cloudflare Workers AI credentials for AI extraction
- Supabase credentials only when processing invoices from Supabase object keys

### Backend

From the repository root:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set the environment variables in `backend/.env`, then run:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On macOS/Linux, activate with `source .venv/bin/activate`. The API health check is `http://127.0.0.1:8000/api/health`.

### Frontend

In a second terminal, from the repository root:

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Set `VITE_API_BASE_URL=http://127.0.0.1:8000` in `frontend/.env` if it is not already present. Vite prints the local URL after startup.

## Configuration

Copy `backend/.env.example` to `backend/.env`. The main settings are:

| Variable | Purpose |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account containing D1 and Workers AI. |
| `CLOUDFLARE_API_TOKEN` | D1 API access token. |
| `D1_DATABASE_ID` | Target D1 database identifier. |
| `CLOUDFLARE_AI_API_TOKEN` | Workers AI token; `CLOUDFLARE_API_KEY` is the compatibility fallback. |
| `CF_AI_VISION_MODEL` | Primary vision extraction model. |
| `CF_AI_FALLBACK_VISION_MODEL` | Vision model used if the primary call fails. |
| `CF_AI_TEXT_MODEL` | Text extraction model. |
| `SUPABASE_URL` | Supabase project URL. |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side Supabase Storage credential. |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket name (defaults to `invoices`). |
| `ALLOWED_ORIGINS` | Allowed browser origins, comma-separated. |
| `ENVIRONMENT` | Runtime environment label. |

The default Workers AI model IDs are declared in `backend/.env.example`. The API health response reports whether AI credentials are configured; it does not make a provider request.

The Cloudflare API token needs **D1: Edit** for the configured database and **Workers AI: Read** for extraction. `CLOUDFLARE_API_TOKEN` is also used by the existing D1 client; the AI client resolves its credential in this order: `CLOUDFLARE_AI_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, then `CLOUDFLARE_API_KEY`. Restrict tokens to the required account and resources.

Frontend configuration is `VITE_API_BASE_URL` in `frontend/.env`.

## HTTP API

All routes use the `/api` prefix. Interactive API documentation is available at `/docs` while the backend is running.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service and AI configuration status. |
| `GET` | `/api/scenarios` | List built-in scenarios. |
| `GET` | `/api/scenarios/{scenario_key}/pdf` | Download a scenario PDF. |
| `POST` | `/api/process` | Process a scenario or upload and stream workflow events over SSE. |
| `POST` | `/api/upload` | Process an uploaded invoice. |
| `POST` | `/api/run` | Process a file referenced by Supabase storage key. |
| `GET` | `/api/history` | List persisted runs. |
| `GET` | `/api/history/{run_id}` | Read a persisted run. |
| `POST` | `/api/runs/{run_id}/review` | Record an approve/reject override for a pending manual review. |
| `GET` | `/api/audit/{run_id}` | Read the full stored trace. |
| `GET` | `/api/stats` | Dashboard counts and totals grouped by currency. |
| `GET` | `/api/vendors` | List vendor records. |
| `GET` | `/api/purchase-orders` | List purchase orders and their lines. |
| `POST` | `/api/reset` | Clear test runs, retain the duplicate seed, and restore seeded PO utilization baselines. |

Review requests require `action` (`APPROVE` or `REJECT`), `reviewer_name`, and `reviewer_note`. Only a run currently marked `MANUAL_REVIEW` can be reviewed, and it can only be reviewed once.

## Database changes

The baseline schema and seed data are in `migrations/0001_schema.sql` and `migrations/0002_seed.sql`. `migrations/0003_hybrid_and_hitl.sql` documents the new extraction, review, and PO utilization fields. The backend startup compatibility routine applies the required changes idempotently for an existing D1 database and preserves existing records.

For a new database, apply the SQL migrations in order using Wrangler or the Cloudflare D1 console. Configure the corresponding D1 credentials before starting the backend. Existing deployments should back up D1 before applying schema changes.

## Verification

Run the backend test suite from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

The optional live Workers AI smoke test runs only when `CLOUDFLARE_ACCOUNT_ID` and an AI token are configured. Frontend type checking and production build:

```powershell
cd frontend
npx tsc --noEmit
npm run build
```

## Deployment

### Render backend

1. Create or update the Render web service with the repository's `backend/Dockerfile` and `backend` as its root directory.
2. Configure the Cloudflare D1 account ID, API token, and database ID. The token needs **D1: Edit** and **Workers AI: Read** permissions.
3. Configure `CLOUDFLARE_AI_API_TOKEN` if using a separate Workers AI token; otherwise the AI integration can reuse `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_API_KEY`.
4. Set `ALLOWED_ORIGINS` to the deployed Pages origin, plus any required local development origin. Set the Workers AI model variables only when overriding the defaults.
5. Set Supabase URL, service role key, and bucket only if the application will retrieve invoices from Supabase Storage.
6. Deploy the backend and confirm `/api/health` reports `status: ok` and the expected AI configuration status. Startup applies the idempotent D1 compatibility changes.

### Cloudflare Pages frontend

1. Create or update the Pages project with `frontend` as its root directory and `npm run build` as the build command.
2. Set `VITE_API_BASE_URL` to the public Render API origin in the Pages build environment.
3. Build and deploy the Pages project, then confirm its origin is included in the backend's `ALLOWED_ORIGINS`.
4. Verify a scenario run, a custom upload, dashboard history, and the manual review flow against the deployed services.

Before deploying a release that expects new D1 fields, back up the database and apply the ordered SQL migrations for a fresh database. Existing installations are upgraded by the backend's idempotent startup compatibility routine. Keep Supabase service role credentials and Cloudflare tokens on the backend; never place them in frontend variables.

## Production Readiness & Security Next Steps

- Use SSO and server-side RBAC for HITL overrides; enforce approval limits and retain an immutable audit trail.
- Reviewer names and notes are demo inputs, not authenticated identities.
- Disable the demo reset control or restrict it to authorized operators in an enterprise rollout.

## License

Private. All rights reserved.
