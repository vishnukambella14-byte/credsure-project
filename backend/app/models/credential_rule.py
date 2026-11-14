"""``credential_rules`` (data_dictionary §3.4). Versioned; immutable once activated (D1, D5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import CredentialType, RecordSource, RuleStatus, sql_in


class CredentialRule(Base):
    __tablename__ = "credential_rules"

    rule_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    course_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("courses.course_id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    credential_type: Mapped[str] = mapped_column(String(20))
    min_score: Mapped[float | None] = mapped_column(Float)
    min_attendance_pct: Mapped[float | None] = mapped_column(Float)
    validity_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    effective_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    record_source: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint("course_id", "version"),  # uq_credential_rules_course_id
        UniqueConstraint("rule_id", "course_id"),  # uq_credential_rules_rule_id: composite FK target
        Index(
            "uq_credential_rules_active_course",
            "course_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "rule_id = 'RUL-' || substr(course_id, 5) || '-V' || version", name="rule_id_derived"
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(sql_in("credential_type", CredentialType), name="credential_type"),
        CheckConstraint(
            "min_score IS NULL OR (min_score >= 0 AND min_score <= 100)", name="min_score_range"
        ),
        CheckConstraint(
            "min_attendance_pct IS NULL OR (min_attendance_pct >= 0 AND min_attendance_pct <= 100)",
            name="min_attendance_pct_range",
        ),
        CheckConstraint("validity_days IS NULL OR validity_days > 0", name="validity_days"),
        CheckConstraint(sql_in("status", RuleStatus), name="status"),
        CheckConstraint("(status = 'draft') = (effective_from IS NULL)", name="draft_effective_from"),
        CheckConstraint("status <> 'retired' OR effective_to IS NOT NULL", name="retired_effective_to"),
        CheckConstraint("status <> 'active' OR effective_to IS NULL", name="active_open"),
        CheckConstraint(
            "status = 'draft' OR min_score IS NOT NULL OR min_attendance_pct IS NOT NULL",
            name="has_criteria",
        ),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="window"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
