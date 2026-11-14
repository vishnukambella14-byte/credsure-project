"""Migration 0001: clean upgrade, inventory, downgrade, re-upgrade, model/migration agreement."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine

from app.database import triggers
from app.database.session import DatabaseStatus, get_database_status
from app.models import Base
from tests.support import BACKEND_DIR, run_alembic, sqlite_objects

EXPECTED_TABLES = {
    "issuers", "learners", "courses", "credential_rules", "completion_records",
    "credentials", "revocations", "verification_requests", "audit_events",
}
DOCS_DATA_DICTIONARY = BACKEND_DIR.parent / "docs" / "data_dictionary.md"


def _model_index_names() -> set[str]:
    return {index.name for table in Base.metadata.tables.values() for index in table.indexes}


def test_metadata_defines_exactly_the_nine_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_check_constraint_is_explicitly_named(migrated_engine: Engine) -> None:
    for table in Base.metadata.tables.values():
        checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        assert checks, f"{table.name} has no CHECK constraints"
        assert all(c.name for c in checks), f"unnamed CHECK on {table.name}"

    with migrated_engine.connect() as conn:
        for name, ddl in conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name <> 'alembic_version' "
            "AND name NOT LIKE 'sqlite_%'"
        ):
            check_names = re.findall(r"CONSTRAINT (\w+) CHECK", ddl)
            assert check_names, name
            assert all(n.startswith(f"ck_{name}_") for n in check_names), (name, check_names)
            assert ddl.count(" CHECK ") == len(check_names), f"unnamed CHECK in {name}"


def test_clean_upgrade_creates_tables_indexes_and_triggers(migrated_engine: Engine) -> None:
    assert sqlite_objects(migrated_engine, "table") == EXPECTED_TABLES | {"alembic_version"}

    found_triggers = sqlite_objects(migrated_engine, "trigger")
    assert found_triggers == set(triggers.CURRENT_TRIGGERS)
    assert len(found_triggers) == 16

    found_indexes = sqlite_objects(migrated_engine, "index")
    expected_indexes = _model_index_names() | set(triggers.CURRENT_INDEXES)
    assert found_indexes == expected_indexes
    assert {"uq_audit_events_single_genesis", "uq_audit_events_attempt_outcome"} <= found_indexes
    assert {"uq_credentials_active_learner_course", "uq_credential_rules_active_course"} <= found_indexes

    with migrated_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0001"


def test_audit_events_uses_autoincrement(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        ddl = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_events'"
        ).scalar()
    assert "AUTOINCREMENT" in ddl.upper()


def test_downgrade_removes_everything_and_reupgrade_succeeds(migrated_engine: Engine) -> None:
    run_alembic(migrated_engine, "downgrade", "base")
    assert sqlite_objects(migrated_engine, "table") == {"alembic_version"}
    assert sqlite_objects(migrated_engine, "trigger") == set()
    assert sqlite_objects(migrated_engine, "index") == set()

    run_alembic(migrated_engine, "upgrade", "head")
    assert sqlite_objects(migrated_engine, "table") == EXPECTED_TABLES | {"alembic_version"}
    assert sqlite_objects(migrated_engine, "trigger") == set(triggers.CURRENT_TRIGGERS)


def test_models_and_migration_agree(migrated_engine: Engine) -> None:
    """Autogenerate finds nothing to change: Alembic discovers all models and 0001 matches them."""

    def include(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN202
        return not (type_ == "index" and name in triggers.CURRENT_INDEXES)

    with migrated_engine.connect() as conn, warnings.catch_warnings():
        # SQLAlchemy cannot reflect the expression index (it is managed by raw SQL; excluded above).
        warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index")
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_object": include}
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == []


def test_health_reports_initialized_on_migrated_database(migrated_engine: Engine) -> None:
    assert get_database_status(migrated_engine) == DatabaseStatus.INITIALIZED


def test_trigger_sql_matches_data_dictionary() -> None:
    """``app.database.triggers`` is generated from data_dictionary §6 and must stay identical."""
    markdown = DOCS_DATA_DICTIONARY.read_text(encoding="utf-8")
    block = re.search(r"## 6\. Reference DDL.*?```sql\n(.*?)```", markdown, re.S).group(1)
    documented, buffer = [], []
    for line in block.splitlines():
        code = "" if line.lstrip().startswith("--") else re.sub(r"\s+--.*$", "", line)
        if code.strip():
            buffer.append(code.rstrip())
            if sqlite3.complete_statement("\n".join(buffer)):
                documented.append("\n".join(buffer).rstrip().rstrip(";"))
                buffer = []

    def normalise(sql: str) -> str:
        return " ".join(sql.split())

    assert [normalise(s) for s in triggers.CURRENT_CREATE] == [normalise(s) for s in documented]


def test_reapply_for_table_restores_triggers_after_rebuild(migrated_engine: Engine) -> None:
    """Simulates a batch rebuild (drop of the table's triggers) and the documented recovery."""
    with migrated_engine.begin() as conn:
        for name in triggers.CURRENT_TRIGGERS:
            if name.startswith("trg_credentials_"):
                conn.exec_driver_sql(f"DROP TRIGGER {name}")
    assert not {t for t in sqlite_objects(migrated_engine, "trigger") if t.startswith("trg_credentials_")}

    with migrated_engine.begin() as conn:
        triggers.reapply_for_table(conn.exec_driver_sql, "credentials")
    assert sqlite_objects(migrated_engine, "trigger") == set(triggers.CURRENT_TRIGGERS)


def test_cli_migrates_configured_database(tmp_path: Path) -> None:
    """``alembic upgrade head`` / ``downgrade base`` via the CLI against CREDSURE_DATABASE_URL."""
    db_file = tmp_path / "configured.db"
    env = {**os.environ, "CREDSURE_DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}

    def alembic(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr

    alembic("upgrade", "head")
    with sqlite3.connect(db_file) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert names == set(triggers.CURRENT_TRIGGERS)
    assert version == ("0001",)

    alembic("downgrade", "base")
    with sqlite3.connect(db_file) as conn:
        remaining = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger','index') "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert remaining == {"alembic_version"}
