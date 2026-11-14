"""Actors, roles and the permission matrix (ADR-010, PRD §3, api_contract §2–§3)."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.errors import DomainError, register_exception_handlers
from app.core.security import (
    ANONYMOUS_VERIFIER,
    PERMISSIONS,
    Actor,
    Permission,
    Role,
    Scope,
    ensure_permitted,
    get_denial_recorder,
    is_permitted,
    parse_actor,
    require_permission,
    role_scope,
)
from app.services import audit
from tests.support import scalar, write_sessions

L, I, AA, SS, PA, EV = (Role.LEARNER, Role.INSTRUCTOR, Role.ACADEMIC_ADMIN, Role.SUPPORT_STAFF,
                        Role.PLATFORM_ADMIN, Role.EXTERNAL_VERIFIER)
A, O, C = Scope.ANY, Scope.OWN, Scope.COURSE

# Transcribed from PRD §3 (permission matrix) with api_contract §3 role columns.
EXPECTED = {
    "issuer.create": {PA: A}, "issuer.read": {AA: A, SS: A, PA: A}, "issuer.retire": {PA: A},
    "issuer.public_key.read": {L: A, I: A, AA: A, SS: A, PA: A, EV: A},
    "learner.create": {AA: A}, "learner.read": {L: O, I: A, AA: A, SS: A, PA: A}, "learner.update": {AA: A},
    "course.create": {AA: A}, "course.read": {I: A, AA: A, SS: A, PA: A}, "course.update": {AA: A},
    "rule.create": {AA: A}, "rule.read": {I: A, AA: A, SS: A, PA: A}, "rule.update_draft": {AA: A},
    "rule.activate": {AA: A},
    "completion.create": {I: C, AA: A}, "completion.read": {L: O, I: C, AA: A, SS: A, PA: A},
    "completion.update": {I: C, AA: A},
    "eligibility.evaluate": {L: O, I: C, AA: A, SS: A, PA: A},
    "credential.issue": {AA: A}, "credential.read": {L: O, I: C, AA: A, SS: A, PA: A},
    "credential.share": {L: O, AA: A, SS: A}, "credential.revoke": {AA: A},
    "revocation.read": {AA: A, SS: A, PA: A},
    "verification.perform": {L: A, I: A, AA: A, SS: A, PA: A, EV: A},
    "verification.history.read": {L: O, AA: A, SS: A, PA: A},
    "audit.read": {AA: A, SS: A, PA: A}, "audit.chain.verify": {PA: A},
    "metrics.read": {AA: A, SS: A, PA: A}, "anomaly.run": {PA: A}, "anomaly.read": {AA: A, PA: A},
}


def test_exactly_six_documented_roles() -> None:
    assert {r.value for r in Role} == {
        "learner", "instructor", "academic_admin", "support_staff", "platform_admin", "external_verifier"}


def test_permission_matrix_matches_prd() -> None:
    assert {p.value: scopes for p, scopes in PERMISSIONS.items()} == EXPECTED


def test_separation_of_duties_and_read_only_roles() -> None:
    write_actions = {"create", "update", "update_draft", "issue", "revoke", "activate", "retire", "run"}
    writes = [p for p in Permission if p.value.rsplit(".", 1)[-1] in write_actions]
    assert len(writes) == 14
    for permission in writes:
        assert SS not in PERMISSIONS[permission], f"support staff must be read-only: {permission}"
        assert EV not in PERMISSIONS[permission]
    for permission in (Permission.CREDENTIAL_ISSUE, Permission.CREDENTIAL_REVOKE):
        assert set(PERMISSIONS[permission]) == {AA}  # platform admin cannot issue or revoke
    assert set(PERMISSIONS[Permission.ISSUER_CREATE]) == {PA}  # academic admin cannot manage keys
    assert {p for p in Permission if EV in PERMISSIONS[p]} == {
        Permission.VERIFY, Permission.ISSUER_PUBLIC_KEY_READ}


def test_parse_actor() -> None:
    assert parse_actor(" LRN-0000042 ", "learner") == Actor("LRN-0000042", L)
    for actor_id, role in [(None, "learner"), ("x", None), ("", "learner"), ("x", "superuser")]:
        with pytest.raises(DomainError) as caught:
            parse_actor(actor_id, role)
        assert (caught.value.code, caught.value.status_code) == ("actor_required", 401)


def test_own_scope_for_learners() -> None:
    learner = Actor("LRN-0000042", L)
    assert role_scope(learner, Permission.CREDENTIAL_READ) is Scope.OWN
    assert is_permitted(learner, Permission.CREDENTIAL_READ, learner_id="LRN-0000042")
    assert not is_permitted(learner, Permission.CREDENTIAL_READ, learner_id="LRN-0000043")
    assert not is_permitted(learner, Permission.CREDENTIAL_READ)  # resource unknown: not granted
    assert not is_permitted(learner, Permission.CREDENTIAL_ISSUE, learner_id="LRN-0000042")


def test_course_scope_for_instructors() -> None:
    instructor = Actor("INS-007", I)
    assert is_permitted(instructor, Permission.COMPLETION_CREATE, lead_instructor_id=None)  # AS-10
    assert is_permitted(instructor, Permission.COMPLETION_CREATE, lead_instructor_id="INS-007")
    assert not is_permitted(instructor, Permission.COMPLETION_CREATE, lead_instructor_id="INS-008")
    with pytest.raises(DomainError) as caught:
        ensure_permitted(instructor, Permission.COMPLETION_CREATE, lead_instructor_id="INS-008")
    assert (caught.value.code, caught.value.status_code) == ("course_scope_denied", 403)
    with pytest.raises(DomainError) as caught:
        ensure_permitted(instructor, Permission.CREDENTIAL_ISSUE)
    assert (caught.value.code, caught.value.status_code) == ("permission_denied", 403)


# ------------------------------------------------------------------ FastAPI dependencies --------
def _app(denials: list) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/issue")
    def issue(actor: Actor = Depends(require_permission(Permission.CREDENTIAL_ISSUE))) -> dict:
        return {"actor_id": actor.actor_id, "role": actor.role.value}

    @app.post("/verify")
    def verify(actor: Actor = Depends(require_permission(Permission.VERIFY))) -> dict:
        return {"actor_id": actor.actor_id, "role": actor.role.value}

    app.dependency_overrides[get_denial_recorder] = lambda: (lambda a, p, path: denials.append((a, p, path)))
    return TestClient(app)


def test_require_permission_dependency() -> None:
    denials: list = []
    client = _app(denials)
    assert client.post("/issue").json()["error"]["code"] == "actor_required"
    assert client.post("/issue").status_code == 401

    denied = client.post("/issue", headers={"X-Actor-Id": "PA-1", "X-Actor-Role": "platform_admin"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "permission_denied"
    assert denials == [(Actor("PA-1", PA), Permission.CREDENTIAL_ISSUE, "/issue")]

    allowed = client.post("/issue", headers={"X-Actor-Id": "AA-1", "X-Actor-Role": "academic_admin"})
    assert allowed.status_code == 200 and allowed.json() == {"actor_id": "AA-1", "role": "academic_admin"}


def test_public_permission_defaults_to_anonymous_verifier() -> None:
    client = _app([])
    assert client.post("/verify").json() == {
        "actor_id": ANONYMOUS_VERIFIER.actor_id, "role": "external_verifier"}
    staff = client.post("/verify", headers={"X-Actor-Id": "SUP-1", "X-Actor-Role": "support_staff"})
    assert staff.json() == {"actor_id": "SUP-1", "role": "support_staff"}


def test_default_denial_recorder_writes_access_denied(migrated_engine: Engine, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(audit, "WriteSessionLocal", write_sessions(migrated_engine))
    get_denial_recorder()(Actor("SUP-1", SS), Permission.CREDENTIAL_REVOKE, "/api/v1/credentials/x/revoke")
    assert scalar(migrated_engine, "SELECT event_type || '|' || reason_code || '|' || actor_role "
                                   "FROM audit_events") == "access.denied|permission_denied|support_staff"
