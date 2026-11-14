"""Third-party verification (FR-14, FR-15, BR-11–BR-15, exception_catalogue §2).

Result precedence (BR-13): ``not_found`` -> ``tampered`` -> ``invalid_signature`` -> ``revoked`` ->
``expired`` -> ``valid``. All checks run and are stored as flags; the stored ``result`` is the
same function of the flags that ``ck_verification_requests_result_precedence`` enforces.

* The hash is always recomputed: over the presented payload (canonicalised) if given, otherwise
  over the stored ``payload_json`` bytes. The Ed25519 signature (presented, or stored) is checked
  against the public key of the credential's **own** issuer row (historical key, BR-17).
* Revoked = a ``revocations`` row effective at the request time **or** ``status = 'revoked'``
  (the revocations table is authoritative; the cached status cannot hide a revocation, BR-12).
* Every request, including ``not_found``, is stored with a ``verification.performed`` audit event
  in one ``BEGIN IMMEDIATE`` transaction (BR-14).
* The credential summary is returned only for ``valid`` / ``revoked`` / ``expired`` and is read
  from the stored, signed payload, never from presented data. Reason codes are not disclosed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import Actor, Role
from app.core.timeutil import format_utc, utc_now
from app.database.session import WriteSessionLocal
from app.models import Credential, Issuer, Revocation, VerificationRequest
from app.models.enums import CredentialStatus, RecordSource, VerificationChannel, VerificationResult
from app.services import audit, ids
from app.services.crypto import canonical_bytes, CanonicalizationError, sha256_hex, verify_signature

SessionFactory = Callable[[], Session]
TRUSTED_SUMMARY_RESULTS = {VerificationResult.VALID.value, VerificationResult.REVOKED.value,
                           VerificationResult.EXPIRED.value}


@dataclass(frozen=True)
class Verifier:
    name: str
    organization: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class VerificationOutcome:
    verification_id: str
    credential_id: str | None
    result: str
    found: bool
    hash_match: bool | None
    signature_valid: bool | None
    is_revoked: bool | None
    is_expired: bool | None
    message: str
    summary: dict[str, Any] | None
    requested_at: datetime


def decide_result(*, found: bool, hash_match: bool | None, signature_valid: bool | None,
                  is_revoked: bool | None, is_expired: bool | None) -> str:
    """BR-13 precedence; mirrors the database CHECK on verification_requests."""
    if not found:
        return VerificationResult.NOT_FOUND.value
    if not hash_match:
        return VerificationResult.TAMPERED.value
    if not signature_valid:
        return VerificationResult.INVALID_SIGNATURE.value
    if is_revoked:
        return VerificationResult.REVOKED.value
    if is_expired:
        return VerificationResult.EXPIRED.value
    return VerificationResult.VALID.value


def _date(value: datetime | None) -> str:
    return format_utc(value)[:10] if value else ""


def _message(result: str, *, revoked_at: datetime | None, expires_at: datetime | None) -> str:
    """Verifier messages of exception_catalogue §2."""
    return {
        "not_found": "No credential with this ID was issued by CredSure.",
        "tampered": "The credential data has been altered.",
        "invalid_signature": "The credential signature is invalid.",
        "revoked": f"This credential was revoked on {_date(revoked_at)}.",
        "expired": f"This credential expired on {_date(expires_at)}.",
        "valid": "This credential is authentic and active.",
    }[result]


def _summary(credential: Credential) -> dict[str, Any]:
    stored = json.loads(credential.payload_json)  # signed, stored payload: trustworthy
    return {
        "credential_type": stored["credential_type"],
        "course_title": stored["course_title"],
        "learner_name": stored["learner_name"],
        "issuer_name": stored["issuer_name"],
        "issued_at": stored["issued_at"],
        "expires_at": stored["expires_at"],
    }


def channel_for(actor: Actor) -> str:
    """``api`` for external verifiers; ``portal`` when platform staff verify for a caller (US-16)."""
    return VerificationChannel.API.value if actor.role is Role.EXTERNAL_VERIFIER else VerificationChannel.PORTAL.value


def verify_credential(
    *,
    actor: Actor,
    credential_id: str,
    verifier: Verifier,
    payload: dict[str, Any] | None = None,
    signature: str | None = None,
    purpose: str | None = None,
    write_factory: SessionFactory | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> VerificationOutcome:
    """Verify, persist the request and audit it. Never raises for an invalid credential."""
    factory = write_factory or WriteSessionLocal
    submitted = credential_id
    with factory() as session:
        requested_at = clock()
        verification_id = ids.next_sequence_id(session, "verification")  # BEGIN IMMEDIATE here
        credential = session.get(Credential, ids.normalize_credential_id(submitted))

        presented_hash: str | None = None
        hash_match = signature_valid = is_revoked = is_expired = None
        revoked_at: datetime | None = None
        if credential is not None:
            if payload is not None:
                try:
                    data: bytes | None = canonical_bytes(payload)
                    presented_hash = sha256_hex(data)
                except CanonicalizationError:  # e.g. a float score: cannot be the signed payload
                    data = None
            else:
                data = credential.payload_json.encode("utf-8")
            computed = sha256_hex(data) if data is not None else None
            hash_match = computed is not None and computed == credential.payload_hash
            issuer = session.get(Issuer, credential.issuer_id)  # the credential's historical issuer
            signature_valid = data is not None and verify_signature(
                issuer.public_key, data, signature if signature is not None else credential.signature)
            revocation = session.query(Revocation).filter_by(credential_id=credential.credential_id).one_or_none()
            revoked_by_table = revocation is not None and revocation.revoked_at <= requested_at
            is_revoked = revoked_by_table or credential.status == CredentialStatus.REVOKED.value
            revoked_at = revocation.revoked_at if revocation is not None else credential.revoked_at
            is_expired = credential.expires_at is not None and requested_at > credential.expires_at

        found = credential is not None
        result = decide_result(found=found, hash_match=hash_match, signature_valid=signature_valid,
                               is_revoked=is_revoked, is_expired=is_expired)
        session.add(VerificationRequest(
            verification_id=verification_id, submitted_credential_id=submitted[:64],
            credential_id=credential.credential_id if found else None,
            verifier_name=verifier.name, verifier_organization=verifier.organization,
            verifier_email=verifier.email, purpose=purpose, channel=channel_for(actor),
            presented_payload_hash=presented_hash, hash_match=hash_match, signature_valid=signature_valid,
            is_revoked=is_revoked, is_expired=is_expired, result=result, requested_at=requested_at,
            responded_at=requested_at, record_source=RecordSource.PLATFORM.value,
        ))
        session.flush()
        audit.record_event(
            session, event_type="verification.performed", entity_type="verification",
            entity_id=verification_id, actor_id=actor.actor_id, actor_role=actor.role.value,
            details={"credential_id": credential.credential_id if found else None, "result": result,
                     "presented_payload_hash": presented_hash},
            occurred_at=requested_at,
        )
        session.commit()

    return VerificationOutcome(
        verification_id=verification_id,
        credential_id=credential.credential_id if found else None,
        result=result, found=found, hash_match=hash_match, signature_valid=signature_valid,
        is_revoked=is_revoked, is_expired=is_expired,
        message=_message(result, revoked_at=revoked_at,
                         expires_at=credential.expires_at if found else None),
        summary=_summary(credential) if found and result in TRUSTED_SUMMARY_RESULTS else None,
        requested_at=requested_at,
    )
