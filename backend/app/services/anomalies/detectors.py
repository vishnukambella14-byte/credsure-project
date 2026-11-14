"""The four documented detectors (exception_catalogue §3). Deterministic, set-based SQL that
recomputes the correct outcome from source records; detectors never read ground truth.

* A1 ``issued_before_completion``: credential whose evidence completion is not completed, has no
  ``completed_at``, or was completed after ``issued_at``.
* A2 ``duplicate_credential_id``: (1) issuance attempts the platform rejected with
  ``duplicate_credential_id`` (from the audit log, by ``attempt_ref``); (2) defensive check for
  credential IDs equal except for case; (3) overwrite check: a credential whose current
  ``payload_hash`` differs from the one recorded in its ``credential.issued`` event.
* A3 ``revoked_verified_as_valid``: verification at or after ``revoked_at`` stored as ``valid``.
* A4 ``tampered_payload_not_detected``: verification whose presented payload hash differs from the
  credential's ``payload_hash`` but is stored as ``valid``; plus the supplementary integrity sweep
  (``sha256(payload_json) != payload_hash``) for storage-level tampering.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import Actor
from app.core.timeutil import format_utc, utc_now
from app.models import AuditEvent, CompletionRecord, Credential, Revocation, VerificationRequest
from app.services import audit
from app.services.crypto import sha256_hex

A1 = "issued_before_completion"
A2 = "duplicate_credential_id"
A3 = "revoked_verified_as_valid"
A4 = "tampered_payload_not_detected"
ANOMALY_TYPES: tuple[str, ...] = (A1, A2, A3, A4)


@dataclass(frozen=True)
class Finding:
    record_type: str  # credential | issuance_attempt | verification_request (ground-truth vocabulary)
    record_id: str
    anomaly_type: str
    evidence: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.record_type, self.record_id, self.anomaly_type


def _ts(value: datetime | None) -> str | None:
    return format_utc(value) if value is not None else None


def detect_a1(session: Session) -> list[Finding]:
    rows = session.execute(
        select(Credential.credential_id, Credential.issued_at, CompletionRecord.completed_at,
               CompletionRecord.status)
        .join(CompletionRecord, CompletionRecord.completion_id == Credential.completion_id)
        .where((CompletionRecord.status != "completed") | CompletionRecord.completed_at.is_(None)
               | (Credential.issued_at < CompletionRecord.completed_at))
        .order_by(Credential.credential_id)
    )
    return [Finding("credential", cid, A1, {"issued_at": _ts(issued), "completed_at": _ts(completed),
                                             "completion_status": status})
            for cid, issued, completed, status in rows]


def detect_a2(session: Session) -> list[Finding]:
    findings = [
        Finding("issuance_attempt", ref, A2, {"credential_id": json.loads(details).get("credential_id"),
                                              "occurred_at": _ts(at)})
        for ref, details, at in session.execute(
            select(AuditEvent.attempt_ref, AuditEvent.details_json, AuditEvent.occurred_at)
            .where(AuditEvent.event_type == "credential.issue_rejected",
                   AuditEvent.reason_code == "duplicate_credential_id")
            .order_by(AuditEvent.event_id))
    ]
    upper = func.upper(Credential.credential_id)
    for (duplicate,) in session.execute(select(upper).group_by(upper).having(func.count() > 1)):
        findings.append(Finding("credential", duplicate, A2, {"check": "case_insensitive_duplicate"}))
    issued = session.execute(
        select(AuditEvent.entity_id, AuditEvent.details_json, Credential.payload_hash)
        .join(Credential, Credential.credential_id == AuditEvent.entity_id)
        .where(AuditEvent.event_type == "credential.issued"))
    for credential_id, details, current in issued:
        recorded = json.loads(details).get("payload_hash")
        if recorded is not None and recorded != current:
            findings.append(Finding("credential", credential_id, A2,
                                    {"check": "overwritten", "issued_hash": recorded, "current_hash": current}))
    return findings


def detect_a3(session: Session) -> list[Finding]:
    rows = session.execute(
        select(VerificationRequest.verification_id, VerificationRequest.credential_id,
               VerificationRequest.requested_at, Revocation.revoked_at)
        .join(Revocation, Revocation.credential_id == VerificationRequest.credential_id)
        .where(Revocation.revoked_at <= VerificationRequest.requested_at, VerificationRequest.result == "valid")
        .order_by(VerificationRequest.verification_id)
    )
    return [Finding("verification_request", vid, A3, {"credential_id": cid, "requested_at": _ts(requested),
                                                       "revoked_at": _ts(revoked)})
            for vid, cid, requested, revoked in rows]


def detect_a4(session: Session) -> list[Finding]:
    rows = session.execute(
        select(VerificationRequest.verification_id, VerificationRequest.credential_id,
               VerificationRequest.presented_payload_hash, Credential.payload_hash)
        .join(Credential, Credential.credential_id == VerificationRequest.credential_id)
        .where(VerificationRequest.presented_payload_hash.is_not(None),
               VerificationRequest.presented_payload_hash != Credential.payload_hash,
               VerificationRequest.result == "valid")
        .order_by(VerificationRequest.verification_id)
    )
    findings = [Finding("verification_request", vid, A4, {"credential_id": cid, "presented_hash": presented,
                                                           "payload_hash": stored})
                for vid, cid, presented, stored in rows]
    for credential_id, payload_json, payload_hash in session.execute(
            select(Credential.credential_id, Credential.payload_json, Credential.payload_hash)
            .order_by(Credential.credential_id)):
        computed = sha256_hex(payload_json.encode("utf-8"))
        if computed != payload_hash:  # supplementary integrity sweep
            findings.append(Finding("credential", credential_id, A4,
                                    {"check": "integrity_sweep", "computed_hash": computed, "payload_hash": payload_hash}))
    return findings


DETECTORS: tuple[Callable[[Session], list[Finding]], ...] = (detect_a1, detect_a2, detect_a3, detect_a4)


def run_detectors(session: Session) -> list[Finding]:
    """All four detectors over the stored records (read-only)."""
    return [finding for detector in DETECTORS for finding in detector(session)]


def run_detection(
    *, actor: Actor, read_factory: Callable[[], Session], write_factory: Callable[[], Session],
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """``POST /api/v1/anomalies/run``: detect, then audit ``anomaly.run_completed`` with the counts."""
    run_at = clock()
    with read_factory() as session:
        findings = run_detectors(session)
    by_type = {kind: sum(1 for f in findings if f.anomaly_type == kind) for kind in ANOMALY_TYPES}
    audit.record_event_in_new_transaction(
        write_factory, event_type="anomaly.run_completed", entity_type="anomaly_run", entity_id=format_utc(run_at),
        actor_id=actor.actor_id, actor_role=actor.role.value, details={"findings_by_type": by_type},
        occurred_at=run_at,
    )
    return {"run_at": run_at, "findings_by_type": by_type, "findings": findings}
