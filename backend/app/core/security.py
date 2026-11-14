"""Simulated actors and role-based permissions (ADR-010, PRD §3, api_contract §2).

There is no authentication: every request states its actor in ``X-Actor-Id`` / ``X-Actor-Role``
(an accepted local-demo risk, AS-01). This module provides:

* ``Role``, ``Actor`` and the permission matrix ``PERMISSIONS`` (PRD §3 matrix, with the role
  columns of api_contract §3 where the API is more specific).
* Pure checks: ``role_scope``, ``is_permitted``, ``ensure_permitted`` (raise 403).
* FastAPI dependencies for later routes: ``get_actor`` (protected), ``get_public_actor``
  (public endpoints default to ``external_verifier`` / ``anonymous``) and
  ``require_permission(permission)``, which audits denials as ``access.denied``.

Scopes: ``OWN`` = a learner's own records (``X-Actor-Id`` must equal the ``learner_id``);
``COURSE`` = an instructor's courses, enforced only when ``lead_instructor_id`` is set (AS-10).
Services apply scopes once they know the resource (``ensure_permitted(..., learner_id=...)``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Header, Request

from app.core.errors import DomainError

logger = logging.getLogger(__name__)


class Role(str, Enum):
    LEARNER = "learner"
    INSTRUCTOR = "instructor"
    ACADEMIC_ADMIN = "academic_admin"
    SUPPORT_STAFF = "support_staff"
    PLATFORM_ADMIN = "platform_admin"
    EXTERNAL_VERIFIER = "external_verifier"


class Scope(str, Enum):
    ANY = "any"
    OWN = "own"  # learner: only records whose learner_id equals the actor ID
    COURSE = "course"  # instructor: only courses where they are lead instructor (if one is set)


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: Role


ANONYMOUS_VERIFIER = Actor(actor_id="anonymous", role=Role.EXTERNAL_VERIFIER)


class Permission(str, Enum):
    ISSUER_CREATE = "issuer.create"
    ISSUER_READ = "issuer.read"
    ISSUER_RETIRE = "issuer.retire"
    ISSUER_PUBLIC_KEY_READ = "issuer.public_key.read"
    LEARNER_CREATE = "learner.create"
    LEARNER_READ = "learner.read"
    LEARNER_UPDATE = "learner.update"
    COURSE_CREATE = "course.create"
    COURSE_READ = "course.read"
    COURSE_UPDATE = "course.update"
    RULE_CREATE = "rule.create"
    RULE_READ = "rule.read"
    RULE_UPDATE_DRAFT = "rule.update_draft"
    RULE_ACTIVATE = "rule.activate"
    COMPLETION_CREATE = "completion.create"
    COMPLETION_READ = "completion.read"
    COMPLETION_UPDATE = "completion.update"
    ELIGIBILITY_EVALUATE = "eligibility.evaluate"
    CREDENTIAL_ISSUE = "credential.issue"
    CREDENTIAL_READ = "credential.read"
    CREDENTIAL_SHARE = "credential.share"
    CREDENTIAL_REVOKE = "credential.revoke"
    REVOCATION_READ = "revocation.read"
    VERIFY = "verification.perform"
    VERIFICATION_HISTORY_READ = "verification.history.read"
    AUDIT_READ = "audit.read"
    AUDIT_CHAIN_VERIFY = "audit.chain.verify"
    METRICS_READ = "metrics.read"
    ANOMALY_RUN = "anomaly.run"
    ANOMALY_READ = "anomaly.read"


L, I, AA, SS, PA, EV = (
    Role.LEARNER, Role.INSTRUCTOR, Role.ACADEMIC_ADMIN, Role.SUPPORT_STAFF,
    Role.PLATFORM_ADMIN, Role.EXTERNAL_VERIFIER,
)
ANY, OWN, COURSE = Scope.ANY, Scope.OWN, Scope.COURSE

# PRD §3 permission matrix (C/R/U/X), refined by api_contract §3 role columns.
PERMISSIONS: dict[Permission, dict[Role, Scope]] = {
    Permission.ISSUER_CREATE: {PA: ANY},
    Permission.ISSUER_READ: {AA: ANY, SS: ANY, PA: ANY},
    Permission.ISSUER_RETIRE: {PA: ANY},
    Permission.ISSUER_PUBLIC_KEY_READ: {L: ANY, I: ANY, AA: ANY, SS: ANY, PA: ANY, EV: ANY},  # public
    Permission.LEARNER_CREATE: {AA: ANY},
    Permission.LEARNER_READ: {L: OWN, I: ANY, AA: ANY, SS: ANY, PA: ANY},
    Permission.LEARNER_UPDATE: {AA: ANY},
    Permission.COURSE_CREATE: {AA: ANY},
    Permission.COURSE_READ: {I: ANY, AA: ANY, SS: ANY, PA: ANY},
    Permission.COURSE_UPDATE: {AA: ANY},
    Permission.RULE_CREATE: {AA: ANY},
    Permission.RULE_READ: {I: ANY, AA: ANY, SS: ANY, PA: ANY},
    Permission.RULE_UPDATE_DRAFT: {AA: ANY},
    Permission.RULE_ACTIVATE: {AA: ANY},
    Permission.COMPLETION_CREATE: {I: COURSE, AA: ANY},
    Permission.COMPLETION_READ: {L: OWN, I: COURSE, AA: ANY, SS: ANY, PA: ANY},
    Permission.COMPLETION_UPDATE: {I: COURSE, AA: ANY},
    Permission.ELIGIBILITY_EVALUATE: {L: OWN, I: COURSE, AA: ANY, SS: ANY, PA: ANY},
    Permission.CREDENTIAL_ISSUE: {AA: ANY},
    Permission.CREDENTIAL_READ: {L: OWN, I: COURSE, AA: ANY, SS: ANY, PA: ANY},
    Permission.CREDENTIAL_SHARE: {L: OWN, AA: ANY, SS: ANY},
    Permission.CREDENTIAL_REVOKE: {AA: ANY},
    Permission.REVOCATION_READ: {AA: ANY, SS: ANY, PA: ANY},
    Permission.VERIFY: {L: ANY, I: ANY, AA: ANY, SS: ANY, PA: ANY, EV: ANY},  # public
    Permission.VERIFICATION_HISTORY_READ: {L: OWN, AA: ANY, SS: ANY, PA: ANY},
    Permission.AUDIT_READ: {AA: ANY, SS: ANY, PA: ANY},
    Permission.AUDIT_CHAIN_VERIFY: {PA: ANY},
    Permission.METRICS_READ: {AA: ANY, SS: ANY, PA: ANY},
    Permission.ANOMALY_RUN: {PA: ANY},
    Permission.ANOMALY_READ: {AA: ANY, PA: ANY},
}


# --------------------------------------------------------------- pure checks ------------------------
def parse_actor(actor_id: str | None, role: str | None) -> Actor:
    """Actor from header values, or ``401 actor_required`` if missing or the role is unknown."""
    actor_id = (actor_id or "").strip()
    role = (role or "").strip()
    try:
        parsed_role = Role(role)
    except ValueError:
        parsed_role = None
    if not actor_id or parsed_role is None:
        raise DomainError("actor_required")
    return Actor(actor_id=actor_id, role=parsed_role)


def role_scope(actor: Actor, permission: Permission) -> Scope | None:
    """The scope the actor's role has for ``permission``, or None if the role lacks it."""
    return PERMISSIONS[permission].get(actor.role)


