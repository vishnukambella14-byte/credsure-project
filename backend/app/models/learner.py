"""``learners`` (data_dictionary §3.2). Identity is ``learner_id``, never the name (BR-01)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import LearnerStatus, RecordSource, sql_in


class Learner(Base):
    __tablename__ = "learners"

    learner_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    external_ref: Mapped[str | None] = mapped_column(String(50), unique=True)
    given_name: Mapped[str] = mapped_column(String(100))
    family_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    status: Mapped[str] = mapped_column(String(20), server_default=LearnerStatus.ACTIVE.value)
    record_source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_learners_family_name_given_name", "family_name", "given_name"),
        CheckConstraint(
            "learner_id GLOB 'LRN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'", name="learner_id_format"
        ),
        CheckConstraint("email = lower(email)", name="email_lower"),
        CheckConstraint(sql_in("status", LearnerStatus), name="status"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
