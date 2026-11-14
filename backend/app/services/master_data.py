"""Master-data creation (T05): issuers (with key generation), learners, courses, draft rules and
completion records. Each create runs in one ``BEGIN IMMEDIATE`` transaction (D3) with its audit
event; IDs come from ``ids.next_sequence_id`` inside that transaction. Database constraints stay
the final authority: an ``IntegrityError`` propagates to the documented error mapping (§1.7).

Rule activation is not here: it is the T04 ``services.rules.activate_rule``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.security import Actor, Permission, ensure_permitted
from app.core.timeutil import to_utc, utc_now
from app.models import CompletionRecord, Course, CredentialRule, Issuer, Learner
from app.models.enums import (
    CompletionStatus,
    CourseStatus,
    IssuerStatus,
    LearnerStatus,
    RecordSource,
    RuleStatus,
)
from app.services import audit, ids
from app.services.crypto import KeyStore, generate_private_key, public_key_b64
from app.services.eligibility import NO_APPLICABLE_RULE, applicable_rule

SessionFactory = Callable[[], Session]
Clock = Callable[[], datetime]
PLATFORM = RecordSource.PLATFORM.value
logger = logging.getLogger(__name__)


def _audit(session: Session, actor: Actor, event_type: str, entity_type: str, entity_id: str,
           details: dict, now: datetime) -> None:
    audit.record_event(session, event_type=event_type, entity_type=entity_type, entity_id=entity_id,
                       actor_id=actor.actor_id, actor_role=actor.role.value, details=details,
                       occurred_at=now)


# ------------------------------------------------------------------ issuers (FR-03, ADR-005/024) ----
def create_issuer(
    *, actor: Actor, name: str, legal_name: str, country_code: str | None = None,
    website_url: str | None = None, supersedes_issuer_id: str | None = None,
    write_factory: SessionFactory, key_store: KeyStore, clock: Clock = utc_now,
) -> Issuer:
    """Platform issuer (ISS-001..ISS-899) with a new Ed25519 key pair.

    The private key goes only to the platform key directory (exclusive create, never overwrite,
    D8); the database stores only the public key. If the transaction fails after the key file
    was written, that file is removed so no orphan key remains.
    """
    ensure_permitted(actor, Permission.ISSUER_CREATE)
    private_key = generate_private_key()  # CPU only; no I/O before the lock
    with write_factory() as session:
        if supersedes_issuer_id is not None:
            if session.get(Issuer, supersedes_issuer_id) is None:
                raise DomainError("issuer_not_found", details={"supersedes_issuer_id": supersedes_issuer_id})
            if session.execute(select(Issuer.issuer_id).where(
                    Issuer.supersedes_issuer_id == supersedes_issuer_id)).first():
                raise DomainError("validation_error", "That issuer has already been superseded.")
        now = clock()
        issuer_id = ids.next_sequence_id(session, "issuer")  # platform range only (D8)
        signing_key_id = ids.signing_key_id_for(issuer_id)
        try:
            key_path = key_store.store_new_platform_key(signing_key_id, private_key)
        except FileExistsError:
            logger.error("Refusing to overwrite existing key file for %s", signing_key_id)
            raise DomainError("internal_server_error") from None
        try:
            issuer = Issuer(
                issuer_id=issuer_id, name=name, legal_name=legal_name, country_code=country_code,
                website_url=website_url, signing_key_id=signing_key_id,
                public_key=public_key_b64(private_key), key_algorithm="Ed25519",
                status=IssuerStatus.ACTIVE.value, supersedes_issuer_id=supersedes_issuer_id,
                valid_from=now, valid_to=None, record_source=PLATFORM, created_by=actor.actor_id,
                created_at=now,
            )
            session.add(issuer)
            session.flush()
            _audit(session, actor, "issuer.created", "issuer", issuer_id,
                   {"signing_key_id": signing_key_id, "supersedes_issuer_id": supersedes_issuer_id}, now)
            session.commit()
        except BaseException:
            session.rollback()
            key_path.unlink(missing_ok=True)  # the key belongs to an issuer that was never created
            raise
        return issuer


# ------------------------------------------------------------------ learners (FR-01, BR-01) ---------
def create_learner(
    *, actor: Actor, given_name: str, family_name: str, email: str, external_ref: str | None = None,
    write_factory: SessionFactory, clock: Clock = utc_now,
) -> Learner:
    ensure_permitted(actor, Permission.LEARNER_CREATE)
    email = email.strip().lower()  # stored lower-case (ck_learners_email_lower)
    with write_factory() as session:
        if session.execute(select(Learner.learner_id).where(Learner.email == email)).first():
            raise DomainError("learner_email_exists")
        if external_ref is not None and session.execute(
                select(Learner.learner_id).where(Learner.external_ref == external_ref)).first():
            raise DomainError("learner_external_ref_exists")
        now = clock()
        learner = Learner(
            learner_id=ids.next_sequence_id(session, "learner"), external_ref=external_ref,
            given_name=given_name, family_name=family_name, email=email,
            status=LearnerStatus.ACTIVE.value, record_source=PLATFORM, created_at=now, updated_at=now,
        )
        session.add(learner)
        session.flush()
        _audit(session, actor, "learner.created", "learner", learner.learner_id, {}, now)
        session.commit()
        return learner


# ------------------------------------------------------------------ courses (FR-02) ---------------
def create_course(
    *, actor: Actor, course_code: str, title: str, issuer_id: str, description: str | None = None,
    credit_hours: int | None = None, lead_instructor_id: str | None = None,
    write_factory: SessionFactory, clock: Clock = utc_now,
) -> Course:
    ensure_permitted(actor, Permission.COURSE_CREATE)
    with write_factory() as session:
        issuer = session.get(Issuer, issuer_id)
        if issuer is None:
            raise DomainError("issuer_not_found")
        if issuer.status != IssuerStatus.ACTIVE.value:
            raise DomainError("issuer_inactive")
        if session.execute(select(Course.course_id).where(Course.course_code == course_code)).first():
            raise DomainError("course_code_exists")
        now = clock()
        course = Course(
            course_id=ids.next_sequence_id(session, "course"), course_code=course_code, title=title,
            description=description, credit_hours=credit_hours, issuer_id=issuer_id,
            lead_instructor_id=lead_instructor_id, status=CourseStatus.ACTIVE.value,
            record_source=PLATFORM, created_at=now, updated_at=now,
        )
        session.add(course)
        session.flush()
        _audit(session, actor, "course.created", "course", course.course_id,
               {"course_code": course_code, "issuer_id": issuer_id}, now)
        session.commit()
        return course


# ------------------------------------------------------------------ draft rules (FR-04, BR-06) -----
def create_draft_rule(
    *, actor: Actor, course_id: str, credential_type: str, min_score: float | None = None,
    min_attendance_pct: float | None = None, validity_days: int | None = None,
    description: str | None = None, write_factory: SessionFactory, clock: Clock = utc_now,
) -> CredentialRule:
    """New ``draft`` version with the next version number. Existing versions are never touched,
    so active and retired criteria stay as they are (activation: T04 ``activate_rule``)."""
    ensure_permitted(actor, Permission.RULE_CREATE)
    with write_factory() as session:
        if session.get(Course, course_id) is None:
            raise DomainError("course_not_found")
        version = (session.execute(select(func.max(CredentialRule.version)).where(
            CredentialRule.course_id == course_id)).scalar() or 0) + 1
        now = clock()
        rule = CredentialRule(
            rule_id=ids.rule_id_for(course_id, version), course_id=course_id, version=version,
            credential_type=credential_type, min_score=min_score, min_attendance_pct=min_attendance_pct,
            validity_days=validity_days, status=RuleStatus.DRAFT.value, effective_from=None,
            effective_to=None, description=description, created_by=actor.actor_id, created_at=now,
            activated_at=None, record_source=PLATFORM,
        )
        session.add(rule)
        session.flush()
        _audit(session, actor, "rule.created", "rule", rule.rule_id, {"version": version}, now)
        session.commit()
        return rule


# ------------------------------------------------------------------ completions (FR-06, BR-19) -----
@dataclass(frozen=True)
class RecordedCompletion:
    completion: CompletionRecord
    applicable_rule_id: str | None
    warnings: list[str]


def _round2(value: float | None) -> float | None:
    return None if value is None else float(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def rule_context(session: Session, completion: CompletionRecord) -> tuple[str | None, list[str]]:
    """Derived ``applicable_rule_id`` and warnings for a completion (api_contract §4.5, D5)."""
    if completion.completed_at is None:
        return None, []
    rule = applicable_rule(session, completion.course_id, completion.completed_at)
    return (rule.rule_id, []) if rule else (None, [NO_APPLICABLE_RULE])


def record_completion(
    *, actor: Actor, learner_id: str, course_id: str, attempt_number: int, status: str,
    score: float | None = None, attendance_pct: float | None = None,
    completed_at: datetime | None = None, write_factory: SessionFactory, clock: Clock = utc_now,
    lead_instructor_check: Callable[[str | None], None] | None = None,
) -> RecordedCompletion:
    """Record a completion fact. ``lead_instructor_check`` receives the course's lead instructor so
    the route can enforce the instructor's course scope (AS-10) with an audited denial."""
    with write_factory() as session:
        if session.get(Learner, learner_id) is None:
            raise DomainError("learner_not_found")
        course = session.get(Course, course_id)
        if course is None:
            raise DomainError("course_not_found")
        if lead_instructor_check is not None:
            lead_instructor_check(course.lead_instructor_id)
        ensure_permitted(actor, Permission.COMPLETION_CREATE, lead_instructor_id=course.lead_instructor_id)

        now = clock()
        moment = to_utc(completed_at) if completed_at is not None else None
        for value in (score, attendance_pct):
            if value is not None and not 0 <= value <= 100:
                raise DomainError("completion_invalid_state", "score and attendance_pct must be 0–100.")
        if status == CompletionStatus.COMPLETED.value and moment is None:
            raise DomainError("completion_invalid_state", "A completed record requires completed_at.")
        if moment is not None and moment > now:
            raise DomainError("completion_in_future")
        if session.execute(select(CompletionRecord.completion_id).where(
                CompletionRecord.learner_id == learner_id, CompletionRecord.course_id == course_id,
                CompletionRecord.attempt_number == attempt_number)).first():
            raise DomainError("completion_attempt_exists")

        completion = CompletionRecord(
            completion_id=ids.next_sequence_id(session, "completion"), learner_id=learner_id,
            course_id=course_id, attempt_number=attempt_number, status=status,
            score=_round2(score), attendance_pct=_round2(attendance_pct), completed_at=moment,
            assessed_by=actor.actor_id, recorded_at=now, updated_at=now, record_source=PLATFORM,
        )
        session.add(completion)
        session.flush()
        rule_id, warnings = rule_context(session, completion)
        _audit(session, actor, "completion.recorded", "completion", completion.completion_id,
               {"learner_id": learner_id, "course_id": course_id, "status": status,
                "attempt_number": attempt_number, "applicable_rule_id": rule_id}, now)
        session.commit()
        return RecordedCompletion(completion=completion, applicable_rule_id=rule_id, warnings=warnings)
