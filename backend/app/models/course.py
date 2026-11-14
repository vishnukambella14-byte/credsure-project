"""``courses`` (data_dictionary §3.3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import CourseStatus, RecordSource, sql_in


class Course(Base):
    __tablename__ = "courses"

    course_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    course_code: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    credit_hours: Mapped[int | None] = mapped_column(Integer)
    issuer_id: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("issuers.issuer_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        index=True,
    )
    lead_instructor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), server_default=CourseStatus.ACTIVE.value)
    record_source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("course_id GLOB 'CRS-[0-9][0-9][0-9][0-9][0-9]'", name="course_id_format"),
        CheckConstraint("credit_hours IS NULL OR credit_hours >= 0", name="credit_hours"),
        CheckConstraint(sql_in("status", CourseStatus), name="status"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
