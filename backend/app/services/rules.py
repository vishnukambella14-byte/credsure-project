"""Rule activation (FR-04, BR-06, BR-07 / D5, ADR-021). Only activation belongs to T04;
draft creation and editing are master-data CRUD (T05).

One ``BEGIN IMMEDIATE`` transaction:

1. Rule exists (404 ``rule_not_found``) and is ``draft`` (409 ``rule_not_draft``).
2. It has a criterion (422 ``rule_has_no_criteria``).
3. ``effective_from`` = supplied or now. Not in the future, and later than the current active
   version's ``effective_from`` (422 ``rule_effective_date_invalid``).
4. No recorded completion of the course has ``completed_at >= effective_from``
   (409 ``rule_effective_date_conflict`` listing them): activation never rebinds a recorded
   completion. The database triggers enforce the same guard.
5. Retire the predecessor with ``effective_to = effective_from``, activate the draft, audit
   ``rule.retired`` and ``rule.activated``, commit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError, map_integrity_error
from app.core.security import Actor, Permission, ensure_permitted
from app.core.timeutil import format_utc, to_utc, utc_now
from app.database.session import WriteSessionLocal
from app.models import CompletionRecord, CredentialRule
from app.models.enums import RuleStatus
from app.services import audit

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class ActivationOutcome:
    rule: CredentialRule
    retired_rule_id: str | None


def activate_rule(
    *,
    actor: Actor,
    rule_id: str,
    effective_from: datetime | None = None,
    write_factory: SessionFactory | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> ActivationOutcome:
    ensure_permitted(actor, Permission.RULE_ACTIVATE)
    factory = write_factory or WriteSessionLocal
    with factory() as session:
        try:
            rule = session.get(CredentialRule, rule_id)  # BEGIN IMMEDIATE here
            if rule is None:
                raise DomainError("rule_not_found")
            if rule.status != RuleStatus.DRAFT.value:
                raise DomainError("rule_not_draft")
            if rule.min_score is None and rule.min_attendance_pct is None:
                raise DomainError("rule_has_no_criteria")

            now = clock()
            effective = to_utc(effective_from) if effective_from is not None else now
            if effective > now:
                raise DomainError("rule_effective_date_invalid", "effective_from must not be in the future.")
            current = session.execute(select(CredentialRule).where(
                CredentialRule.course_id == rule.course_id,
                CredentialRule.status == RuleStatus.ACTIVE.value,
            )).scalar_one_or_none()
            if current is not None and effective <= current.effective_from:
                raise DomainError(
                    "rule_effective_date_invalid",
                    "effective_from must be later than the active version's effective_from.",
                    details={"active_rule_id": current.rule_id,
                             "active_effective_from": format_utc(current.effective_from)},
                )
            conflicts = session.execute(select(CompletionRecord.completion_id).where(
                CompletionRecord.course_id == rule.course_id,
                CompletionRecord.completed_at >= effective,
            ).order_by(CompletionRecord.completion_id)).scalars().all()
            if conflicts:
                raise DomainError("rule_effective_date_conflict",
                                  details={"conflicting_completion_ids": list(conflicts)})

            if current is not None:
                current.status = RuleStatus.RETIRED.value
                current.effective_to = effective
                session.flush()  # free the single active slot before activating the draft
                audit.record_event(
                    session, event_type="rule.retired", entity_type="rule", entity_id=current.rule_id,
                    actor_id=actor.actor_id, actor_role=actor.role.value,
                    details={"effective_to": format_utc(effective), "superseded_by": rule.rule_id},
                    occurred_at=now,
                )
            rule.status = RuleStatus.ACTIVE.value
            rule.effective_from = effective
            rule.activated_at = now
            session.flush()
            audit.record_event(
                session, event_type="rule.activated", entity_type="rule", entity_id=rule.rule_id,
                actor_id=actor.actor_id, actor_role=actor.role.value,
                details={"effective_from": format_utc(effective), "activated_at": format_utc(now),
                         "retired_rule_id": current.rule_id if current else None},
                occurred_at=now,
            )
            session.commit()
            return ActivationOutcome(rule=rule, retired_rule_id=current.rule_id if current else None)
        except IntegrityError as exc:  # database guard (triggers / checks) has the final word
            session.rollback()
            raise map_integrity_error(exc) from None
        except DomainError:
            session.rollback()
            raise
