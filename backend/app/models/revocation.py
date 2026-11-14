"""``revocations`` (data_dictionary §3.7). Immutable; one per credential; atomic with the
credential's ``active -> revoked`` transition (D1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import RecordSource, RevocationReason, sql_in


class Revocation(Base):
    __tablename__ = "revocations"

    revocation_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.credential_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        unique=True,
    )
    reason_code: Mapped[str] = mapped_column(String(30))
    reason_text: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    record_source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint(sql_in("reason_code", RevocationReason), name="reason_code"),
        CheckConstraint("reason_code <> 'other' OR reason_text IS NOT NULL", name="reason_text"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
