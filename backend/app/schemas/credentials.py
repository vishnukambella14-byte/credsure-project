"""Credential, issuance and revocation schemas (api_contract §4.2, §4.4)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiModel, UtcDateTime
from app.services.ids import is_attempt_ref


class IssueCredentialRequest(ApiModel):
    learner_id: str = Field(min_length=1, max_length=20)
    course_id: str = Field(min_length=1, max_length=20)
    # Optional: normally system-generated; supplied by the dataset replay and duplicate-ID tests.
    credential_id: str | None = Field(default=None, min_length=1, max_length=30)
    attempt_ref: str | None = None

    @field_validator("attempt_ref")
    @classmethod
    def _attempt_ref_format(cls, value: str | None) -> str | None:
        if value is not None and not is_attempt_ref(value):
            raise ValueError("attempt_ref must be ATT- + 12 Crockford characters or ATT-nnnnnn")
        return value


class CredentialOut(BaseModel):
    credential_id: str
    learner_id: str
    course_id: str
    completion_id: str
    rule_id: str
    issuer_id: str
    credential_type: str
    status: str
    issued_at: UtcDateTime
    expires_at: UtcDateTime | None
    revoked_at: UtcDateTime | None
    payload_hash: str
    signing_key_id: str


class IssuedCredentialOut(BaseModel):
    """201 body of ``POST /api/v1/credentials`` (api_contract §4.2)."""

    credential_id: str
    learner_id: str
    course_id: str
    completion_id: str
    rule_id: str
    issuer_id: str
    credential_type: str
    status: str
    issued_at: UtcDateTime
    expires_at: UtcDateTime | None
    payload: dict[str, Any]
    payload_hash: str
    signature: str
    signing_key_id: str
    attempt_ref: str


class RevocationOut(BaseModel):
    revocation_id: str
    credential_id: str
    reason_code: str
    reason_text: str | None
    requested_by: str | None
    approved_by: str
    revoked_at: UtcDateTime


class CredentialDetailOut(CredentialOut):
    payload: dict[str, Any]
    signature: str
    schema_version: str
    issued_by: str
    revocation: RevocationOut | None


class ShareBundleOut(BaseModel):
    payload: dict[str, Any]
    payload_hash: str
    signature: str
    issuer_id: str
    signing_key_id: str


class RevokeRequest(ApiModel):
    # Typed as str (not an enum) so an unknown code is a documented, audited
    # revocation_reason_required rejection rather than an unaudited validation_error (SR-24).
    reason_code: str | None = None
    reason_text: str | None = None
    requested_by: str | None = Field(default=None, max_length=64)


class RevokeResponse(BaseModel):
    credential: CredentialOut
    revocation: RevocationOut
