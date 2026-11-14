# CredSure database

CredSure uses **SQLite** through **SQLAlchemy 2.x**, with schema changes managed by **Alembic**.

## Location

| Purpose      | Path |
|--------------|------|
| Development  | `backend/data/credsure.db` (default; override with `CREDSURE_DATABASE_URL`) |
| Tests        | Temporary files: one per pytest session for the app engine, plus one fresh file per schema/migration test (migrated with the real `alembic upgrade head`) |
| Migrations   | `database/migrations/` (Alembic `env.py`, `script.py.mako`, `versions/`) |
| Alembic config | `backend/alembic.ini` |

The database file and its `-wal`/`-shm` companions are git-ignored.

## Connection settings

Configured in `backend/app/database/session.py`. Every new connection runs:

- `PRAGMA foreign_keys=ON`, because SQLite does not enforce foreign keys by default
- `PRAGMA journal_mode=WAL`, for better read/write concurrency during local development
- `PRAGMA busy_timeout=30000`
- `PRAGMA recursive_triggers=ON`, so `INSERT OR REPLACE` cannot silently bypass the delete triggers (D3)

The driver's implicit transactions are disabled (`isolation_level = None`). The engine `begin`
event emits `BEGIN IMMEDIATE` for `WriteSessionLocal` / `transaction_scope()` / `get_write_db`
(write lock from the first statement) and `BEGIN` for `SessionLocal` / `get_db` (read-only). See
[ADR-019](../docs/architecture_decisions.md#adr-019-sqlite-transaction-configuration).

The engine uses `check_same_thread=False` so FastAPI worker threads can share pooled connections.

## Migrations

Run Alembic from the `backend/` directory with the virtual environment active.
The URL comes from the application settings, not from `alembic.ini`.

```powershell
cd backend
.\venv\Scripts\Activate.ps1

alembic current                                   # show applied revision
alembic revision --autogenerate -m "describe change"
alembic upgrade head                              # apply migrations
alembic downgrade -1                              # roll back one revision
```

Migrations are generated with `render_as_batch=True`, which SQLite needs for `ALTER TABLE`
operations. Models must be imported in `backend/app/models/__init__.py` for autogenerate to
detect them. `env.py` renders `UTCDateTime` as `sa.DateTime()`, so migration files never import
application column types. Every `CheckConstraint` needs an explicit `name`, or autogenerate fails
with the project's naming convention.

## Current state

| Revision | Content |
|----------|---------|
| `0001` (`versions/0001_initial_schema.py`) | The nine tables of [data_dictionary §3](../docs/data_dictionary.md#3-tables) with the final constraint set (D1), plus 16 triggers and 2 raw-SQL indexes from [data_dictionary §6](../docs/data_dictionary.md#6-reference-ddl-triggers-and-expression-indexes) |

`GET /api/health` reports `"database": "initialized"` once `0001` is applied.

### Triggers and future migrations

- Alembic autogenerate **never** creates triggers. They live in `backend/app/database/triggers.py`, which is generated from the documented SQL. A test asserts the two stay identical.
- Migration `0001` applies the frozen constants `SCHEMA_0001_CREATE` at the end of `upgrade()` and `SCHEMA_0001_DROP` at the start of `downgrade()`.
- **A SQLite batch rebuild ("move and copy") silently drops every trigger on the rebuilt table.** Any future migration that batch-alters a table in `triggers.TRIGGER_TABLES` must call `triggers.reapply_for_table(op.execute, "<table>")` afterwards. `tests/test_migrations.py` asserts the full trigger inventory at head, so a forgotten re-apply fails the test suite.
- Autogenerate ignores the two raw-SQL indexes (`include_object` in `env.py`). The warning `Skipped unsupported reflection of expression-based index uq_audit_events_single_genesis` during `alembic check` is expected.
- Never use `INSERT OR REPLACE` / upserts on `issuers`, `credential_rules`, `credentials`, `revocations`, `verification_requests` or `audit_events`.
