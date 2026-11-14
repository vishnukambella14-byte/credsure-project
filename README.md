# CredSure

**Digital Credential Lifecycle Management Platform**

CredSure issues credentials only after learners meet versioned course rules. It signs every
credential (canonical JSON → SHA-256 → Ed25519), lets third parties verify credentials publicly,
revokes them permanently, and keeps an append-only, hash-chained audit log. It also generates
reproducible synthetic data with injected anomalies, detects those anomalies, and scores the
detectors against ground truth.

> **Status:** roadmap tasks T01–T08 are implemented and covered by automated tests (T09:
> documentation and readiness).
> **Manual browser click-through of the UI has not been performed yet.** No browser-control
> tool was available. The demo workflow passed at API level and all automated tests pass. See
> [Known issues](#known-issues-and-limitations).

---

## What is implemented

| Area | Capability | Roadmap |
|------|------------|---------|
| Schema | Nine tables, final constraint set (composite FKs, verification-consistency and rule-state CHECKs), 16 immutability and audit-chain triggers; Alembic revision `0001` | T02 |
| Core services | Canonical JSON, SHA-256, Ed25519, ID generation, hash-chained audit with chain verification, BR-04 eligibility with completion-time rule selection, simulated roles and permissions, documented error mapping | T03 |
| Credential lifecycle | Issuance (D7 check order, attempt references), public verification (BR-13 precedence), atomic irreversible revocation, rule activation with the D5 date guard; audit and eligibility APIs | T04 |
| Master data | Create and retrieve issuers (with key generation), learners, courses, draft rules and completion records; public issuer key | T05 |
| Synthetic data | Seeded generator (seed `20261022`, `starter` and `full` profiles), A1–A4 injection with separate ground truth, manifest with checksums, loader with issuance replay | T06 |
| Analytics | Live metrics; detectors A1–A4; evaluation (TP, FP, FN, TN, precision, recall, F1) | T07 |
| Frontend | Role switcher; Dashboard, Issue credential, Credential detail, Verify and Anomalies screens | T08 |

## Architecture

A three-tier **modular monolith** that runs locally on Windows. No Docker, no cloud, no microservices.

```
┌──────────────────────┐   REST/JSON   ┌──────────────────────┐   SQLAlchemy   ┌──────────────────┐
│ Tier 1: Frontend     │ ────────────▶ │ Tier 2: Backend      │ ─────────────▶ │ Tier 3: Database │
│ React + TS + Vite    │   /api/*      │ FastAPI + Uvicorn    │      ORM       │ SQLite (WAL)     │
│ http://localhost:5173│  (Vite proxy) │ http://127.0.0.1:8000│                │ backend/data/    │
└──────────────────────┘               └──────────────────────┘                └──────────────────┘
```

- **Backend layers:** `api/routes` (thin routers, role checks) → `services` (all business rules) → `models` / `database`.
- **Write transactions** use `BEGIN IMMEDIATE`. Every connection enables foreign keys and `recursive_triggers`.
- **Private keys** live only in git-ignored files under `backend/keys/`: `platform/` for platform issuers, `synthetic/<profile>/` for synthetic issuers. The database stores only public keys.
- **Roles are simulated** with `X-Actor-Id` / `X-Actor-Role` headers (ADR-010). There is no real authentication.

Details: [docs/architecture.md](docs/architecture.md) and [docs/architecture_decisions.md](docs/architecture_decisions.md) (ADR-001 – ADR-024).

### Repository layout

```
.
├── frontend/                 React 19 + TypeScript + Vite 8 + Tailwind CSS 4
│   └── src/
│       ├── api/              Typed API client and schema types
│       ├── components/       UI primitives, role switcher, presentational views
│       ├── pages/            Dashboard, IssueCredential, CredentialDetail, Verify, Anomalies
│       └── App.tsx           Shell and state-based navigation
├── backend/
│   ├── app/
│   │   ├── api/              Routers (health, credentials, verifications, lifecycle, master_data, analytics)
│   │   ├── core/             Settings, errors, security (roles), time helpers
│   │   ├── database/         Engine/sessions, UTCDateTime type, trigger DDL
│   │   ├── models/           Nine ORM models and enums
│   │   ├── schemas/          Pydantic request/response models
│   │   ├── services/         crypto, ids, audit, eligibility, issuance, verification,
│   │   │                     revocation, rules, master_data, metrics, anomalies/
│   │   └── datagen/          Synthetic data generator, injector, loader, CLI
│   ├── tests/                pytest suite (temporary databases only)
│   ├── keys/                 Issuer private keys (git-ignored except README.md)
│   └── data/                 Development SQLite database (git-ignored)
├── database/migrations/      Alembic environment and revision 0001
├── data/
│   ├── generated/<profile>/  Synthetic input CSVs and manifest (git-ignored)
│   └── ground_truth/<profile>/ anomaly_labels.csv
└── docs/                     Specification, decisions, review, demo script
```

## Technology stack

| Layer      | Technologies |
|------------|--------------|
| Frontend   | React 19, TypeScript 6, Vite 8, Tailwind CSS 4 (`@tailwindcss/vite`), lucide-react, ESLint, Vitest |
| Backend    | Python 3.10+, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2, Alembic |
| Database   | SQLite 3.35+ (bundled with Python), WAL journal, triggers, partial and expression indexes |
| Security   | `cryptography` (Ed25519), `hashlib` (SHA-256) |
| Testing    | pytest 9.1, HTTPX (FastAPI `TestClient`), Vitest 5 |

## Prerequisites

- Windows 11 with PowerShell
- Python **3.10 or newer**
- Node.js **20.19+ or 22.12+** and npm
- Git

## Installation

```powershell
# Backend
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env        # optional; every setting has a default
python -m alembic upgrade head     # creates the schema in backend/data/credsure.db

# Frontend
cd ..\frontend
npm install
```

If PowerShell blocks activation, run once:
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

## Running the application

```powershell
# Terminal 1: backend (http://127.0.0.1:8000, OpenAPI docs at /docs)
cd backend
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2: frontend (http://localhost:5173)
cd frontend
npm run dev
```

`Invoke-RestMethod http://127.0.0.1:8000/api/health` reports `database: initialized` once the
migration has been applied.

The UI has no master-data forms (a "could" item, FR-30). The end-to-end demo therefore seeds a
platform issuer, learner, course, rule and completions through the API first. See
**[docs/demo_script.md](docs/demo_script.md)**, which also uses a disposable database so the
development database stays untouched.

## Synthetic data

Run these from `backend/` with the venv active:

```powershell
python -m app.datagen generate --profile starter --seed 20261022   # or --profile full
python -m app.datagen load --profile starter            # into the configured database
python -m app.datagen load --profile starter --reset    # recreate the schema first (DELETES ALL DATA)
```

| Profile | Learners | Courses | Credentials | Verifications | A1 / A2 / A3 / A4 | Generate / load time |
|---------|---------:|--------:|------------:|--------------:|-------------------|----------------------|
| starter | 200 | 20 | 100 | 50 | 3 / 2 / 2 / 2 | < 1 s / < 1 s |
| full | 20,000 | 1,000 | 10,000 | 5,000 | 100 / 50 / 50 / 50 | ≈ 5 s / ≈ 6 s |

Output is byte-identical for the same seed and profile (SHA-256 checksums in `manifest.json`).
Synthetic issuers use `ISS-901`…`ISS-999` and keys under `backend/keys/synthetic/<profile>/`;
the generator never touches platform keys (D8). With the profile loaded, the detectors reach
precision, recall and F1 of 1.0 against ground truth on both profiles (full evaluation ≈ 1 s).

## Tests and quality checks

```powershell
# Backend (from backend/, venv active): uses temporary databases, never backend/data/credsure.db
python -m pytest

# Frontend (from frontend/)
npm run lint
npm run build
npm run test

# Optional live API demo flow (backend + Vite running and the demo data seeded, see docs/demo_script.md)
$env:VITE_DEMO_API_BASE = "http://127.0.0.1:5173/api/v1"; npm run test
```

Results of the final T09 run (2026-11-14):

| Check | Result |
|-------|--------|
| Backend `python -m pytest` | **332 passed**, 1 warning (Starlette/httpx deprecation) |
| Frontend `npm run lint` | clean, no errors or warnings |
| Frontend `npm run build` | succeeds |
| Frontend `npm run test` | **20 passed**, 1 skipped (the opt-in live demo test) |
| Live API demo flow (`demo.live.test.ts` through the Vite proxy, T08) | passed |
| Manual browser click-through of the UI | **not performed (outstanding)** |

## API overview

All business endpoints are under `/api/v1` ([docs/api_contract.md](docs/api_contract.md)):

| Group | Endpoints |
|-------|-----------|
| Issuers | `POST/GET /issuers`, `GET /issuers/{id}`, `GET /issuers/{id}/public-key` (public) |
| Learners, courses | `POST/GET /learners`, `GET /learners/{id}`, `POST/GET /courses`, `GET /courses/{id}` |
| Rules | `POST/GET /courses/{id}/rules`, `POST /rules/{id}/activate` |
| Completions, eligibility | `POST/GET /completions`, `GET /eligibility` |
| Credentials | `POST/GET /credentials`, `GET /credentials/{id}`, `GET /credentials/{id}/share`, `POST /credentials/{id}/revoke`, `GET /revocations` |
| Verification | `POST /verifications` (public), `GET /verifications`, `GET /verifications/{id}` |
| Audit | `GET /audit-events`, `GET /audit-events/verify-chain` |
| Analytics | `GET /metrics/summary`, `POST /anomalies/run`, `GET /anomalies/evaluation?profile=` |

## Local database

- Location: `backend/data/credsure.db` (override with `CREDSURE_DATABASE_URL`). The file is git-ignored.
- Schema: Alembic revision `0001` ([database/README.md](database/README.md)).
- Append-only and immutability rules are enforced by triggers. Rows in protected tables cannot be deleted; `load --reset` therefore recreates the schema instead.

## Known issues and limitations

| # | Issue | Impact / workaround |
|---|-------|---------------------|
| 1 | **Manual browser click-through not performed.** No browser-control tool was available during T08/T09. | The demo workflow passed at API level through the same client code the UI uses, via the Vite proxy against a running backend. Screens were checked by static render tests, lint and build, and Vite served every module. Layout, interaction and visual states in a real browser are **unverified**. Follow [docs/demo_script.md](docs/demo_script.md) once in a browser before presenting. |
| 2 | No master-data forms in the UI (FR-30, "could"). | Seed platform data through the API as described in the demo script. |
| 3 | Documented update endpoints were not implemented in T05: `POST /issuers/{id}/retire`, `PATCH /learners/{id}`, `PATCH /courses/{id}`, `PATCH /rules/{id}`, `PATCH /completions/{id}` (FR-07). | Create and retrieve work; corrections and issuer retirement are only possible directly in the database (where triggers permit). |
| 4 | The running API cannot issue for synthetic issuers (its key store has no dataset profile). | By design (D8): synthetic issuance happens only through the loader's replay. Use a platform issuer for live issuance. |
| 5 | Authentication is simulated with request headers (AS-01, ADR-010). | Local demo only; headers can be spoofed. |
| 6 | The audit hash chain detects application-level tampering, not an attacker with full access to the database file (ADR-020). | Documented threat-model limit. |
| 7 | `python -m app.datagen load --reset` recreates the schema and deletes all data, including platform data. | Use it only on a database you can lose. |
| 8 | `GET /verifications/{id}` returns the generic `http_404` for an unknown ID (no catalogue code exists). | Cosmetic. |
| 9 | pytest is pinned to 9.1.1, because 9.2.0 crashes at startup on Python 3.10. | See `backend/requirements.txt`. |
| 10 | Starlette warns that `httpx` with its `TestClient` is deprecated. | Warning only; tests pass. |
| 11 | `npm audit` reports a moderate `minimatch` advisory in the dev-only `typescript-eslint` toolchain. | Not shipped in the bundle; the `--force` fix would downgrade the linter. |
| 12 | `data/ground_truth/<profile>/anomaly_labels.csv` is not git-ignored (only `data/generated/*` is). | Decide whether the labels should be committed. |
| 13 | Unused Vite template files remain: `frontend/src/App.css`, `frontend/src/assets/*`, `frontend/README.md`. | Harmless; can be deleted. |

## Documentation

| Document | Contents |
|----------|----------|
| [PRD](docs/PRD.md) | Problem, goals, actors and permissions, requirements, assumptions, decision log |
| [Business rules](docs/business_rules.md) | Numbered rules with enforcement layer and tests |
| [User stories](docs/user_stories.md) | Stories per actor with acceptance criteria |
| [Architecture](docs/architecture.md) | Tiers, module layout, issuance, verification and revocation flows |
| [Data dictionary](docs/data_dictionary.md) | Nine tables, constraints, triggers DDL, payload format, dataset files |
| [Exception catalogue](docs/exception_catalogue.md) | API errors, verification outcomes, anomalies A1–A4 |
| [API contract](docs/api_contract.md) | Endpoints, roles, request and response schemas |
| [Architecture decisions](docs/architecture_decisions.md) | ADR-001 – ADR-024 |
| [Schema review](docs/schema_review.md) | Pre-implementation review and resolutions |
| [Roadmap](docs/roadmap.md) | Task plan T01–T09 |
| [Demo script](docs/demo_script.md) | End-to-end demo workflow |
| [Development setup](docs/development_setup.md) | Environment requirements |
