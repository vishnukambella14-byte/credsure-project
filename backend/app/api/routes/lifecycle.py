"""Eligibility, rule activation and audit routes (api_contract §3, §4.1, §4.6, §4.9)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import (
    Pagination,
    SessionFactories,
    enforce_scope,
    get_clock,
    get_key_store,
    get_pagination,
    get_session_factories,
)
from app.core.security import Actor, DenialRecorder, Permission, get_denial_recorder, require_permission
from app.models import AuditEvent, Course
from app.schemas.common import Page
from app.schemas.lifecycle import (
    ActivateRuleRequest,
    ActivateRuleResponse,
    AuditEventOut,
    ChainVerificationOut,
    EligibilityOut,
)
from app.services import audit
from app.services.crypto import KeyStore
from app.services.eligibility import evaluate
from app.services.rules import activate_rule

router = APIRouter()


@router.get("/eligibility", response_model=EligibilityOut, tags=["eligibility"])
def eligibility(
    request: Request,
    learner_id: str = Query(min_length=1),
    course_id: str = Query(min_length=1),
    actor: Actor = Depends(require_permission(Permission.ELIGIBILITY_EVALUATE)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
    keys: KeyStore = Depends(get_key_store),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> EligibilityOut:
    """Evaluate without side effects (FR-08). Learners: own; instructors: their courses (AS-10)."""
    with sessions.read() as session:
        course = session.get(Course, course_id)
        enforce_scope(request, actor, Permission.ELIGIBILITY_EVALUATE, record_denial, learner_id=learner_id,
                      lead_instructor_id=course.lead_instructor_id if course else None)
        result = evaluate(session, learner_id, course_id, at=clock(), key_store=keys)
    return EligibilityOut(
        learner_id=result.learner_id, course_id=result.course_id, eligible=result.eligible,
        rule_id=result.rule_id, rule_version=result.rule_version, completion_id=result.completion_id,
        reasons=list(result.reasons), checks=result.checks, evaluated_at=result.evaluated_at,
    )


@router.post("/rules/{rule_id}/activate", response_model=ActivateRuleResponse, tags=["rules"])
def activate(
    rule_id: str,
    body: ActivateRuleRequest | None = Body(default=None),
    actor: Actor = Depends(require_permission(Permission.RULE_ACTIVATE)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> ActivateRuleResponse:
    outcome = activate_rule(actor=actor, rule_id=rule_id,
                            effective_from=body.effective_from if body else None,
                            write_factory=sessions.write, clock=clock)
    rule = outcome.rule
    return ActivateRuleResponse(rule_id=rule.rule_id, status=rule.status, effective_from=rule.effective_from,
                                activated_at=rule.activated_at, retired_rule_id=outcome.retired_rule_id)


@router.get("/audit-events/verify-chain", response_model=ChainVerificationOut, tags=["audit"])
def verify_chain(
    actor: Actor = Depends(require_permission(Permission.AUDIT_CHAIN_VERIFY)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> ChainVerificationOut:
    """Recompute the chain (FR-19), then record ``audit.chain_verified`` with the outcome."""
    with sessions.read() as session:
        result = audit.verify_chain(session)
    audit.record_event_in_new_transaction(
        sessions.write, event_type="audit.chain_verified", entity_type="audit", entity_id="audit_events",
        actor_id=actor.actor_id, actor_role=actor.role.value,
        outcome="success" if result.valid else "error", reason_code=result.problem,
        details={"valid": result.valid, "events_checked": result.events_checked,
                 "first_broken_event_id": result.first_broken_event_id},
    )
    return ChainVerificationOut(valid=result.valid, events_checked=result.events_checked,
                                first_broken_event_id=result.first_broken_event_id)


@router.get("/audit-events", response_model=Page[AuditEventOut], tags=["audit"])
def list_audit_events(
    entity_type: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    actor_id: str | None = None,
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.AUDIT_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> Page[AuditEventOut]:
    query = select(AuditEvent)
    for column, value in ((AuditEvent.entity_type, entity_type), (AuditEvent.entity_id, entity_id),
                          (AuditEvent.event_type, event_type), (AuditEvent.actor_id, actor_id)):
        if value:
            query = query.where(column == value)
    with sessions.read() as session:
        total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = session.execute(query.order_by(AuditEvent.event_id.desc())
                               .limit(page.limit).offset(page.offset)).scalars().all()
        items = [
            AuditEventOut(
                event_id=e.event_id, event_uuid=e.event_uuid, event_type=e.event_type,
                entity_type=e.entity_type, entity_id=e.entity_id, actor_id=e.actor_id,
                actor_role=e.actor_role, outcome=e.outcome, reason_code=e.reason_code,
                attempt_ref=e.attempt_ref, details=json.loads(e.details_json), occurred_at=e.occurred_at,
                previous_hash=e.previous_hash, event_hash=e.event_hash,
            )
            for e in rows
        ]
    return Page[AuditEventOut](items=items, total=total, limit=page.limit, offset=page.offset)
