"""Shared helpers for schema, trigger and migration tests (not a conftest, so it can be imported)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    CompletionRecord,
    Course,
    Credential,
    CredentialRule,
    Issuer,
    Learner,
    Revocation,
    VerificationRequest,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
UTC = timezone.utc


def ts(text_value: str) -> datetime:
    """Aware UTC datetime from 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.fromisoformat(text_value).replace(tzinfo=UTC)


T0 = ts("2026-01-01 00:00:00")
CRED_ID = "CRED-7K2M9QXR4T1B"
CRED_ID_2 = "CRED-0000000000AB"
HASH = "a" * 64
SIG = "S" * 88


# ---------------------------------------------------------------- Alembic --------------------
def alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logger"] = False
    return cfg


def run_alembic(engine: Engine, action: str, target: str) -> None:
    """Run ``alembic upgrade|downgrade <target>`` against ``engine`` (not the configured DB)."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, target)


def sqlite_objects(engine: Engine, kind: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = :kind AND name NOT LIKE 'sqlite_%'"),
            {"kind": kind},
        )
        return {row[0] for row in rows}


# ---------------------------------------------------------------- row factories --------------
def issuer_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        issuer_id="ISS-001", name="CredSure Institute", legal_name="CredSure Institute Ltd",
        signing_key_id="ISS-001-K1", public_key="PK-ISS-001", key_algorithm="Ed25519",
        status="active", valid_from=T0, record_source="platform", created_by="PA-1", created_at=T0,
    )
    row.update(overrides)
    return row


def learner_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        learner_id="LRN-0000001", given_name="Asha", family_name="Rao", email="asha@example.org",
        status="active", record_source="platform", created_at=T0, updated_at=T0,
    )
    row.update(overrides)
    return row


def course_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        course_id="CRS-00001", course_code="DATA-101", title="Data Foundations", issuer_id="ISS-001",
        status="active", record_source="platform", created_at=T0, updated_at=T0,
    )
    row.update(overrides)
    return row


def rule_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        rule_id="RUL-00001-V1", course_id="CRS-00001", version=1, credential_type="certificate",
        min_score=70.0, status="active", effective_from=T0, created_by="AA-1", created_at=T0,
        activated_at=T0, record_source="platform",
    )
    row.update(overrides)
    return row


def completion_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        completion_id="CMP-0000001", learner_id="LRN-0000001", course_id="CRS-00001",
        attempt_number=1, status="completed", score=82.5, attendance_pct=95.0,
        completed_at=ts("2026-03-01 10:00:00"), recorded_at=ts("2026-03-01 10:00:00"),
        updated_at=ts("2026-03-01 10:00:00"), record_source="platform",
    )
    row.update(overrides)
    return row


def credential_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        credential_id=CRED_ID, learner_id="LRN-0000001", course_id="CRS-00001",
        completion_id="CMP-0000001", rule_id="RUL-00001-V1", issuer_id="ISS-001",
        credential_type="certificate", status="active", issued_at=ts("2026-03-02 09:00:00"),
        payload_json="{}", payload_hash=HASH, signature=SIG, signing_key_id="ISS-001-K1",
        schema_version="1.0", issued_by="AA-1", record_source="platform",
        created_at=ts("2026-03-02 09:00:00"),
    )
    row.update(overrides)
    return row


def revocation_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        revocation_id="REV-000001", credential_id=CRED_ID, reason_code="issued_in_error",
        approved_by="AA-1", revoked_at=ts("2026-05-01 00:00:00"), record_source="platform",
        created_at=ts("2026-05-01 00:00:00"),
    )
    row.update(overrides)
    return row


def verification_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        verification_id="VER-0000001", submitted_credential_id=CRED_ID, credential_id=CRED_ID,
        verifier_name="Jordan Lee", channel="api", hash_match=True, signature_valid=True,
        is_revoked=False, is_expired=False, result="valid", requested_at=ts("2026-04-01 12:00:00"),
        record_source="platform",
    )
    row.update(overrides)
    return row


def audit_row(n: int, previous_hash: str | None, **overrides: Any) -> dict[str, Any]:
    row = dict(
        event_uuid=f"00000000-0000-0000-0000-{n:012d}", event_type="dataset.imported",
        entity_type="dataset", entity_id="starter", actor_id="system", actor_role="system",
        outcome="success", occurred_at=T0, previous_hash=previous_hash, event_hash=f"{n:064x}",
    )
    row.update(overrides)
    return row


MODELS = {
    "issuer": Issuer, "learner": Learner, "course": Course, "rule": CredentialRule,
    "completion": CompletionRecord, "credential": Credential, "revocation": Revocation,
    "verification": VerificationRequest, "audit": AuditEvent,
}


def insert(engine: Engine, model: type, row: dict[str, Any]) -> None:
    """Plain INSERT (never OR REPLACE) in its own transaction."""
    with engine.begin() as conn:
        conn.execute(model.__table__.insert().values(**row))


def execute(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def scalar(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def write_sessions(engine: Engine):  # noqa: ANN201 - sessionmaker
    """Session factory with the application's write configuration (BEGIN IMMEDIATE)."""
    from sqlalchemy.orm import sessionmaker

    from app.database.session import write_engine

    return sessionmaker(bind=write_engine(engine), autoflush=False, expire_on_commit=False)


def read_sessions(engine: Engine):  # noqa: ANN201 - sessionmaker
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def seed_core(engine: Engine) -> None:
    """Issuer, two learners, one course, active rule v1 (from 2026-01-01), one completed record."""
    insert(engine, Issuer, issuer_row())
    insert(engine, Learner, learner_row())
    insert(engine, Learner, learner_row(learner_id="LRN-0000002", email="ben@example.org"))
    insert(engine, Course, course_row())
    insert(engine, CredentialRule, rule_row())
    insert(engine, CompletionRecord, completion_row())


def seed_issuable(engine: Engine, key_store: Any) -> Any:
    """Like ``seed_core`` but the issuer has a real Ed25519 key: public key in the database,
    private key in ``key_store``'s platform directory. Returns the private key."""
    from app.services.crypto import generate_private_key, public_key_b64

    private_key = generate_private_key()
    key_store.store_new_platform_key("ISS-001-K1", private_key)
    insert(engine, Issuer, issuer_row(public_key=public_key_b64(private_key)))
    insert(engine, Learner, learner_row())
    insert(engine, Learner, learner_row(learner_id="LRN-0000002", given_name="Ben", family_name="Okafor",
                                        email="ben@example.org"))
    insert(engine, Course, course_row())
    insert(engine, CredentialRule, rule_row())
    insert(engine, CompletionRecord, completion_row())
    return private_key


def seed_with_credential(engine: Engine) -> None:
    seed_core(engine)
    insert(engine, Credential, credential_row())


def revoke(engine: Engine, revoked_at: datetime = ts("2026-05-01 00:00:00")) -> None:
    """Atomic revocation (D1): revocation row, then active -> revoked, in one transaction."""
    with engine.begin() as conn:
        conn.execute(Revocation.__table__.insert().values(**revocation_row(revoked_at=revoked_at)))
        conn.execute(
            Credential.__table__.update()
            .where(Credential.__table__.c.credential_id == CRED_ID)
            .values(status="revoked", revoked_at=revoked_at)
        )


__all__ = ["IntegrityError"]
