"""Verification routes: public verify; history and detail (api_contract §3, §4.3)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import (
    Pagination,
    SessionFactories,
    enforce_scope,
    get_clock,
    get_pagination,
    get_session_factories,
)
from app.core.security import Actor, DenialRecorder, Permission, Role, get_denial_recorder, require_permission
from app.models import Credential, VerificationRequest
from app.schemas.common import Page
from app.schemas.verifications import (
    CredentialSummary,
    VerificationChecks,
    VerificationRecordOut,
    VerifyRequest,
    VerifyResponse,
)
from app.services.verification import Verifier, verify_credential

router = APIRouter(tags=["verifications"])


@router.post("/verifications", response_model=VerifyResponse)
def verify(
    body: VerifyRequest,
    actor: Actor = Depends(require_permission(Permission.VERIFY)),  # public: no headers needed
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> VerifyResponse:
    """Always 200 for a well-formed request: an invalid credential is a result, not an error."""
    outcome = verify_credential(
        actor=actor, credential_id=body.credential_id, payload=body.payload, signature=body.signature,
        verifier=Verifier(body.verifier.name, body.verifier.organization, body.verifier.email),
        purpose=body.purpose, write_factory=sessions.write, clock=clock,
    )
    return VerifyResponse(
        verification_id=outcome.verification_id, credential_id=outcome.credential_id,
        result=outcome.result,
        checks=VerificationChecks(found=outcome.found, hash_match=outcome.hash_match,
                                  signature_valid=outcome.signature_valid, is_revoked=outcome.is_revoked,
                                  is_expired=outcome.is_expired),
        message=outcome.message,
        credential=CredentialSummary(**outcome.summary) if outcome.summary else None,
        requested_at=outcome.requested_at,
    )


@router.get("/verifications", response_model=Page[VerificationRecordOut])
def list_verifications(
    credential_id: str | None = None,
    result: str | None = None,
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.VERIFICATION_HISTORY_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> Page[VerificationRecordOut]:
    query = select(VerificationRequest)
    if actor.role is Role.LEARNER:  # verifications of the learner's own credentials only
        query = query.join(Credential, Credential.credential_id == VerificationRequest.credential_id).where(
            Credential.learner_id == actor.actor_id)
    if credential_id:
        query = query.where(VerificationRequest.credential_id == credential_id.strip().upper())
    if result:
        query = query.where(VerificationRequest.result == result)
    with sessions.read() as session:
        total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = session.execute(
            query.order_by(VerificationRequest.requested_at.desc(), VerificationRequest.verification_id.desc())
            .limit(page.limit).offset(page.offset)
        ).scalars().all()
        items = [VerificationRecordOut.model_validate(r, from_attributes=True) for r in rows]
    return Page[VerificationRecordOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/verifications/{verification_id}", response_model=VerificationRecordOut)
def get_verification(
    request: Request,
    verification_id: str,
    actor: Actor = Depends(require_permission(Permission.VERIFICATION_HISTORY_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> VerificationRecordOut:
    """api_contract §3: AA SS PA only. Learners hold an OWN scope on history, which a single
    record without a learner context cannot satisfy, so they are denied (and audited)."""
    enforce_scope(request, actor, Permission.VERIFICATION_HISTORY_READ, record_denial, learner_id=None)
    with sessions.read() as session:
        record = session.get(VerificationRequest, verification_id)
        if record is None:  # no catalogue code exists for this; the generic documented http_404
            raise StarletteHTTPException(status_code=404, detail="Verification request not found.")
        return VerificationRecordOut.model_validate(record, from_attributes=True)
