"""``verification_requests`` (data_dictionary §3.8). Immutable; the stored ``result`` must be the
BR-13 precedence function of the stored flags (D1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import (
    RecordSource,
    VerificationChannel,
    VerificationPurpose,
    VerificationResult,
    sql_in,
)

RESULT_PRECEDENCE_SQL = (
    "result = CASE WHEN credential_id IS NULL THEN 'not_found' "
    "WHEN hash_match = 0 THEN 'tampered' "
    "WHEN signature_valid = 0 THEN 'invalid_signature' "
    "WHEN is_revoked = 1 THEN 'revoked' "
    "WHEN is_expired = 1 THEN 'expired' "
    "ELSE 'valid' END"
)


class VerificationRequest(Base):
    __tablename__ = "verification_requests"

    verification_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    submitted_credential_id: Mapped[str] = mapped_column(String(64), index=True)
    credential_id: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("credentials.credential_id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    verifier_name: Mapped[str] = mapped_column(String(200))
    verifier_organization: Mapped[str | None] = mapped_column(String(200))
    verifier_email: Mapped[str | None] = mapped_column(String(254))
    purpose: Mapped[str | None] = mapped_column(String(30))
    channel: Mapped[str] = mapped_column(String(20))
    presented_payload_hash: Mapped[str | None] = mapped_column(String(64))
    hash_match: Mapped[bool | None] = mapped_column(Boolean)
    signature_valid: Mapped[bool | None] = mapped_column(Boolean)
    is_revoked: Mapped[bool | None] = mapped_column(Boolean)
    is_expired: Mapped[bool | None] = mapped_column(Boolean)
    result: Mapped[str] = mapped_column(String(20), index=True)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    record_source: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        Index("ix_verification_requests_credential_id_requested_at", "credential_id", "requested_at"),
        CheckConstraint(
            "purpose IS NULL OR " + sql_in("purpose", VerificationPurpose), name="purpose"
        ),
        CheckConstraint(sql_in("channel", VerificationChannel), name="channel"),
        CheckConstraint(sql_in("result", VerificationResult), name="result"),
        CheckConstraint(
            "credential_id IS NULL OR (hash_match IS NOT NULL AND signature_valid IS NOT NULL "
            "AND is_revoked IS NOT NULL AND is_expired IS NOT NULL)",
            name="flags_present",
        ),
        CheckConstraint(RESULT_PRECEDENCE_SQL, name="result_precedence"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
