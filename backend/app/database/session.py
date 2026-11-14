"""SQLAlchemy engine, session factories and database status helpers for SQLite.

Transaction model (decision D3, ADR-019):

* Every connection runs ``foreign_keys=ON``, ``journal_mode=WAL``, ``busy_timeout`` and
  ``recursive_triggers=ON`` (the latter stops ``INSERT OR REPLACE`` from silently bypassing
  the delete triggers that protect immutable tables).
* pysqlite's implicit transaction handling is disabled (``isolation_level = None``) and the
  engine ``begin`` event emits the transaction explicitly: ``BEGIN IMMEDIATE`` for connections
  carrying the execution option ``sqlite_write=True`` (``WriteSessionLocal``), plain ``BEGIN``
  otherwise (``SessionLocal``). Write-critical operations therefore hold the write lock from
  their first statement, including the reads that precede their inserts.
* Immutable and audit-sensitive tables are written with plain ``INSERT`` only.
"""

import logging
import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class DatabaseStatus(str, Enum):
    INITIALIZED = "initialized"  # reachable and at least one Alembic migration applied
    NOT_INITIALIZED = "not_initialized"  # reachable, but no schema migrations applied yet
    UNAVAILABLE = "unavailable"  # connection or query failed


def _ensure_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


WRITE_OPTION = "sqlite_write"
DEFAULT_BUSY_TIMEOUT_MS = 30_000


def build_engine(
    database_url: str, echo: bool = False, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> Engine:
    """Create an engine with the CredSure SQLite connection and transaction configuration."""
    _ensure_sqlite_directory(database_url)
    is_sqlite = make_url(database_url).get_backend_name() == "sqlite"
    engine = create_engine(
        database_url,
        echo=echo,
        # FastAPI may use a session from a worker thread other than the one that created it.
        connect_args=(
            {"check_same_thread": False, "timeout": busy_timeout_ms / 1000} if is_sqlite else {}
        ),
        pool_pre_ping=True,
    )
    if is_sqlite:
        event.listen(engine, "connect", _sqlite_on_connect(busy_timeout_ms))
        event.listen(engine, "begin", _sqlite_on_begin)
    return engine


def _sqlite_on_connect(busy_timeout_ms: int):  # noqa: ANN202 - SQLAlchemy listener factory
    def on_connect(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
        """Runs for every new DBAPI connection."""
        # Disable pysqlite's implicit BEGIN (it only starts transactions before DML, so reads
        # preceding a write would run outside the transaction). ``_sqlite_on_begin`` emits it.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            cursor.execute("PRAGMA recursive_triggers=ON")
        finally:
            cursor.close()

    return on_connect


def _sqlite_on_begin(conn: Connection) -> None:
    """Emit the transaction start explicitly: IMMEDIATE (write lock now) for write sessions."""
    if conn.get_execution_options().get(WRITE_OPTION, False):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        conn.exec_driver_sql("BEGIN")


def write_engine(base_engine: Engine) -> Engine:
    """The same engine (shared pool and listeners) whose transactions start with BEGIN IMMEDIATE."""
    return base_engine.execution_options(**{WRITE_OPTION: True})


_settings = get_settings()
engine: Engine = build_engine(_settings.database_url, echo=_settings.database_echo)
# Read-only work: deferred BEGIN, concurrent with other readers under WAL.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
# Every write-critical operation (ADR-019 lists them): BEGIN IMMEDIATE, serialised writers.
WriteSessionLocal = sessionmaker(bind=write_engine(engine), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for read-only requests: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_write_db() -> Generator[Session, None, None]:
    """FastAPI dependency for write requests: BEGIN IMMEDIATE, committed by the caller."""
    db = WriteSessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def transaction_scope() -> Iterator[Session]:
    """Write unit of work for services/scripts: BEGIN IMMEDIATE, commit on success, else roll back."""
    db = WriteSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_database_status(target_engine: Engine | None = None) -> DatabaseStatus:
    """Inspect the real database instead of assuming it is healthy."""
    target_engine = target_engine or engine
    try:
        with target_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if not inspect(conn).has_table("alembic_version"):
                return DatabaseStatus.NOT_INITIALIZED
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            return DatabaseStatus.INITIALIZED if revision else DatabaseStatus.NOT_INITIALIZED
    except Exception:
        logger.exception("Database status check failed")
        return DatabaseStatus.UNAVAILABLE
