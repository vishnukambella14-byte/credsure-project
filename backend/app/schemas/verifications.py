"""Verification schemas (api_contract §4.3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel, UtcDateTime


class VerifierIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)


class VerifyRequest(ApiModel):
    credential_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] | None = None
    signature: str | None = None
    verifier: VerifierIn
    purpose: Literal["employment", "admission", "licensing", "other"] | None = None


class VerificationChecks(BaseModel):
    found: bool
    hash_match: bool | None
    signature_valid: bool | None
    is_revoked: bool | None
    is_expired: bool | None


class CredentialSummary(BaseModel):
    """Echoed only for valid / revoked / expired results (never for tampered data)."""

    credential_type: str
    course_title: str
    learner_name: str
    issuer_name: str
    issued_at: str
    expires_at: str | None


class VerifyResponse(BaseModel):
    verification_id: str
    credential_id: str | None
    result: str
    checks: VerificationChecks
    message: str
    credential: CredentialSummary | None
    requested_at: UtcDateTime


class VerificationRecordOut(BaseModel):
    verification_id: str
    submitted_credential_id: str
    credential_id: str | None
    verifier_name: str
    verifier_organization: str | None
    purpose: str | None
    channel: str
    presented_payload_hash: str | None
    hash_match: bool | None
    signature_valid: bool | None
    is_revoked: bool | None
    is_expired: bool | None
    result: str
    requested_at: UtcDateTime
    responded_at: UtcDateTime | None
