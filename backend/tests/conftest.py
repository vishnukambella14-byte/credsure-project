"""Test configuration.

The database URL is redirected to a throw-away SQLite file BEFORE any ``app`` module is
imported, so tests can never touch the development database at backend/data/credsure.db.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="credsure-test-"))
TEST_DATABASE_PATH = _TEST_DB_DIR / "test_credsure.db"

os.environ["CREDSURE_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["CREDSURE_ENVIRONMENT"] = "test"

from fastapi.testclient import TestClient  # noqa: E402

from app.database.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_database() -> Iterator[None]:
    yield
    engine.dispose()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def isolated_db_path() -> Path:
    return TEST_DATABASE_PATH


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fresh_engine(tmp_path: Path) -> Iterator[Engine]:
    """Engine on an empty, disposable SQLite file, built with the application configuration."""
    from app.database.session import build_engine

    db_engine = build_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}", busy_timeout_ms=2000)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def migrated_engine(fresh_engine: Engine) -> Engine:
    """``fresh_engine`` after ``alembic upgrade head`` (the real migration, not create_all)."""
    from tests.support import run_alembic

    run_alembic(fresh_engine, "upgrade", "head")
    return fresh_engine


# ------------------------------------------------------------------ T04 lifecycle fixtures --------
@pytest.fixture
def key_store(tmp_path: Path):  # noqa: ANN201
    from app.services.crypto import KeyStore

    return KeyStore(tmp_path / "keys" / "platform", tmp_path / "keys" / "synthetic")


@pytest.fixture
def issuable(migrated_engine: Engine, key_store) -> Engine:  # noqa: ANN001
    """Migrated DB with a signing-capable issuer ISS-001, learners LRN-0000001 (completed CRS-00001
    on 2026-03-01, score 82.5) and LRN-0000002 (no completion), active rule RUL-00001-V1 (min 70)."""
    from tests.support import seed_issuable

    seed_issuable(migrated_engine, key_store)
    return migrated_engine


@pytest.fixture
def factories(migrated_engine: Engine):  # noqa: ANN201
    from app.api.deps import SessionFactories
    from tests.support import read_sessions, write_sessions

    return SessionFactories(read=read_sessions(migrated_engine), write=write_sessions(migrated_engine))


@pytest.fixture
def api(issuable: Engine, key_store, factories, monkeypatch) -> Iterator[TestClient]:  # noqa: ANN001
    """The real application with its session factories, key store and clock pointed at the test DB."""
    from app.api.deps import get_clock, get_key_store, get_session_factories
    from app.services import audit
    from tests.support import ts

    monkeypatch.setattr(audit, "WriteSessionLocal", factories.write)  # default access.denied recorder
    app.dependency_overrides[get_session_factories] = lambda: factories
    app.dependency_overrides[get_key_store] = lambda: key_store
    app.dependency_overrides[get_clock] = lambda: (lambda: ts("2026-11-14 12:00:00"))
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
