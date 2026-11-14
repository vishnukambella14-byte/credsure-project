"""Load a generated dataset into the existing database (FR-25, ADR-013, ADR-021, ADR-023, ADR-024).

* Files load in FK and D5 order (rules before the completions they govern), each in its own
  ``BEGIN IMMEDIATE`` transaction with one ``dataset.imported`` audit event (AS-09). Rows are plain
  INSERTs with ``record_source = 'synthetic_import'``: service rules (eligibility) are bypassed,
  database constraints and triggers are not. Any IntegrityError aborts that file's transaction.
* Only synthetic-range issuers (``ISS-9nn``) are accepted (D8).
* ``issuance_attempts.csv`` is replayed through the real ``issue_credential`` with the dataset's
  ``attempt_id`` as ``attempt_ref``, an injected clock set to ``attempted_at`` and the profile's
  synthetic ``KeyStore`` (D7). Valid attempts issue credentials; A2 duplicates are rejected and
  audited by the platform itself.
* ``--reset`` recreates the schema (``alembic downgrade base`` + ``upgrade head``); the append-only
  triggers make row deletion impossible by design.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Engine, Float, Integer, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError, map_integrity_error
from app.core.security import Actor, Role
from app.core.timeutil import parse_utc
from app.database.types import UTCDateTime
from app.datagen import config as cfg
from app.datagen.generator import TABLE_MODELS
from app.models.enums import RecordSource
from app.services import audit
from app.services.crypto import KeyStore
from app.services.issuance import issue_credential

SessionFactory = Callable[[], Session]
SYNTHETIC = RecordSource.SYNTHETIC_IMPORT.value
_SYNTHETIC_ISSUER = re.compile(r"^ISS-9[0-9]{2}$")


class LoadError(RuntimeError):
    pass


@dataclass
class LoadReport:
    profile: str
    rows: dict[str, int] = field(default_factory=dict)
    replay_issued: list[str] = field(default_factory=list)  # attempt_ids that issued a credential
    replay_rejected: dict[str, str] = field(default_factory=dict)  # attempt_id -> error code

    def summary(self) -> dict[str, Any]:
        return {"profile": self.profile, "rows": self.rows, "replay_issued": len(self.replay_issued),
                "replay_rejected": dict(Counter(self.replay_rejected.values()))}


def _converter(column: Any) -> Callable[[str], Any]:
    if isinstance(column.type, UTCDateTime):
        return parse_utc
    if isinstance(column.type, Boolean):
        return lambda value: value == "1"
    if isinstance(column.type, Integer):
        return int
    if isinstance(column.type, Float):
        return float
    return str


def _defaults(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Columns not in the CSVs (data_dictionary §5.1): business timestamps and the synthetic source."""
    extra: dict[str, Any] = {"record_source": SYNTHETIC}
    if table == "issuers":
        extra["created_at"] = row["valid_from"]
    elif table in ("learners", "courses"):
        extra["created_at"] = extra["updated_at"] = cfg.DATASET_START
    elif table == "credential_rules":
        extra["created_at"] = row["effective_from"] or cfg.DATASET_START
    elif table == "completion_records":
        extra["updated_at"] = row["recorded_at"]
    elif table == "credentials":
        extra["created_at"] = row["issued_at"]
    elif table == "revocations":
        extra["created_at"] = row["revoked_at"]
    return extra


def read_rows(path: Path, model: type | None = None) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        raw = list(csv.DictReader(handle))
    if model is None:
        return raw
    columns = {c.name: c for c in inspect(model).columns}
    converters = {name: _converter(columns[name]) for name in (raw[0].keys() if raw else [])}
    return [{name: (None if value == "" else converters[name](value)) for name, value in row.items()} for row in raw]


def _require_migrated(read_factory: SessionFactory) -> None:
    with read_factory() as session:
        bind = session.get_bind()
        if not inspect(bind).has_table("alembic_version"):
            raise LoadError("database is not migrated: run 'alembic upgrade head' first")


def reset_schema(engine: Engine) -> None:
    """Recreate the schema through the real migration (downgrade base, upgrade head)."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(cfg.BACKEND_DIR / "alembic.ini"))
    config.attributes["configure_logger"] = False
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
        command.upgrade(config, "head")


def load_dataset(
    profile: str,
    *,
    write_factory: SessionFactory,
    read_factory: SessionFactory,
    key_store: KeyStore,
    generated_root: Path = cfg.GENERATED_ROOT,
) -> LoadReport:
    if profile not in cfg.PROFILES:
        raise LoadError(f"unknown profile {profile!r}")
    if key_store.synthetic_profile != profile:
        raise LoadError("the key store must point at this profile's synthetic keys (D8)")
    directory = generated_root / profile
    if not (directory / cfg.MANIFEST_FILE).is_file():
        raise LoadError(f"no generated dataset at {directory}: run 'python -m app.datagen generate' first")
    _require_migrated(read_factory)
    report = LoadReport(profile=profile)

    for file_name, table in cfg.TABLE_FILES:
        model = TABLE_MODELS[file_name]
        rows = read_rows(directory / file_name, model)
        if table == "issuers":
            bad = [r["issuer_id"] for r in rows if not _SYNTHETIC_ISSUER.match(r["issuer_id"])]
            if bad:
                raise LoadError(f"issuers.csv contains non-synthetic issuer IDs (D8): {bad}")
        records = [{**row, **_defaults(table, row)} for row in rows]
        with write_factory() as session:
            try:
                if records:
                    session.execute(model.__table__.insert(), records)  # plain INSERT (executemany)
                audit.record_event(
                    session, event_type="dataset.imported", entity_type="dataset",
                    entity_id=f"{profile}/{file_name}", actor_id=audit.SYSTEM_ACTOR_ID,
                    actor_role=audit.SYSTEM_ACTOR_ROLE, details={"profile": profile, "file": file_name,
                                                               "rows": len(records)},
                    occurred_at=cfg.REFERENCE,
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                error = map_integrity_error(exc)
                raise LoadError(f"{file_name}: rejected by the database ({error.code}); "
                                "use --reset to load into a fresh schema") from exc
        report.rows[file_name] = len(records)

    attempts = read_rows(directory / cfg.ATTEMPTS_FILE)
    report.rows[cfg.ATTEMPTS_FILE] = len(attempts)
    for attempt in sorted(attempts, key=lambda a: a["attempt_id"]):
        attempted_at = parse_utc(attempt["attempted_at"])
        try:
            issue_credential(
                actor=Actor(attempt["actor_id"], Role.ACADEMIC_ADMIN), learner_id=attempt["learner_id"],
                course_id=attempt["course_id"], credential_id=attempt["credential_id"],
                attempt_ref=attempt["attempt_id"], write_factory=write_factory, read_factory=read_factory,
                key_store=key_store, clock=lambda moment=attempted_at: moment, record_source=SYNTHETIC,
            )
            report.replay_issued.append(attempt["attempt_id"])
        except DomainError as error:
            report.replay_rejected[attempt["attempt_id"]] = error.code
    return report
