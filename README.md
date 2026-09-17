# Zamp AP — Invoice Intelligence Engine

Production-grade accounts payable automation that processes supplier invoices through a six-stage validation pipeline, matches them against purchase orders, detects duplicates, and delivers defensible approve/reject decisions with full audit trails.

**Live:** [invoice-engine.pages.dev](https://invoice-engine.pages.dev) · **API:** [invoice-engine-api.onrender.com](https://invoice-engine-api.onrender.com/api/health) · **Repo:** [github.com/subham212/Invoice-Engine](https://github.com/subham212/Invoice-Engine)

---

## What It Does

Upload a PDF invoice (or pick from five built-in test scenarios) and the engine will:

1. **Extract** — Parse invoice fields, line items, and totals from the PDF using regex-based text extraction
2. **Normalize** — Validate required fields, cast types, compute missing subtotals
3. **Vendor Guard** — Match the vendor against a known vendor database using fuzzy name matching and tax ID lookup
4. **Duplicate Detection** — Compare against historical invoice runs to flag repeated submissions
5. **PO Matching** — Reconcile line items against the linked purchase order with configurable tolerance rules
6. **Decision** — Apply a deterministic rules engine to produce one of four outcomes:

| Decision | Meaning |
|---|---|
| `APPROVED` | All checks passed |
| `APPROVED_WITH_WARNING` | Passed with noted concerns (partial billing, missing PO) |
| `MANUAL_REVIEW` | Requires human review (vendor confidence low, price variance) |
| `REJECTED` | Hard failure (duplicate detected) |

Every run produces a full JSON audit trace that can be exported for compliance.

---

## Architecture

```
┌─────────────────────┐      SSE / REST       ┌──────────────────────┐
│   React SPA         │ ◄──────────────────►  │   FastAPI Backend    │
│   Cloudflare Pages  │                       │   Render (Docker)    │
└─────────────────────┘                       └──────┬───────┬───────┘
                                                     │       │
                                              D1 REST API   Storage API
                                                     │       │
                                              ┌──────▼──┐  ┌─▼──────────┐
                                              │  D1     │  │  Supabase  │
                                              │ (SQLite)│  │  Storage   │
                                              └─────────┘  └────────────┘
```

- **Frontend** — React 19, Tailwind CSS 4, Recharts, pdfjs-dist. Single-page app with real-time pipeline visualization via Server-Sent Events.
- **Backend** — Python 3.12, FastAPI, pdfplumber. Stateless API server with a modular processing engine. No LLM dependency; all extraction and matching is deterministic.
- **Database** — Cloudflare D1 (serverless SQLite). Four tables: vendors, purchase orders, PO line items, invoice runs.
- **File Storage** — Supabase Storage for uploaded PDF invoices.

---

## Test Scenarios

Five predefined scenarios ship with sample PDFs to demonstrate every decision path:

| # | Scenario | What It Tests | Expected Decision |
|---|---|---|---|
| 1 | Happy path | Clean three-way match | `APPROVED` |
| 2 | Partial billing | Invoice covers part of an open PO | `APPROVED_WITH_WARNING` |
| 3 | Price creep | Unit price variance against PO | `MANUAL_REVIEW` |
| 4 | Duplicate invoice | Repeated invoice number + vendor | `REJECTED` |
| 5 | Missing PO | No resolvable purchase order | `APPROVED_WITH_WARNING` |

Custom PDF upload is also supported.

---

## API Endpoints

All routes are prefixed with `/api`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/scenarios` | List available scenarios |
| `GET` | `/api/scenarios/{key}/pdf` | Download scenario sample PDF |
| `POST` | `/api/process` | Process invoice (SSE streaming) |
| `POST` | `/api/upload` | Process uploaded PDF |
| `POST` | `/api/run` | Process from Supabase storage key |
| `GET` | `/api/history` | List past runs (paginated, filterable) |
| `GET` | `/api/history/{run_id}` | Get single run |
| `GET` | `/api/audit/{run_id}` | Full audit trace JSON |
| `GET` | `/api/stats` | Dashboard statistics |
| `GET` | `/api/vendors` | List vendors |
| `GET` | `/api/purchase-orders` | List POs with line items |
| `POST` | `/api/reset` | Clear all run history |

---

## Database Schema

| Table | Purpose |
|---|---|
| `vendors` | Supplier registry (name, tax ID, status, payment terms) |
| `purchase_orders` | POs linked to vendors with amount tracking |
| `po_line_items` | Individual line items per PO (item code, qty, unit price) |
| `invoice_runs` | Full audit trail of every processed invoice |

Seeded with 5 vendors, 5 purchase orders, 5 line items, and 1 historical run (for duplicate detection).

---

## Project Structure

```
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point, CORS, health
│   │   ├── api/routes.py              # All route handlers
│   │   ├── core/
│   │   │   ├── config.py              # Pydantic settings
│   │   │   ├── d1_client.py           # Cloudflare D1 HTTP client
│   │   │   └── supabase_storage.py    # Supabase Storage client
│   │   ├── data/scenarios.py          # Scenario metadata
│   │   ├── engine/
│   │   │   ├── pipeline.py            # Processing orchestrator
│   │   │   ├── extractor.py           # PDF parsing + normalization
│   │   │   ├── vendor_guard.py        # Vendor matching
│   │   │   ├── duplicate_detector.py  # Duplicate detection
│   │   │   ├── po_matcher.py          # PO matching + reconciliation
│   │   │   ├── tolerance_rules.py     # Arithmetic validation
│   │   │   └── decision_arbiter.py    # Decision logic
│   │   └── models/models.py           # Pydantic models and enums
│   ├── generated_samples/             # Prebuilt scenario PDFs
│   └── scripts/                       # PDF generation and upload utilities
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── wrangler.jsonc
│   └── src/
│       ├── App.tsx                    # All components and routing
│       ├── services.ts                # API client and types
│       ├── index.css                  # Tailwind styles
│       └── main.tsx                   # React entry point
└── migrations/
    ├── 0001_schema.sql                # Table definitions and indexes
    └── 0002_seed.sql                  # Seed data
```

---

## Local Development

### Prerequisites

- Python 3.12+
- Node.js 20+
- Cloudflare account (D1 database provisioned)
- Supabase project (storage bucket created)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
cp .env.example .env          # Fill in your credentials
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env          # Set VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev                   # Starts on http://localhost:5173
```

### Environment Variables

**Backend** (`backend/.env`):

```
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
D1_DATABASE_ID=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_STORAGE_BUCKET=invoices
ALLOWED_ORIGINS=http://localhost:5173
ENVIRONMENT=development
```

**Frontend** (`frontend/.env`):

```
VITE_API_BASE_URL=http://127.0.0.1:8000
```

---

## Deployment

| Component | Platform | Config |
|---|---|---|
| Backend | Render | Docker, root directory `backend`, auto-deploy from `master` |
| Frontend | Cloudflare Pages | Vite build output, `wrangler pages deploy` |
| Database | Cloudflare D1 | Migrations applied via D1 console or Wrangler |
| Storage | Supabase | `invoices` bucket for PDF uploads |

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `d` | Switch to Dashboard |
| `r` | Switch to Live Run |
| `1`–`5` | Select scenario 1–5 |

---

## Tech Stack

**Backend:** Python 3.12 · FastAPI · pdfplumber · Pydantic v2 · httpx · sse-starlette · uvicorn

**Frontend:** React 19 · TypeScript 6 · Tailwind CSS 4 · Vite 8 · Recharts · pdfjs-dist · Framer Motion · Lucide Icons

**Infrastructure:** Cloudflare D1 · Cloudflare Pages · Supabase Storage · Render · Docker

---

## License

Private. All rights reserved.
