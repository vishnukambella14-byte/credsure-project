"""Master-data routes (T05, api_contract §3): create and retrieve issuers, learners, courses,
draft rules and completion records. Rule activation is the T04 route ``POST /rules/{id}/activate``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.sql import Select

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
from app.models import CompletionRecord, Course, CredentialRule, Issuer, Learner
from app.models.enums import RuleStatus
from app.schemas.common import Page
from app.schemas.master_data import (
    CompletionCreate,
    CompletionOut,
    CourseCreate,
    CourseDetailOut,
    CourseOut,
    IssuerCreate,
    IssuerOut,
    LearnerCreate,
    LearnerOut,
    PublicKeyOut,
    RuleCreate,
    RuleOut,
)
from app.services import master_data
from app.services.crypto import KeyStore

router = APIRouter()
Clock = Callable[[], datetime]


def _page(sessions: SessionFactories, query: Select, order_by, page: Pagination, convert) -> dict:  # noqa: ANN001
    with sessions.read() as session:
        total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = session.execute(query.order_by(*order_by).limit(page.limit).offset(page.offset)).scalars().all()
        return {"items": [convert(session, row) for row in rows], "total": total,
                "limit": page.limit, "offset": page.offset}


def _as(model):  # noqa: ANN001, ANN202
    return lambda _session, row: model.model_validate(row, from_attributes=True)


# ------------------------------------------------------------------ issuers ----------------------
@router.post("/issuers", response_model=IssuerOut, status_code=status.HTTP_201_CREATED, tags=["issuers"])
def create_issuer(
    body: IssuerCreate,
    actor: Actor = Depends(require_permission(Permission.ISSUER_CREATE)),
    sessions: SessionFactories = Depends(get_session_factories),
    keys: KeyStore = Depends(get_key_store),
    clock: Clock = Depends(get_clock),
) -> IssuerOut:
    issuer = master_data.create_issuer(actor=actor, **body.model_dump(), write_factory=sessions.write,
                                       key_store=keys, clock=clock)
    return IssuerOut.model_validate(issuer, from_attributes=True)


@router.get("/issuers", response_model=Page[IssuerOut], tags=["issuers"])
def list_issuers(
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.ISSUER_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> dict:
    return _page(sessions, select(Issuer), [Issuer.issuer_id], page, _as(IssuerOut))


def _issuer(sessions: SessionFactories, issuer_id: str) -> Issuer:
    with sessions.read() as session:
        issuer = session.get(Issuer, issuer_id)
    if issuer is None:
        raise DomainError("issuer_not_found")
    return issuer


@router.get("/issuers/{issuer_id}", response_model=IssuerOut, tags=["issuers"])
def get_issuer(
    issuer_id: str,
    actor: Actor = Depends(require_permission(Permission.ISSUER_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> IssuerOut:
    return IssuerOut.model_validate(_issuer(sessions, issuer_id), from_attributes=True)


@router.get("/issuers/{issuer_id}/public-key", response_model=PublicKeyOut, tags=["issuers"])
def get_public_key(
    issuer_id: str,
    actor: Actor = Depends(require_permission(Permission.ISSUER_PUBLIC_KEY_READ)),  # public
    sessions: SessionFactories = Depends(get_session_factories),
) -> PublicKeyOut:
    issuer = _issuer(sessions, issuer_id)
    return PublicKeyOut(issuer_id=issuer.issuer_id, signing_key_id=issuer.signing_key_id,
                        algorithm=issuer.key_algorithm, public_key=issuer.public_key)


# ------------------------------------------------------------------ learners ---------------------
@router.post("/learners", response_model=LearnerOut, status_code=status.HTTP_201_CREATED, tags=["learners"])
def create_learner(
    body: LearnerCreate,
    actor: Actor = Depends(require_permission(Permission.LEARNER_CREATE)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Clock = Depends(get_clock),
) -> LearnerOut:
    learner = master_data.create_learner(actor=actor, **body.model_dump(), write_factory=sessions.write,
                                         clock=clock)
    return LearnerOut.model_validate(learner, from_attributes=True)


@router.get("/learners", response_model=Page[LearnerOut], tags=["learners"])
def list_learners(
    request: Request,
    search: str | None = None,
    learner_status: str | None = Query(None, alias="status"),
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.LEARNER_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> dict:
    if actor.role is Role.LEARNER:  # api_contract §3: I AA SS PA (learners only read their own record)
        enforce_scope(request, actor, Permission.LEARNER_READ, record_denial, learner_id=None)
    query = select(Learner)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Learner.learner_id.ilike(pattern), Learner.email.ilike(pattern),
                                Learner.given_name.ilike(pattern), Learner.family_name.ilike(pattern)))
    if learner_status:
        query = query.where(Learner.status == learner_status)
    return _page(sessions, query, [Learner.learner_id], page, _as(LearnerOut))


@router.get("/learners/{learner_id}", response_model=LearnerOut, tags=["learners"])
def get_learner(
    request: Request,
    learner_id: str,
    actor: Actor = Depends(require_permission(Permission.LEARNER_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> LearnerOut:
    enforce_scope(request, actor, Permission.LEARNER_READ, record_denial, learner_id=learner_id)
    with sessions.read() as session:
        learner = session.get(Learner, learner_id)
    if learner is None:
        raise DomainError("learner_not_found")
    return LearnerOut.model_validate(learner, from_attributes=True)


# ------------------------------------------------------------------ courses ----------------------
@router.post("/courses", response_model=CourseOut, status_code=status.HTTP_201_CREATED, tags=["courses"])
def create_course(
    body: CourseCreate,
    actor: Actor = Depends(require_permission(Permission.COURSE_CREATE)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Clock = Depends(get_clock),
) -> CourseOut:
    course = master_data.create_course(actor=actor, **body.model_dump(), write_factory=sessions.write,
                                       clock=clock)
    return CourseOut.model_validate(course, from_attributes=True)


@router.get("/courses", response_model=Page[CourseOut], tags=["courses"])
def list_courses(
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.COURSE_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> dict:
    return _page(sessions, select(Course), [Course.course_id], page, _as(CourseOut))


@router.get("/courses/{course_id}", response_model=CourseDetailOut, tags=["courses"])
def get_course(
    course_id: str,
    actor: Actor = Depends(require_permission(Permission.COURSE_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> CourseDetailOut:
    with sessions.read() as session:
        course = session.get(Course, course_id)
        if course is None:
            raise DomainError("course_not_found")
        active = session.execute(select(CredentialRule).where(
            CredentialRule.course_id == course_id, CredentialRule.status == RuleStatus.ACTIVE.value)
        ).scalar_one_or_none()
    return CourseDetailOut(**CourseOut.model_validate(course, from_attributes=True).model_dump(),
                           active_rule=RuleOut.model_validate(active, from_attributes=True) if active else None)


# ------------------------------------------------------------------ draft rules ------------------
@router.post("/courses/{course_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED,
             tags=["rules"])
def create_rule(
    course_id: str,
    body: RuleCreate,
    actor: Actor = Depends(require_permission(Permission.RULE_CREATE)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Clock = Depends(get_clock),
) -> RuleOut:
    rule = master_data.create_draft_rule(actor=actor, course_id=course_id, **body.model_dump(),
                                         write_factory=sessions.write, clock=clock)
    return RuleOut.model_validate(rule, from_attributes=True)


@router.get("/courses/{course_id}/rules", response_model=list[RuleOut], tags=["rules"])
def list_rules(
    course_id: str,
    actor: Actor = Depends(require_permission(Permission.RULE_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
) -> list[RuleOut]:
    with sessions.read() as session:
        if session.get(Course, course_id) is None:
            raise DomainError("course_not_found")
        rules = session.execute(select(CredentialRule).where(CredentialRule.course_id == course_id)
                                .order_by(CredentialRule.version)).scalars().all()
        return [RuleOut.model_validate(rule, from_attributes=True) for rule in rules]


# ------------------------------------------------------------------ completions ------------------
def _completion_out(session, completion: CompletionRecord) -> CompletionOut:  # noqa: ANN001
    rule_id, warnings = master_data.rule_context(session, completion)
    return CompletionOut(**{c: getattr(completion, c) for c in CompletionOut.model_fields
                            if c not in ("applicable_rule_id", "warnings")},
                         applicable_rule_id=rule_id, warnings=warnings)


@router.post("/completions", response_model=CompletionOut, status_code=status.HTTP_201_CREATED,
             tags=["completions"])
def create_completion(
    request: Request,
    body: CompletionCreate,
    actor: Actor = Depends(require_permission(Permission.COMPLETION_CREATE)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Clock = Depends(get_clock),
) -> CompletionOut:
    def scope(lead_instructor_id: str | None) -> None:  # instructor: their courses (AS-10), audited
        enforce_scope(request, actor, Permission.COMPLETION_CREATE, record_denial,
                      lead_instructor_id=lead_instructor_id)

    recorded = master_data.record_completion(actor=actor, **body.model_dump(), write_factory=sessions.write,
                                             clock=clock, lead_instructor_check=scope)
    with sessions.read() as session:
        return _completion_out(session, recorded.completion)


@router.get("/completions", response_model=Page[CompletionOut], tags=["completions"])
def list_completions(
    request: Request,
    learner_id: str | None = None,
    course_id: str | None = None,
    page: Pagination = Depends(get_pagination),
    actor: Actor = Depends(require_permission(Permission.COMPLETION_READ)),
    record_denial: DenialRecorder = Depends(get_denial_recorder),
    sessions: SessionFactories = Depends(get_session_factories),
) -> dict:
    query = select(CompletionRecord)
    if actor.role is Role.LEARNER:  # own records only
        enforce_scope(request, actor, Permission.COMPLETION_READ, record_denial,
                      learner_id=learner_id or actor.actor_id)
        query = query.where(CompletionRecord.learner_id == actor.actor_id)
    elif actor.role is Role.INSTRUCTOR:  # courses without a lead instructor, or their own (AS-10)
        query = query.join(Course, Course.course_id == CompletionRecord.course_id).where(
            or_(Course.lead_instructor_id.is_(None), Course.lead_instructor_id == actor.actor_id))
    if learner_id:
        query = query.where(CompletionRecord.learner_id == learner_id)
    if course_id:
        query = query.where(CompletionRecord.course_id == course_id)
    return _page(sessions, query, [CompletionRecord.completion_id], page, _completion_out)
