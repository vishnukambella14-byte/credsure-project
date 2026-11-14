"""Issuance service (FR-09–FR-11, BR-02/03/08/09/26, D7, D1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.core.security import Actor, Role
from app.models import CompletionRecord, Course, CredentialRule
from app.services import audit, issuance
from app.services.crypto import KeyStore, sha256_hex, verify_signature
from app.services.eligibility import evaluate as real_evaluate
from tests.support import completion_row, course_row, execute, insert, read_sessions, rule_row, scalar, ts

AA = Actor("AA-1", Role.ACADEMIC_ADMIN)
NOW = ts("2026-11-14 12:00:00")
PAYLOAD_KEYS = {
    "schema_version", "credential_id", "credential_type", "learner_id", "learner_name", "course_id",
    "course_code", "course_title", "rule_id", "rule_version", "completion_id", "completed_at", "score",
    "issuer_id", "issuer_name", "signing_key_id", "issued_at", "expires_at",
}


def issue(factories, key_store, *, learner="LRN-0000001", course="CRS-00001", clock=NOW, **kw):  # noqa: ANN001, ANN201
    return issuance.issue_credential(
        actor=AA, learner_id=learner, course_id=course, write_factory=factories.write,
        read_factory=factories.read, key_store=key_store, clock=lambda: clock, **kw)


def terminal_events(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT event_type, outcome, reason_code, attempt_ref, details_json FROM audit_events "
            "WHERE event_type IN ('credential.issued','credential.issue_rejected') ORDER BY event_id")).all()


def chain_ok(engine: Engine) -> bool:
    with read_sessions(engine)() as session:
        return audit.verify_chain(session).valid


# ------------------------------------------------------------------ success ----------------------
def test_successful_issuance(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    issued = issue(factories, key_store)
    c = issued.credential
    stored = scalar(issuable, "SELECT payload_json FROM credentials WHERE credential_id = :c", {"c": c.credential_id})
    assert stored == c.payload_json and json.loads(stored) == issued.payload
    assert set(issued.payload) == PAYLOAD_KEYS
    assert issued.payload["score"] == "82.50"
    assert issued.payload["issued_at"] == "2026-11-14T12:00:00Z" and issued.payload["expires_at"] is None
    assert issued.payload["completed_at"] == "2026-03-01T10:00:00Z"
    assert issued.payload["learner_name"] == "Asha Rao" and issued.payload["rule_version"] == 1
    assert c.payload_hash == sha256_hex(stored.encode("utf-8"))
    public_key = scalar(issuable, "SELECT public_key FROM issuers WHERE issuer_id='ISS-001'")
    assert verify_signature(public_key, stored.encode("utf-8"), c.signature)
    assert (c.status, c.issued_by, c.record_source, c.rule_id, c.completion_id) == (
        "active", "AA-1", "platform", "RUL-00001-V1", "CMP-0000001")
    assert issued.attempt_ref.startswith("ATT-") and len(issued.attempt_ref) == 16

    events = scalar(issuable, "SELECT group_concat(e, ',') FROM (SELECT event_type || ':' || attempt_ref AS e "
                              "FROM audit_events ORDER BY event_id)")
    assert events == f"eligibility.evaluated:{issued.attempt_ref},credential.issued:{issued.attempt_ref}"
    assert chain_ok(issuable)


def test_expiry_from_rule_validity_days(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    insert(issuable, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(issuable, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002", validity_days=365))
    insert(issuable, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
    issued = issue(factories, key_store, course="CRS-00002")
    assert issued.payload["expires_at"] == "2027-11-14T12:00:00Z"
    assert issued.credential.expires_at == ts("2027-11-14 12:00:00")


# ------------------------------------------------------------------ every BR-04 rejection -------
def _setup(engine: Engine, case: str) -> dict:
    """Arrange one failing BR-04 reason; returns overrides for ``issue``."""
    if case == "learner_not_found":
        return {"learner": "LRN-0009999"}
    if case == "learner_inactive":
        execute(engine, "UPDATE learners SET status='inactive' WHERE learner_id='LRN-0000001'")
    if case == "course_not_found":
        return {"course": "CRS-09999"}
    if case == "course_inactive":
        execute(engine, "UPDATE courses SET status='archived'")
    if case == "no_completed_record":
        return {"learner": "LRN-0000002"}
    if case == "completion_in_future":
        return {"clock": ts("2026-02-01 00:00:00")}
    if case == "no_applicable_rule":
        insert(engine, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
        insert(engine, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002",
                                                effective_from=ts("2026-06-01 00:00:00")))
        insert(engine, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
        return {"course": "CRS-00002"}
    if case == "score_below_minimum":
        insert(engine, CompletionRecord, completion_row(completion_id="CMP-0000002", learner_id="LRN-0000002", score=60.0))
        return {"learner": "LRN-0000002"}
    if case == "attendance_below_minimum":
        insert(engine, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
        insert(engine, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002",
                                                min_score=None, min_attendance_pct=80.0))
        insert(engine, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002",
                                                        attendance_pct=50.0))
        return {"course": "CRS-00002"}
    if case == "issuer_inactive":
        execute(engine, "UPDATE issuers SET status='retired', valid_to='2026-06-01 00:00:00'")
    return {}


REJECTIONS = [
    ("learner_not_found", "not_eligible", 422), ("learner_inactive", "not_eligible", 422),
    ("course_not_found", "not_eligible", 422), ("course_inactive", "not_eligible", 422),
    ("no_completed_record", "not_eligible", 422), ("completion_in_future", "not_eligible", 422),
    ("no_applicable_rule", "not_eligible", 422), ("score_below_minimum", "not_eligible", 422),
    ("attendance_below_minimum", "not_eligible", 422), ("issuer_inactive", "not_eligible", 422),
]


@pytest.mark.parametrize(("reason", "code", "status"), REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_each_ineligibility_is_rejected_and_audited(issuable: Engine, factories, key_store,  # noqa: ANN001
                                                   reason: str, code: str, status: int) -> None:
    overrides = _setup(issuable, reason)
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store, **overrides)
    error = caught.value
    assert (error.code, error.status_code) == (code, status)
    assert reason in error.details["reasons"] and error.details["attempt_ref"].startswith("ATT-")
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 0
    ((event_type, outcome, reason_code, attempt_ref, details),) = terminal_events(issuable)
    assert (event_type, outcome, reason_code, attempt_ref) == (
        "credential.issue_rejected", "rejected", code, error.details["attempt_ref"])
    assert reason in json.loads(details)["reasons"]
    assert chain_ok(issuable)


def test_already_issued_is_409_credential_already_issued(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    issue(factories, key_store)
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store)
    assert (caught.value.code, caught.value.status_code) == ("credential_already_issued", 409)
    assert caught.value.details["reasons"] == ["already_issued"]


def test_missing_signing_key_is_503_and_audited_as_error(issuable: Engine, factories, tmp_path: Path) -> None:  # noqa: ANN001
    with pytest.raises(DomainError) as caught:
        issue(factories, KeyStore(tmp_path / "empty", tmp_path / "empty-s"))
    assert (caught.value.code, caught.value.status_code) == ("signing_key_unavailable", 503)
    ((_, outcome, reason_code, _, _),) = terminal_events(issuable)
    assert (outcome, reason_code) == ("error", "signing_key_unavailable")
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 0


# ------------------------------------------------------------------ D7 order, races, attempt refs -
def test_duplicate_credential_id_is_checked_before_eligibility(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    first = issue(factories, key_store)
    for variant in (first.credential.credential_id, first.credential.credential_id.lower()):
        with pytest.raises(DomainError) as caught:  # LRN-0000002 is not eligible at all
            issue(factories, key_store, learner="LRN-0000002", credential_id=variant)
        assert (caught.value.code, caught.value.status_code) == ("duplicate_credential_id", 409)
        assert "reasons" not in caught.value.details
    rejected = [e for e in terminal_events(issuable) if e[0] == "credential.issue_rejected"]
    assert [e[2] for e in rejected] == ["duplicate_credential_id", "duplicate_credential_id"]
    stored = scalar(issuable, "SELECT payload_hash FROM credentials")
    assert stored == first.credential.payload_hash  # never overwritten


def test_supplied_credential_id_is_used_and_validated(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    issued = issue(factories, key_store, credential_id="cred-7k2m9qxr4t1b")
    assert issued.credential.credential_id == "CRED-7K2M9QXR4T1B"
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store, learner="LRN-0000002", credential_id="CRED-NOT-VALID")
    assert caught.value.code == "validation_error"
    assert terminal_events(issuable)[-1][2] == "validation_error"


def test_database_primary_key_is_the_final_authority(issuable: Engine, factories, key_store, monkeypatch) -> None:  # noqa: ANN001
    first = issue(factories, key_store)
    insert(issuable, CompletionRecord, completion_row(completion_id="CMP-0000002", learner_id="LRN-0000002"))
    monkeypatch.setattr(issuance.ids, "new_credential_id", lambda: first.credential.credential_id)
    with pytest.raises(DomainError) as caught:  # generated ID collides: only the PK can catch it
        issue(factories, key_store, learner="LRN-0000002")
    assert caught.value.code == "duplicate_credential_id"
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 1
    assert terminal_events(issuable)[-1][:3] == ("credential.issue_rejected", "rejected", "duplicate_credential_id")


def test_active_credential_race_is_caught_by_partial_unique_index(issuable: Engine, factories, key_store, monkeypatch) -> None:  # noqa: ANN001
    issue(factories, key_store)

    def stale_evaluation(session, learner_id, course_id, **kw):  # noqa: ANN001, ANN202
        result = real_evaluate(session, learner_id, course_id, **kw)  # sees the active credential...
        return result.__class__(**{**result.__dict__, "eligible": True, "reasons": ()})  # ...but races

    monkeypatch.setattr(issuance, "evaluate", stale_evaluation)
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store)
    assert (caught.value.code, caught.value.status_code) == ("credential_already_issued", 409)
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 1
    assert terminal_events(issuable)[-1][2] == "credential_already_issued"


def test_attempt_refs_are_unique_and_reuse_is_rejected_without_audit(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    first = issue(factories, key_store)
    with pytest.raises(DomainError):
        issue(factories, key_store, learner="LRN-0000002")
    refs = [e[3] for e in terminal_events(issuable)]
    assert len(refs) == len(set(refs)) == 2 and refs[0] == first.attempt_ref

    before = scalar(issuable, "SELECT count(*) FROM audit_events")
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store, learner="LRN-0000002", attempt_ref=first.attempt_ref)
    assert (caught.value.code, caught.value.status_code) == ("duplicate_attempt_ref", 409)
    assert caught.value.details == {"attempt_ref": first.attempt_ref}
    assert scalar(issuable, "SELECT count(*) FROM audit_events") == before  # not a new attempt


def test_supplied_attempt_ref_is_carried_through(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    issued = issue(factories, key_store, attempt_ref="ATT-000123")
    assert issued.attempt_ref == "ATT-000123"
    assert scalar(issuable, "SELECT count(*) FROM audit_events WHERE attempt_ref='ATT-000123'") == 2


def test_failure_after_insert_rolls_back_and_still_audits(issuable: Engine, factories, key_store, monkeypatch) -> None:  # noqa: ANN001
    real_record = audit.record_event

    def failing_record(session, **event):  # noqa: ANN001, ANN202
        if event["event_type"] == "credential.issued":
            raise RuntimeError("disk full")
        return real_record(session, **event)

    monkeypatch.setattr(audit, "record_event", failing_record)
    with pytest.raises(DomainError) as caught:
        issue(factories, key_store)
    assert caught.value.code == "internal_server_error"
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 0  # credential rolled back
    ((event_type, outcome, reason_code, attempt_ref, _),) = terminal_events(issuable)
    assert (event_type, outcome, reason_code) == ("credential.issue_rejected", "error", "internal_server_error")
    assert attempt_ref == caught.value.details["attempt_ref"]
    assert chain_ok(issuable)
