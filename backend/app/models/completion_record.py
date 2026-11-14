"""``completion_records`` (data_dictionary §3.5). Locked while an active credential uses it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import CompletionStatus, RecordSource, sql_in


class CompletionRecord(Base):
    __tablename__ = "completion_records"

    completion_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    learner_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("learners.learner_id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    course_id: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("courses.course_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Float)
    attendance_pct: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    assessed_by: Mapped[str | None] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    record_source: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint(
            "learner_id", "course_id", "attempt_number", name="uq_completion_records_attempt"
        ),
        # uq_completion_records_completion_id: composite FK target for credentials
        UniqueConstraint("completion_id", "learner_id", "course_id"),
        Index("ix_completion_records_learner_id_course_id", "learner_id", "course_id"),
        CheckConstraint("attempt_number >= 1", name="attempt_number"),
        CheckConstraint(sql_in("status", CompletionStatus), name="status"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
        CheckConstraint(
            "attendance_pct IS NULL OR (attendance_pct >= 0 AND attendance_pct <= 100)",
            name="attendance_pct_range",
        ),
        CheckConstraint("status <> 'completed' OR completed_at IS NOT NULL", name="completed_at"),
        CheckConstraint("completed_at IS NULL OR completed_at <= recorded_at", name="recorded_after"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
