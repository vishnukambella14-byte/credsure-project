"""Credential issuance (FR-09–FR-11, BR-02/03/08/09, BR-26, ADR-023 / D7, D1, D3).

Order of checks inside one ``BEGIN IMMEDIATE`` transaction (D7):

1. Request schema (API layer).
2. ``attempt_ref``: supplied and unused, or generated; a reused one is ``409 duplicate_attempt_ref``
   (not a new attempt: logged, not audited).
3. Credential ID: supplied ID normalised to upper case and checked for format and uniqueness
   **before** eligibility (``409 duplicate_credential_id``); otherwise generated.
4. Eligibility (T03 ``evaluate``): ``already_issued`` -> ``409 credential_already_issued``
   (SR-12); ``signing_key_unavailable`` as the only failure -> ``503 signing_key_unavailable``;
   any other failure -> ``422 not_eligible``.
5. Build the canonical payload (data_dictionary §4), hash and sign it.
6. Plain INSERT of the credential plus ``eligibility.evaluated`` and ``credential.issued``.
7. Commit.

Every attempt that passes step 2 ends with exactly one terminal audit event carrying its
``attempt_ref``: ``credential.issued`` commits with the credential; on any rejection (including
an ``IntegrityError`` from a uniqueness race at insert) the transaction is rolled back and
``credential.issue_rejected`` is written in its own transaction. The private key is loaded
*before* the write lock is taken, so no file I/O happens while the lock is held (except the
rare case where the course's issuer changed in between).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError, map_integrity_error
from app.core.security import Actor, Permission, ensure_permitted
from app.core.timeutil import format_utc, utc_now
from app.database.session import SessionLocal, WriteSessionLocal
from app.models import AuditEvent, Course, Credential, Issuer, Learner
from app.models.enums import TERMINAL_ISSUANCE_EVENTS, CredentialStatus, RecordSource
from app.services import audit, ids
from app.services.crypto import KeyStore, canonical_json, default_key_store, format_score, sha256_hex, sign
from app.services.eligibility import SIGNING_KEY_UNAVAILABLE, EligibilityResult, evaluate, ineligibility_error

SCHEMA_VERSION = "1.0"
SessionFactory = Callable[[], Session]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedCredential:
    credential: Credential
    payload: dict[str, Any]
    attempt_ref: str


def build_payload(
    *,
    credential_id: str,
    learner: Learner,
    course: Course,
    issuer: Issuer,
    eligibility: EligibilityResult,
    issued_at: datetime,
    expires_at: datetime | None,
) -> dict[str, Any]:
    """Canonical payload fields of data_dictionary §4 (schema 1.0); every key always present."""
    rule, completion = eligibility.rule, eligibility.completion
    assert rule is not None and completion is not None  # guaranteed by an eligible result
    return {
        "schema_version": SCHEMA_VERSION,
        "credential_id": credential_id,
        "credential_type": rule.credential_type,
        "learner_id": learner.learner_id,
        "learner_name": f"{learner.given_name} {learner.family_name}",
        "course_id": course.course_id,
        "course_code": course.course_code,
        "course_title": course.title,
        "rule_id": rule.rule_id,
        "rule_version": rule.version,
        "completion_id": completion.completion_id,
        "completed_at": format_utc(completion.completed_at) if completion.completed_at else None,
        "score": format_score(completion.score),
        "issuer_id": issuer.issuer_id,
        "issuer_name": issuer.name,
        "signing_key_id": issuer.signing_key_id,
        "issued_at": format_utc(issued_at),
        "expires_at": format_utc(expires_at) if expires_at else None,
    }


def _issuance_error(result: EligibilityResult) -> DomainError | None:
    if result.eligible:
        return None
    if result.reasons == (SIGNING_KEY_UNAVAILABLE,):  # otherwise eligible: a server-side fault
        return DomainError("signing_key_unavailable", details={"reasons": list(result.reasons)})
    return ineligibility_error(result)


def _preload_key(
    read_factory: SessionFactory, course_id: str, keys: KeyStore
) -> tuple[str | None, Ed25519PrivateKey | None]:
    """Load the course issuer's key before the write lock (file I/O outside the lock)."""
    with read_factory() as session:
        course = session.get(Course, course_id)
        issuer = session.get(Issuer, course.issuer_id) if course is not None else None
    if issuer is None or not keys.is_available(issuer.signing_key_id, issuer.record_source):
        return None, None
    try:
        return issuer.issuer_id, keys.load(issuer.signing_key_id, issuer.record_source)
    except DomainError:
        return None, None


