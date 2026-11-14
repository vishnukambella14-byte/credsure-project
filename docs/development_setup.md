# CredSure: development environment setup

This document covers the local development environment: requirements, configuration, and how
each tier is verified. For quick-start commands, see the root [README](../README.md).

## 1. Environment requirements

| Requirement | Version used during setup | Notes |
|-------------|---------------------------|-------|
| OS          | Windows 11                | PowerShell 7+ or Windows PowerShell 5.1 |
| Python      | 3.10.0                    | 3.10+ required (code uses `X \| Y` type unions) |
| Node.js     | 22.14.0                   | Vite 8 requires Node 20.19+ or 22.12+ |
| npm         | 10.9.2                    | |
| Git         | 2.56                      | |
| Editor      | Visual Studio Code        | Optional: Python, ESLint and Tailwind CSS IntelliSense extensions |

Not used, by design: PostgreSQL, Docker, Docker Compose and cloud services. Everything runs
directly on the local machine. Python packages are installed only inside `backend/venv`.

## 2. Backend (FastAPI)

### Virtual environment

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Dependencies (`backend/requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.162.5 | Web framework |
| uvicorn[standard] | 0.54.0 | ASGI server |
| python-multipart | 0.0.32 | Form and file upload parsing |
| pydantic | 2.14.3 | Data validation |
| pydantic-settings | 2.15.0 | Environment-based settings |
| SQLAlchemy | 2.0.57 | ORM and engine |
| alembic | 1.20.1 | Schema migrations |
| cryptography | 50.0.2 | SHA-256 hashing and Ed25519 signatures (future use) |
| pytest | 9.1.1 | Test runner (9.2.0 crashes on Python 3.10; see section 7) |
| httpx | 0.28.1 | HTTP client for FastAPI `TestClient` |

### Configuration

Settings live in `app/core/config.py` (pydantic-settings). Values are read from environment
variables prefixed `CREDSURE_`, then from `backend/.env`, then from defaults. Copy
`.env.example` to `.env` to override them. **Never commit `.env` or key material.**

| Variable | Default |
|----------|---------|
| `CREDSURE_ENVIRONMENT` | `development` |
| `CREDSURE_DEBUG` | `false` |
| `CREDSURE_LOG_LEVEL` | `INFO` |
| `CREDSURE_DATABASE_URL` | `sqlite:///<absolute path>/backend/data/credsure.db` |
| `CREDSURE_DATABASE_ECHO` | `false` |
| `CREDSURE_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |

### Application structure

- `app/main.py`: `create_app()` factory. Sets up logging, CORS, exception handlers and the `/api` router; the lifespan hook checks the database on startup.
- `app/api/router.py`: aggregates feature routers. `routes/health.py` provides `GET /api/health`.
- `app/core/logging.py`: one console handler with a timestamped format.
- `app/core/errors.py`: consistent error envelope `{"error": {"code", "message", "details?"}}` for HTTP errors, validation errors (422) and unhandled exceptions (500, logged with a stack trace).
- `app/database/`: engine, session factory, `get_db` dependency, `transaction_scope()`, and `get_database_status()`.

### Health endpoint

`GET /api/health` queries the database on each call:

| Database state | HTTP | Response |
|----------------|------|----------|
| Reachable, no Alembic revision | 200 | `{"status": "healthy", "application": "CredSure", "database": "not_initialized"}` |
| Reachable, revision applied | 200 | `... "database": "initialized"` |
| Connection or query failure | 503 | `{"status": "unhealthy", ..., "database": "unavailable"}` |

### Start

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

OpenAPI docs: http://127.0.0.1:8000/docs

## 3. Database (SQLite)

- File: `backend/data/credsure.db`, created on first connection. The directory is created automatically.
- Every connection runs `PRAGMA foreign_keys=ON`, `journal_mode=WAL` and `busy_timeout=30000`.
- Alembic environment: `database/migrations/`, config in `backend/alembic.ini`. Batch mode is enabled for SQLite.
- No business tables exist yet. See [database/README.md](../database/README.md).

## 4. Frontend (React + Vite)

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173
```

- `vite.config.ts` registers `@vitejs/plugin-react` and `@tailwindcss/vite` (Tailwind v4: no `tailwind.config.js` or PostCSS config needed). It proxies `/api` to `http://127.0.0.1:8000` and configures Vitest.
- `src/index.css` contains `@import 'tailwindcss';` and is imported by `src/main.tsx`.
- `src/App.tsx` renders a temporary setup screen. The real dashboard is not built yet.
- Icons: `lucide-react`.

| Script | Purpose |
|--------|---------|
| `npm run dev` | Dev server with HMR |
| `npm run build` | `tsc -b` type-check and production build to `dist/` |
| `npm run lint` | ESLint (flat config) |
| `npm run test` | Vitest (single run) |
| `npm run preview` | Serve the production build |

## 5. Testing

| Suite | Command | Location | Notes |
|-------|---------|----------|-------|
| Backend | `python -m pytest` (in `backend/`, venv active) | `backend/tests/` | `conftest.py` sets `CREDSURE_DATABASE_URL` to a temp file **before** importing the app, so the dev DB is never touched |
| Frontend | `npm run test` (in `frontend/`) | `frontend/src/**/*.test.tsx` | Renders components with `react-dom/server`; no DOM emulator needed yet |

Current backend coverage: the health endpoint (normal and unavailable cases), the error envelope,
CORS for the Vite origin, test-database isolation, database file creation, `PRAGMA foreign_keys`
on every connection, and rejection of a foreign-key violation.

## 6. Version control

- The root `.gitignore` excludes virtual environments, `__pycache__`, `.env*` (except `.env.example`), `node_modules`, `frontend/dist`, SQLite files, `data/generated/*`, private keys and certificates, and test caches.
- `.gitkeep` files keep the otherwise-empty directories in the repository.

## 7. Known issues

- **pytest 9.2.0** fails at startup on Python 3.10 (`AttributeError: 'NodeId' object has no attribute '_str_cache'`), even with a trivial test. `requirements.txt` pins `pytest==9.1.1`.
- **Starlette deprecation warning:** Starlette 1.7 warns that using `httpx` with its `TestClient` is deprecated in favour of `httpx2`. Tests pass; the project keeps `httpx` as specified.
- **npm audit:** one moderate advisory in `minimatch`, pulled in by dev-only `typescript-eslint`. It does not affect the shipped bundle. The suggested `--force` fix downgrades `typescript-eslint`, so it has not been applied.
