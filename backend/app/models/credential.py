"""``credentials`` (data_dictionary §3.6).

Only ``status``/``revoked_at`` may change, and only ``active -> revoked`` backed by a matching
revocation row (triggers, D1). Composite FKs tie the evidence to the same learner/course and the
signing key to the same issuer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import CROCKFORD_CLASS, CredentialStatus, CredentialType, RecordSource, sql_in

_FK = {"ondelete": "RESTRICT", "onupdate": "RESTRICT"}


class Credential(Base):
    __tablename__ = "credentials"

    credential_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    learner_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("learners.learner_id", **_FK), index=True
    )
    course_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("courses.course_id", **_FK), index=True
    )
    completion_id: Mapped[str] = mapped_column(String(20), index=True)
    rule_id: Mapped[str] = mapped_column(String(30), index=True)
    issuer_id: Mapped[str] = mapped_column(String(20), index=True)
    credential_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), server_default=CredentialStatus.ACTIVE.value, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(String(100))
    signing_key_id: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(10), server_default="1.0")
    issued_by: Mapped[str] = mapped_column(String(64))
    record_source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        # Composite FKs replace single-column FKs on completion_id, rule_id, issuer_id/signing_key_id.
        ForeignKeyConstraint(
            ["completion_id", "learner_id", "course_id"],
            [
                "completion_records.completion_id",
                "completion_records.learner_id",
                "completion_records.course_id",
            ],
            **_FK,
        ),
        ForeignKeyConstraint(
            ["rule_id", "course_id"],
            ["credential_rules.rule_id", "credential_rules.course_id"],
            **_FK,
        ),
        ForeignKeyConstraint(
            ["issuer_id", "signing_key_id"],
            ["issuers.issuer_id", "issuers.signing_key_id"],
            **_FK,
        ),
        Index(
            "uq_credentials_active_learner_course",
            "learner_id",
            "course_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "credential_id GLOB 'CRED-" + CROCKFORD_CLASS * 12 + "'", name="credential_id_format"
        ),
        CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'",
            name="payload_hash_format",
        ),
        CheckConstraint("length(signature) = 88", name="signature_length"),
        CheckConstraint(sql_in("credential_type", CredentialType), name="credential_type"),
        CheckConstraint(sql_in("status", CredentialStatus), name="status"),
        CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name="revoked_at"),
        CheckConstraint("expires_at IS NULL OR expires_at > issued_at", name="expiry"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
