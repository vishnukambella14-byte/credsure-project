"""Credential routes: issue, list, detail, share, revoke; revocation list (api_contract §3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import (
    Pagination,
    SessionFactories,
    enforce_scope,
    get_clock,
    get_key_store,
    get_pagination,
    get_session_factories,
)
from app.core.errors import DomainError
from app.core.security import Actor, DenialRecorder, Permission, Role, get_denial_recorder, require_permission
from app.models import Course, Credential, Revocation
from app.schemas.common import Page
from app.schemas.credentials import (
    CredentialDetailOut,
    CredentialOut,
    IssueCredentialRequest,
    IssuedCredentialOut,
    RevocationOut,
    RevokeRequest,
    RevokeResponse,
    ShareBundleOut,
)
from app.services import ids
from app.services.crypto import KeyStore
from app.services.issuance import issue_credential
from app.services.revocation import revoke_credential

router = APIRouter(tags=["credentials"])


def _credential_out(credential: Credential) -> CredentialOut:
    return CredentialOut.model_validate(credential, from_attributes=True)


def _revocation_out(revocation: Revocation | None) -> RevocationOut | None:
    return RevocationOut.model_validate(revocation, from_attributes=True) if revocation else None


@router.post("/credentials", response_model=IssuedCredentialOut, status_code=status.HTTP_201_CREATED)
def issue(
    body: IssueCredentialRequest,
    actor: Actor = Depends(require_permission(Permission.CREDENTIAL_ISSUE)),
    sessions: SessionFactories = Depends(get_session_factories),
    keys: KeyStore = Depends(get_key_store),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> IssuedCredentialOut:
    issued = issue_credential(
        actor=actor, learner_id=body.learner_id, course_id=body.course_id,
        credential_id=body.credential_id, attempt_ref=body.attempt_ref,
        write_factory=sessions.write, read_factory=sessions.read, key_store=keys, clock=clock,
    )
    c = issued.credential
    return IssuedCredentialOut(
        credential_id=c.credential_id, learner_id=c.learner_id, course_id=c.course_id,
        completion_id=c.completion_id, rule_id=c.rule_id, issuer_id=c.issuer_id,
        credential_type=c.credential_type, status=c.status, issued_at=c.issued_at,
        expires_at=c.expires_at, payload=issued.payload, payload_hash=c.payload_hash,
        signature=c.signature, signing_key_id=c.signing_key_id, attempt_ref=issued.attempt_ref,
    )


@router.get("/credentials", response_model=Page[CredentialOut])
def list_credentials(
    request: Request,
    learner_id: str | None = None,
    course_id: str | None = None,
    credential_status: str | None = Query(None, alias="status"),
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.CREDENTIAL_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> Page[CredentialOut]:
    query = select(Credential)
    if actor.role is Role.LEARNER:  # own records only
        enforce_scope(request, actor, Permission.CREDENTIAL_READ, record_denial,
                      learner_id=learner_id or actor.actor_id)
        query = query.where(Credential.learner_id == actor.actor_id)
    elif actor.role is Role.INSTRUCTOR:  # courses without a lead instructor, or their own (AS-10)
        query = query.join(Course, Course.course_id == Credential.course_id).where(
            or_(Course.lead_instructor_id.is_(None), Course.lead_instructor_id == actor.actor_id))
    if learner_id:
        query = query.where(Credential.learner_id == learner_id)
    if course_id:
        query = query.where(Credential.course_id == course_id)
    if credential_status:
        query = query.where(Credential.status == credential_status)
    with sessions.read() as session:
        total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = session.execute(
            query.order_by(Credential.issued_at.desc(), Credential.credential_id)
            .limit(page.limit).offset(page.offset)
        ).scalars().all()
        items = [_credential_out(row) for row in rows]
    return Page[CredentialOut](items=items, total=total, limit=page.limit, offset=page.offset)


def _load_scoped(
    request: Request, credential_id: str, actor: Actor, permission: Permission,
    record_denial: DenialRecorder, sessions: SessionFactories,
) -> tuple[Credential, Revocation | None]:
    with sessions.read() as session:
        credential = session.get(Credential, ids.normalize_credential_id(credential_id))
        if credential is None:
            raise DomainError("credential_not_found")
        course = session.get(Course, credential.course_id)
        revocation = session.query(Revocation).filter_by(credential_id=credential.credential_id).one_or_none()
    enforce_scope(request, actor, permission, record_denial, learner_id=credential.learner_id,
                  lead_instructor_id=course.lead_instructor_id if course else None)
    return credential, revocation


@router.get("/credentials/{credential_id}", response_model=CredentialDetailOut)
def get_credential(
    request: Request,
    credential_id: str,
    actor: Actor = Depends(require_permission(Permission.CREDENTIAL_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> CredentialDetailOut:
    credential, revocation = _load_scoped(request, credential_id, actor, Permission.CREDENTIAL_READ,
                                          record_denial, sessions)
    base = _credential_out(credential).model_dump()
    return CredentialDetailOut(
        **base, payload=json.loads(credential.payload_json), signature=credential.signature,
        schema_version=credential.schema_version, issued_by=credential.issued_by,
        revocation=_revocation_out(revocation),
    )


@router.get("/credentials/{credential_id}/share", response_model=ShareBundleOut)
def share_credential(
    request: Request,
    credential_id: str,
    actor: Actor = Depends(require_permission(Permission.CREDENTIAL_SHARE)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> ShareBundleOut:
    credential, _ = _load_scoped(request, credential_id, actor, Permission.CREDENTIAL_SHARE,
                                 record_denial, sessions)
    return ShareBundleOut(
        payload=json.loads(credential.payload_json), payload_hash=credential.payload_hash,
        signature=credential.signature, issuer_id=credential.issuer_id,
        signing_key_id=credential.signing_key_id,
    )


@router.post("/credentials/{credential_id}/revoke", response_model=RevokeResponse)
def revoke(
    credential_id: str,
    body: RevokeRequest,
    actor: Actor = Depends(require_permission(Permission.CREDENTIAL_REVOKE)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> RevokeResponse:
    outcome = revoke_credential(
        actor=actor, credential_id=credential_id, reason_code=body.reason_code,
        reason_text=body.reason_text, requested_by=body.requested_by,
        write_factory=sessions.write, clock=clock,
    )
    return RevokeResponse(credential=_credential_out(outcome.credential),
                          revocation=_revocation_out(outcome.revocation))


@router.get("/revocations", response_model=Page[RevocationOut])
def list_revocations(
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.REVOCATION_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> Page[RevocationOut]:
    with sessions.read() as session:
        total = session.execute(select(func.count()).select_from(Revocation)).scalar_one()
        rows = session.execute(
            select(Revocation).order_by(Revocation.revoked_at.desc(), Revocation.revocation_id)
            .limit(page.limit).offset(page.offset)
        ).scalars().all()
        items = [_revocation_out(row) for row in rows]
    return Page[RevocationOut](items=items, total=total, limit=page.limit, offset=page.offset)
