from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import DEFAULT_DATABASE_PATH
from app.database.session import DatabaseStatus, engine, get_database_status


def test_tests_use_isolated_database(isolated_db_path: Path) -> None:
    assert Path(engine.url.database).resolve() == isolated_db_path.resolve()
    assert Path(engine.url.database).resolve() != DEFAULT_DATABASE_PATH.resolve()


def test_database_file_is_created_on_connect(isolated_db_path: Path) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    assert isolated_db_path.exists()


def test_foreign_keys_enabled_on_every_connection() -> None:
    for _ in range(3):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_foreign_key_violation_is_rejected() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE fk_child (id INTEGER PRIMARY KEY, "
                "parent_id INTEGER NOT NULL REFERENCES fk_parent(id))"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO fk_child (id, parent_id) VALUES (1, 999)"))
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE fk_child"))
            conn.execute(text("DROP TABLE fk_parent"))


def test_status_is_not_initialized_without_migrations() -> None:
    assert get_database_status() == DatabaseStatus.NOT_INITIALIZED
