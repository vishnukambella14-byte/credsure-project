"""Eligibility evaluation (BR-04, BR-07 / D5, AS-04, SR-12). Read-only: never inserts anything.

Checks (all are evaluated; every failing reason is returned, BR-04):

| # | check key             | failure reason(s)                              |
|---|-----------------------|------------------------------------------------|
| 1 | learner_active        | learner_not_found / learner_inactive           |
| 2 | course_active         | course_not_found / course_inactive             |
| 3 | completed             | no_completed_record (latest completed attempt) |
| 4 | completion_not_future | completion_in_future                           |
| 5 | rule_found            | no_applicable_rule                             |
| 6 | score_ok              | score_below_minimum                            |
| 7 | attendance_ok         | attendance_below_minimum                       |
| 8 | not_already_issued    | already_issued                                 |
| 9 | issuer_ready          | issuer_inactive / signing_key_unavailable      |

A check that cannot be evaluated because an earlier fact is missing (e.g. no completion, so no
rule to compare the score against) is reported as ``None`` and adds no extra reason.

Rule selection (D5): the version of the course with ``status IN ('active','retired')`` and
``effective_from <= completed_at < COALESCE(effective_to, +inf)``. There is no fallback to
another version; windows never overlap by construction, so more than one match is a data defect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.timeutil import to_utc, utc_now
from app.models import CompletionRecord, Course, Credential, CredentialRule, Issuer, Learner
from app.models.enums import (
    CompletionStatus,
    CourseStatus,
    CredentialStatus,
    IssuerStatus,
    LearnerStatus,
    RuleStatus,
)
from app.services.crypto import KeyStore, default_key_store, to_decimal

# Internal reason codes (BR-04). SR-12: "already_issued" maps to 409 credential_already_issued.
LEARNER_NOT_FOUND = "learner_not_found"
LEARNER_INACTIVE = "learner_inactive"
COURSE_NOT_FOUND = "course_not_found"
COURSE_INACTIVE = "course_inactive"
NO_COMPLETED_RECORD = "no_completed_record"
COMPLETION_IN_FUTURE = "completion_in_future"
NO_APPLICABLE_RULE = "no_applicable_rule"
SCORE_BELOW_MINIMUM = "score_below_minimum"
ATTENDANCE_BELOW_MINIMUM = "attendance_below_minimum"
ALREADY_ISSUED = "already_issued"
ISSUER_INACTIVE = "issuer_inactive"
SIGNING_KEY_UNAVAILABLE = "signing_key_unavailable"

CHECK_KEYS: tuple[str, ...] = (
    "learner_active", "course_active", "completed", "completion_not_future", "rule_found",
    "score_ok", "attendance_ok", "not_already_issued", "issuer_ready",
)


class RuleWindowConflict(RuntimeError):
    """More than one rule version covers a timestamp: a data defect (ADR-021 forbids overlaps)."""


@dataclass(frozen=True)
class EligibilityResult:
    learner_id: str
    course_id: str
    eligible: bool
    reasons: tuple[str, ...]
    checks: dict[str, bool | None]
    evaluated_at: datetime
    rule_id: str | None = None
    rule_version: int | None = None
    completion_id: str | None = None
    issuer_id: str | None = None
    signing_key_id: str | None = None
    # Loaded facts for the caller (issuance builds the payload from them); not part of the API body.
    rule: CredentialRule | None = field(default=None, repr=False, compare=False)
    completion: CompletionRecord | None = field(default=None, repr=False, compare=False)


def applicable_rule(session: Session, course_id: str, completed_at: datetime) -> CredentialRule | None:
    """The single rule version effective on or before ``completed_at`` (D5), or None. No fallback."""
    moment = to_utc(completed_at)
    rules = session.execute(
        select(CredentialRule).where(
            CredentialRule.course_id == course_id,
            CredentialRule.status.in_([RuleStatus.ACTIVE.value, RuleStatus.RETIRED.value]),
            CredentialRule.effective_from <= moment,
            or_(CredentialRule.effective_to.is_(None), CredentialRule.effective_to > moment),
        )
    ).scalars().all()
    if len(rules) > 1:
        raise RuleWindowConflict(
            f"{len(rules)} rule versions of {course_id} cover {moment.isoformat()}: "
            + ", ".join(rule.rule_id for rule in rules)
        )
    return rules[0] if rules else None


def latest_completed_attempt(session: Session, learner_id: str, course_id: str) -> CompletionRecord | None:
    """AS-04: the latest attempt with ``status = completed`` (highest attempt_number)."""
    return session.execute(
        select(CompletionRecord)
        .where(
            CompletionRecord.learner_id == learner_id,
            CompletionRecord.course_id == course_id,
            CompletionRecord.status == CompletionStatus.COMPLETED.value,
        )
        .order_by(CompletionRecord.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def _meets(value: float | None, minimum: float | None) -> bool:
    """Threshold check with Decimal comparison (ADR-022). No minimum: passes. NULL value: fails."""
    if minimum is None:
        return True
    if value is None:
        return False
    return to_decimal(value) >= to_decimal(minimum)


def evaluate(
    session: Session,
    learner_id: str,
    course_id: str,
    *,
    at: datetime | None = None,
    key_store: KeyStore | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> EligibilityResult:
    """Evaluate BR-04 for (learner, course) at time ``at`` (default: now). Read-only."""
    moment = to_utc(at) if at is not None else clock()
    keys = key_store or default_key_store()
    reasons: list[str] = []
    checks: dict[str, bool | None] = dict.fromkeys(CHECK_KEYS)

    # 1. learner
    learner = session.get(Learner, learner_id)
    if learner is None:
        checks["learner_active"] = False
        reasons.append(LEARNER_NOT_FOUND)
    else:
        checks["learner_active"] = learner.status == LearnerStatus.ACTIVE.value
        if not checks["learner_active"]:
            reasons.append(LEARNER_INACTIVE)

    # 2. course
    course = session.get(Course, course_id)
    if course is None:
        checks["course_active"] = False
        reasons.append(COURSE_NOT_FOUND)
    else:
        checks["course_active"] = course.status == CourseStatus.ACTIVE.value
        if not checks["course_active"]:
            reasons.append(COURSE_INACTIVE)

    # 3–7. completion, timing, applicable rule, thresholds
    completion = latest_completed_attempt(session, learner_id, course_id)
    rule: CredentialRule | None = None
    checks["completed"] = completion is not None
    if completion is None:
        reasons.append(NO_COMPLETED_RECORD)
    else:
        completed_at = completion.completed_at  # NOT NULL for completed records (CHECK)
        checks["completion_not_future"] = completed_at <= moment
        if not checks["completion_not_future"]:
            reasons.append(COMPLETION_IN_FUTURE)
        rule = applicable_rule(session, course_id, completed_at)
        checks["rule_found"] = rule is not None
        if rule is None:
            reasons.append(NO_APPLICABLE_RULE)
        else:
            checks["score_ok"] = _meets(completion.score, rule.min_score)
            if not checks["score_ok"]:
                reasons.append(SCORE_BELOW_MINIMUM)
            checks["attendance_ok"] = _meets(completion.attendance_pct, rule.min_attendance_pct)
            if not checks["attendance_ok"]:
                reasons.append(ATTENDANCE_BELOW_MINIMUM)

    # 8. no active credential for (learner, course)
    active = session.execute(
        select(Credential.credential_id).where(
            and_(
                Credential.learner_id == learner_id,
                Credential.course_id == course_id,
                Credential.status == CredentialStatus.ACTIVE.value,
            )
        )
    ).first()
    checks["not_already_issued"] = active is None
    if active is not None:
        reasons.append(ALREADY_ISSUED)

    # 9. course issuer active and its signing key available
    issuer: Issuer | None = session.get(Issuer, course.issuer_id) if course is not None else None
    if issuer is not None:
        issuer_active = issuer.status == IssuerStatus.ACTIVE.value
        key_available = keys.is_available(issuer.signing_key_id, issuer.record_source)
        checks["issuer_ready"] = issuer_active and key_available
        if not issuer_active:
            reasons.append(ISSUER_INACTIVE)
        if not key_available:
            reasons.append(SIGNING_KEY_UNAVAILABLE)

    return EligibilityResult(
        learner_id=learner_id,
        course_id=course_id,
        eligible=not reasons,
        reasons=tuple(reasons),
        checks=checks,
        evaluated_at=moment,
        rule_id=rule.rule_id if rule else None,
        rule_version=rule.version if rule else None,
        completion_id=completion.completion_id if completion else None,
        issuer_id=issuer.issuer_id if issuer else None,
        signing_key_id=issuer.signing_key_id if issuer else None,
        rule=rule,
        completion=completion,
    )


def ineligibility_error(result: EligibilityResult) -> DomainError | None:
    """Issuance error for a failed evaluation (SR-12, BR-04).

    ``already_issued`` among the reasons -> ``409 credential_already_issued``; any other failure ->
    ``422 not_eligible``. All reasons are listed in ``details.reasons``. None when eligible.
    """
    if result.eligible:
        return None
    code = "credential_already_issued" if ALREADY_ISSUED in result.reasons else "not_eligible"
    return DomainError(code, details={"reasons": list(result.reasons)})
