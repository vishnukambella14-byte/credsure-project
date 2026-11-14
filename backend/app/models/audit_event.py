"""``audit_events`` (data_dictionary §3.9). Append-only, single linear hash chain (D4).

The two non-model indexes ``uq_audit_events_single_genesis`` (expression index) and
``uq_audit_events_attempt_outcome`` plus the chain triggers are created by the migration from
``app.database.triggers`` (data_dictionary §6).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import CROCKFORD_CLASS, TERMINAL_ISSUANCE_EVENTS, AuditOutcome, sql_in


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    event_type: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(50))
    attempt_ref: Mapped[str | None] = mapped_column(String(30))
    details_json: Mapped[str] = mapped_column(Text, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)

    __table_args__ = (
        UniqueConstraint("previous_hash"),  # uq_audit_events_previous_hash: no forks (D4)
        Index("ix_audit_events_event_type_reason_code", "event_type", "reason_code"),
        Index("ix_audit_events_entity_type_entity_id", "entity_type", "entity_id"),
        CheckConstraint(sql_in("outcome", AuditOutcome), name="outcome"),
        CheckConstraint(
            "event_type NOT IN ("
            + ",".join(repr(e) for e in TERMINAL_ISSUANCE_EVENTS)
            + ") OR attempt_ref IS NOT NULL",
            name="attempt_ref_required",
        ),
        CheckConstraint(
            f"attempt_ref IS NULL OR attempt_ref GLOB 'ATT-{CROCKFORD_CLASS}*'",
            name="attempt_ref_format",
        ),
        {"sqlite_autoincrement": True},
    )