def _attempt_ref_used(session: Session, attempt_ref: str) -> bool:
    return bool(session.execute(select(exists().where(
        AuditEvent.attempt_ref == attempt_ref,
        AuditEvent.event_type.in_(TERMINAL_ISSUANCE_EVENTS),
    ))).scalar())


def _record_rejection(
    write_factory: SessionFactory,
    *,
    actor: Actor,
    attempt_ref: str,
    error: DomainError,
    details: dict[str, Any],
    eligibility: EligibilityResult | None,
    clock: Callable[[], datetime],
) -> None:
    """After rollback: eligibility outcome (if evaluated) + the terminal rejection, one transaction."""
    with write_factory() as session:
        target = f"{details['learner_id']}:{details['course_id']}"
        if eligibility is not None:
            audit.record_event(
                session, event_type="eligibility.evaluated", entity_type="credential", entity_id=target,
                actor_id=actor.actor_id, actor_role=actor.role.value, attempt_ref=attempt_ref,
                outcome="success" if eligibility.eligible else "rejected",
                details={"eligible": eligibility.eligible, "reasons": list(eligibility.reasons),
                         "rule_id": eligibility.rule_id, "completion_id": eligibility.completion_id},
                clock=clock,
            )
        reasons = list(eligibility.reasons) if eligibility is not None and not eligibility.eligible else None
        audit.record_event(
            session, event_type="credential.issue_rejected", entity_type="credential",
            entity_id=details.get("credential_id") or target, actor_id=actor.actor_id,
            actor_role=actor.role.value, outcome="error" if error.status_code >= 500 else "rejected",
            reason_code=error.code, attempt_ref=attempt_ref,
            details={**details, **({"reasons": reasons} if reasons is not None else {})},
            clock=clock,
        )
        session.commit()


