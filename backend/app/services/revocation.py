"""Revocation (FR-16, BR-12, BR-16, ADR-017 / D1). Atomic and irreversible.

One ``BEGIN IMMEDIATE`` transaction: look up the credential (404 ``credential_not_found``,
not audited) -> reject an already revoked credential (409, audited) -> validate the reason (422,
audited) -> INSERT ``revocations`` -> UPDATE the credential ``active -> revoked`` with the same
``revoked_at`` (the only transition ``trg_credentials_status_transition`` accepts) -> audit
``credential.revoked`` -> commit. Any failure rolls back all of it; audited rejections are then
written in their own transaction. There is no reinstatement operation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError, map_integrity_error
from app.core.security import Actor, Permission, ensure_permitted
from app.core.timeutil import utc_now
from app.database.session import WriteSessionLocal
from app.models import Credential, Revocation
from app.models.enums import CredentialStatus, RecordSource, RevocationReason
from app.services import audit, ids

SessionFactory = Callable[[], Session]
_REASONS = {reason.value for reason in RevocationReason}


@dataclass(frozen=True)
class RevocationOutcome:
    credential: Credential
    revocation: Revocation


def revoke_credential(
    *,
    actor: Actor,
    credential_id: str,
    reason_code: str | None,
    reason_text: str | None = None,
    requested_by: str | None = None,
    write_factory: SessionFactory | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> RevocationOutcome:
    ensure_permitted(actor, Permission.CREDENTIAL_REVOKE)
    factory = write_factory or WriteSessionLocal
    normalized = ids.normalize_credential_id(credential_id)
    session = factory()
    try:
        credential = session.get(Credential, normalized)  # BEGIN IMMEDIATE here
        if credential is None:
            raise DomainError("credential_not_found")
        already = session.query(Revocation).filter_by(credential_id=normalized).one_or_none()
        if already is not None or credential.status == CredentialStatus.REVOKED.value:
            raise DomainError("credential_already_revoked")
        if reason_code not in _REASONS or (reason_code == RevocationReason.OTHER.value and not reason_text):
            raise DomainError("revocation_reason_required")

        now = clock()
        revocation = Revocation(
            revocation_id=ids.next_sequence_id(session, "revocation"), credential_id=normalized,
            reason_code=reason_code, reason_text=reason_text, requested_by=requested_by,
            approved_by=actor.actor_id, revoked_at=now, record_source=RecordSource.PLATFORM.value,
            created_at=now,
        )
        session.add(revocation)
        session.flush()  # the revocation row must exist before the status transition (trigger)
        credential.status = CredentialStatus.REVOKED.value
        credential.revoked_at = now
        session.flush()
        audit.record_event(
            session, event_type="credential.revoked", entity_type="credential", entity_id=normalized,
            actor_id=actor.actor_id, actor_role=actor.role.value, reason_code=reason_code,
            details={"revocation_id": revocation.revocation_id, "reason_code": reason_code,
                     "requested_by": requested_by},
            occurred_at=now,
        )
        session.commit()
        return RevocationOutcome(credential=credential, revocation=revocation)
    except IntegrityError as exc:  # e.g. a concurrent revocation won the race
        session.rollback()
        error = map_integrity_error(exc)
    except DomainError as exc:
        session.rollback()
        if exc.code == "credential_not_found":
            raise
        error = exc
    finally:
        session.close()

    audit.record_event_in_new_transaction(
        factory, event_type="credential.revoke_rejected", entity_type="credential", entity_id=normalized,
        actor_id=actor.actor_id, actor_role=actor.role.value,
        outcome="error" if error.status_code >= 500 else "rejected", reason_code=error.code,
        details={"reason_code": reason_code}, clock=clock,
    )
    raise error
