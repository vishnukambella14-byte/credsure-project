"""``issuers`` (data_dictionary §3.1). Immutable except ``active -> retired`` (trigger, D1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime
from app.models.enums import IssuerStatus, RecordSource, sql_in


class Issuer(Base):
    __tablename__ = "issuers"

    issuer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str | None] = mapped_column(String(2))
    website_url: Mapped[str | None] = mapped_column(String(500))
    signing_key_id: Mapped[str] = mapped_column(String(64), unique=True)
    public_key: Mapped[str] = mapped_column(String(100), unique=True)
    key_algorithm: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default=IssuerStatus.ACTIVE.value)
    supersedes_issuer_id: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("issuers.issuer_id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    record_source: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint("issuer_id", "signing_key_id"),  # uq_issuers_issuer_id: composite FK target
        UniqueConstraint("supersedes_issuer_id"),  # uq_issuers_supersedes_issuer_id
        CheckConstraint("issuer_id GLOB 'ISS-[0-9][0-9][0-9]'", name="issuer_id_format"),
        CheckConstraint(
            "(record_source = 'synthetic_import') = (issuer_id GLOB 'ISS-9[0-9][0-9]')",
            name="synthetic_range",
        ),
        CheckConstraint("signing_key_id GLOB issuer_id || '-K[0-9]*'", name="signing_key_id_format"),
        CheckConstraint(
            "supersedes_issuer_id IS NULL OR supersedes_issuer_id <> issuer_id",
            name="no_self_supersede",
        ),
        CheckConstraint(sql_in("status", IssuerStatus), name="status"),
        CheckConstraint("(status = 'retired') = (valid_to IS NOT NULL)", name="retired_valid_to"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_window"),
        CheckConstraint("key_algorithm = 'Ed25519'", name="key_algorithm"),
        CheckConstraint(sql_in("record_source", RecordSource), name="record_source"),
    )