def issue_credential(
    *,
    actor: Actor,
    learner_id: str,
    course_id: str,
    credential_id: str | None = None,
    attempt_ref: str | None = None,
    write_factory: SessionFactory | None = None,
    read_factory: SessionFactory | None = None,
    key_store: KeyStore | None = None,
    clock: Callable[[], datetime] = utc_now,
    record_source: str = RecordSource.PLATFORM.value,
) -> IssuedCredential:
    """Issue one credential, or raise the documented DomainError after auditing the rejection."""
    ensure_permitted(actor, Permission.CREDENTIAL_ISSUE)
    write_factory = write_factory or WriteSessionLocal
    read_factory = read_factory or SessionLocal
    keys = key_store or default_key_store()
    preloaded_issuer_id, private_key = _preload_key(read_factory, course_id, keys)

    supplied_id = ids.normalize_credential_id(credential_id) if credential_id is not None else None
    eligibility: EligibilityResult | None = None
    details: dict[str, Any] = {"learner_id": learner_id, "course_id": course_id, "credential_id": supplied_id}

    session = write_factory()
    try:
        # 2. attempt reference (BEGIN IMMEDIATE starts with this first statement)
        if attempt_ref is not None:
            if _attempt_ref_used(session, attempt_ref):
                raise DomainError("duplicate_attempt_ref", details={"attempt_ref": attempt_ref})
        else:
            attempt_ref = ids.new_attempt_ref()
    except BaseException:
        session.rollback()
        session.close()
        raise

    try:
        # 3. credential ID before eligibility (D7)
        if supplied_id is not None:
            if not ids.is_credential_id(supplied_id):
                raise DomainError("validation_error", "credential_id must be CRED- + 12 Crockford characters.")
            if session.get(Credential, supplied_id) is not None:
                raise DomainError("duplicate_credential_id")
            new_id = supplied_id
        else:
            new_id = ids.new_credential_id()
        details["credential_id"] = new_id

        # 4. eligibility (BR-04, D5)
        now = clock()
        eligibility = evaluate(session, learner_id, course_id, at=now, key_store=keys)
        error = _issuance_error(eligibility)
        if error is not None:
            raise error

        # 5. payload, hash, signature
        learner = session.get(Learner, learner_id)
        course = session.get(Course, course_id)
        issuer = session.get(Issuer, eligibility.issuer_id)
        if private_key is None or preloaded_issuer_id != issuer.issuer_id:
            private_key = keys.load(issuer.signing_key_id, issuer.record_source)  # rare: issuer changed
        rule = eligibility.rule
        expires_at = now + timedelta(days=rule.validity_days) if rule.validity_days else None
        payload = build_payload(credential_id=new_id, learner=learner, course=course, issuer=issuer,
                                eligibility=eligibility, issued_at=now, expires_at=expires_at)
        canonical = canonical_json(payload)
        data = canonical.encode("utf-8")

        # 6. plain INSERT + audit events in the same transaction
        credential = Credential(
            credential_id=new_id, learner_id=learner_id, course_id=course_id,
            completion_id=eligibility.completion_id, rule_id=rule.rule_id, issuer_id=issuer.issuer_id,
            credential_type=rule.credential_type, status=CredentialStatus.ACTIVE.value,
            issued_at=now, expires_at=expires_at, revoked_at=None, payload_json=canonical,
            payload_hash=sha256_hex(data), signature=sign(private_key, data),
            signing_key_id=issuer.signing_key_id, schema_version=SCHEMA_VERSION,
            issued_by=actor.actor_id, record_source=record_source, created_at=now,
        )
        session.add(credential)
        session.flush()
        target = f"{learner_id}:{course_id}"
        audit.record_event(
            session, event_type="eligibility.evaluated", entity_type="credential", entity_id=target,
            actor_id=actor.actor_id, actor_role=actor.role.value, attempt_ref=attempt_ref,
            details={"eligible": True, "reasons": [], "rule_id": rule.rule_id,
                     "completion_id": eligibility.completion_id},
            occurred_at=now,
        )
        audit.record_event(
            session, event_type="credential.issued", entity_type="credential", entity_id=new_id,
            actor_id=actor.actor_id, actor_role=actor.role.value, attempt_ref=attempt_ref,
            details={"learner_id": learner_id, "course_id": course_id, "rule_id": rule.rule_id,
                     "completion_id": eligibility.completion_id, "payload_hash": credential.payload_hash,
                     "signing_key_id": issuer.signing_key_id},
            occurred_at=now,
        )
        # 7. commit
        session.commit()
        return IssuedCredential(credential=credential, payload=payload, attempt_ref=attempt_ref)
    except IntegrityError as exc:  # uniqueness race caught by the database (final authority)
        session.rollback()
        error = map_integrity_error(exc)
    except DomainError as exc:
        session.rollback()
        error = exc
    except Exception:  # unexpected defect: the attempt still gets its terminal event
        session.rollback()
        logger.exception("Unexpected error during issuance attempt %s", attempt_ref)
        error = DomainError("internal_server_error")
    finally:
        session.close()

    if error.code != "duplicate_attempt_ref":  # a reused reference is not a new attempt (D7)
        _record_rejection(write_factory, actor=actor, attempt_ref=attempt_ref, error=error,
                          details=details, eligibility=eligibility, clock=clock)
    error.details = {**(error.details or {}), "attempt_ref": attempt_ref}
    raise error