def is_permitted(
    actor: Actor,
    permission: Permission,
    *,
    learner_id: str | None = None,
    lead_instructor_id: str | None = None,
) -> bool:
    """Role check plus scope check when the resource is known.

    * ``OWN`` requires ``learner_id`` and ``learner_id == actor.actor_id``.
    * ``COURSE`` passes when the course has no lead instructor (AS-10) or it is the actor.
    """
    scope = role_scope(actor, permission)
    if scope is None:
        return False
    if scope is Scope.OWN:
        return learner_id is not None and learner_id == actor.actor_id
    if scope is Scope.COURSE:
        return lead_instructor_id is None or lead_instructor_id == actor.actor_id
    return True


def ensure_permitted(
    actor: Actor,
    permission: Permission,
    *,
    learner_id: str | None = None,
    lead_instructor_id: str | None = None,
) -> None:
    """Raise ``403`` if not permitted: ``course_scope_denied`` for an instructor outside their
    course, ``permission_denied`` otherwise."""
    if is_permitted(actor, permission, learner_id=learner_id, lead_instructor_id=lead_instructor_id):
        return
    if role_scope(actor, permission) is Scope.COURSE:
        raise DomainError("course_scope_denied", details={"permission": permission.value})
    raise DomainError("permission_denied", details={"permission": permission.value})


# --------------------------------------------------------------- FastAPI dependencies ---------------
DenialRecorder = Callable[[Actor, Permission, str], None]


def _record_denial_in_audit(actor: Actor, permission: Permission, path: str) -> None:
    """Default recorder: ``access.denied`` audit event in its own write transaction."""
    from app.services.audit import record_event_in_new_transaction  # avoid import cycle

    record_event_in_new_transaction(
        event_type="access.denied",
        entity_type="audit",
        entity_id=permission.value,
        actor_id=actor.actor_id,
        actor_role=actor.role.value,
        outcome="rejected",
        reason_code="permission_denied",
        details={"permission": permission.value, "path": path},
    )


def get_denial_recorder() -> DenialRecorder:
    """Dependency so routes and tests can replace how denials are recorded."""
    return _record_denial_in_audit


def get_actor(
    x_actor_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
) -> Actor:
    """Actor for protected endpoints (``401 actor_required`` without valid headers)."""
    return parse_actor(x_actor_id, x_actor_role)


def get_public_actor(
    x_actor_id: str | None = Header(default=None),
    x_actor_role: str | None = Header(default=None),
) -> Actor:
    """Actor for public endpoints: headers optional, default ``external_verifier`` / ``anonymous``."""
    if not (x_actor_id or x_actor_role):
        return ANONYMOUS_VERIFIER
    return parse_actor(x_actor_id, x_actor_role)


def require_permission(permission: Permission) -> Callable[..., Actor]:
    """Route dependency: resolve the actor and check the role (scope is checked by the service).

    A denial is recorded as ``access.denied`` before ``403 permission_denied`` is raised.
    """
    public = all(role in PERMISSIONS[permission] for role in Role)
    actor_dependency = get_public_actor if public else get_actor

    def dependency(
        request: Request,
        actor: Actor = Depends(actor_dependency),
        record_denial: DenialRecorder = Depends(get_denial_recorder),
    ) -> Actor:
        if role_scope(actor, permission) is None:
            try:
                record_denial(actor, permission, request.url.path)
            except Exception:  # the denial itself must still be returned
                logger.exception("Could not audit access.denied for %s", permission.value)
            raise DomainError("permission_denied", details={"permission": permission.value})
        return actor

    return dependency
