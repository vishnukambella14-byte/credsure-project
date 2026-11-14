"""Verification (BR-11–BR-15) and revocation (BR-16, D1) services."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Engine

from app.core.errors import DomainError
from app.core.security import ANONYMOUS_VERIFIER, Actor, Role
from app.models import CompletionRecord, Course, CredentialRule
from app.services import audit, issuance, revocation, verification
from app.services.crypto import generate_private_key, sign
from tests.support import completion_row, course_row, execute, insert, rule_row, scalar, ts

AA = Actor("AA-1", Role.ACADEMIC_ADMIN)
NOW = ts("2026-11-14 12:00:00")
VERIFIER = verification.Verifier("Jordan Lee", "Acme Corp")


@pytest.fixture
def issued(issuable: Engine, factories, key_store):  # noqa: ANN001, ANN201
    return issuance.issue_credential(actor=AA, learner_id="LRN-0000001", course_id="CRS-00001",
                                     write_factory=factories.write, read_factory=factories.read,
                                     key_store=key_store, clock=lambda: NOW)


def verify(factories, credential_id, *, actor=ANONYMOUS_VERIFIER, at=NOW, **kw):  # noqa: ANN001, ANN201
    return verification.verify_credential(actor=actor, credential_id=credential_id, verifier=VERIFIER,
                                          write_factory=factories.write, clock=lambda: at, **kw)


def revoke(factories, credential_id, *, actor=AA, at=ts("2026-11-15 09:00:00"), **kw):  # noqa: ANN001, ANN201
    kw.setdefault("reason_code", "issued_in_error")
    return revocation.revoke_credential(actor=actor, credential_id=credential_id,
                                        write_factory=factories.write, clock=lambda: at, **kw)


# ------------------------------------------------------------------ verification outcomes --------
def test_not_found_is_recorded(issuable: Engine, factories) -> None:  # noqa: ANN001
    outcome = verify(factories, "CRED-UNKNOWN0000X")
    assert (outcome.result, outcome.found, outcome.credential_id, outcome.summary) == ("not_found", False, None, None)
    assert outcome.message == "No credential with this ID was issued by CredSure."
    row = scalar(issuable, "SELECT submitted_credential_id || '|' || ifnull(credential_id,'NULL') || '|' || result "
                           "FROM verification_requests")
    assert row == "CRED-UNKNOWN0000X|NULL|not_found"
    assert scalar(issuable, "SELECT event_type FROM audit_events ORDER BY event_id DESC LIMIT 1") == "verification.performed"


def test_valid_by_lookup_and_by_presented_payload(issued, factories) -> None:  # noqa: ANN001
    lookup = verify(factories, issued.credential.credential_id.lower())
    assert lookup.result == "valid" and lookup.message == "This credential is authentic and active."
    assert lookup.summary == {"credential_type": "certificate", "course_title": "Data Foundations",
                              "learner_name": "Asha Rao", "issuer_name": "CredSure Institute",
                              "issued_at": "2026-11-14T12:00:00Z", "expires_at": None}
    presented = verify(factories, issued.credential.credential_id, payload=dict(issued.payload),
                       signature=issued.credential.signature)
    assert presented.result == "valid" and presented.hash_match and presented.signature_valid


def test_tampered_presented_payload(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    outcome = verify(factories, issued.credential.credential_id, payload={**issued.payload, "score": "99.00"})
    assert (outcome.result, outcome.hash_match, outcome.summary) == ("tampered", False, None)
    assert outcome.message == "The credential data has been altered."
    stored_hash = scalar(issuable, "SELECT presented_payload_hash FROM verification_requests")
    assert stored_hash is not None and stored_hash != issued.credential.payload_hash


def test_float_score_cannot_verify(issued, factories) -> None:  # noqa: ANN001
    outcome = verify(factories, issued.credential.credential_id, payload={**issued.payload, "score": 82.5})
    assert outcome.result == "tampered"


def test_tampered_stored_payload_is_detected_on_lookup(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    execute(issuable, "DROP TRIGGER trg_credentials_immutable")  # simulate tampering behind the triggers
    forged = issued.credential.payload_json.replace('"82.50"', '"99.00"')
    execute(issuable, "UPDATE credentials SET payload_json = :p", {"p": forged})
    assert verify(factories, issued.credential.credential_id).result == "tampered"


def test_invalid_signature(issued, factories) -> None:  # noqa: ANN001
    forged_signature = sign(generate_private_key(), issued.credential.payload_json.encode("utf-8"))
    outcome = verify(factories, issued.credential.credential_id, payload=dict(issued.payload),
                     signature=forged_signature)
    assert (outcome.result, outcome.hash_match, outcome.signature_valid) == ("invalid_signature", True, False)
    assert outcome.summary is None


def test_expired(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    insert(issuable, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(issuable, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002", validity_days=30))
    insert(issuable, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
    cred = issuance.issue_credential(actor=AA, learner_id="LRN-0000001", course_id="CRS-00002",
                                     write_factory=factories.write, read_factory=factories.read,
                                     key_store=key_store, clock=lambda: NOW).credential
    assert verify(factories, cred.credential_id, at=ts("2026-12-14 12:00:00")).result == "valid"  # boundary
    outcome = verify(factories, cred.credential_id, at=ts("2026-12-14 12:00:01"))
    assert outcome.result == "expired" and outcome.message == "This credential expired on 2026-12-14."
    assert outcome.summary is not None


def test_precedence_when_several_conditions_apply(issued, factories) -> None:  # noqa: ANN001
    revoke(factories, issued.credential.credential_id)
    cid = issued.credential.credential_id
    later = ts("2027-01-01 00:00:00")
    tampered = verify(factories, cid, at=later, payload={**issued.payload, "score": "99.00"})
    assert tampered.result == "tampered" and tampered.is_revoked
    bad_sig = verify(factories, cid, at=later, payload=dict(issued.payload),
                     signature=sign(generate_private_key(), b"x"))
    assert bad_sig.result == "invalid_signature" and bad_sig.is_revoked
    assert verify(factories, cid, at=later).result == "revoked"


def test_revoked_wins_over_expired(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    insert(issuable, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(issuable, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002", validity_days=1))
    insert(issuable, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
    cred = issuance.issue_credential(actor=AA, learner_id="LRN-0000001", course_id="CRS-00002",
                                     write_factory=factories.write, read_factory=factories.read,
                                     key_store=key_store, clock=lambda: NOW).credential
    revoke(factories, cred.credential_id)
    outcome = verify(factories, cred.credential_id, at=ts("2027-01-01 00:00:00"))
    assert (outcome.result, outcome.is_revoked, outcome.is_expired) == ("revoked", True, True)


def test_revocations_table_beats_cached_status(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    revoke(factories, issued.credential.credential_id)
    execute(issuable, "DROP TRIGGER trg_credentials_status_transition")  # simulate a stale cache
    execute(issuable, "UPDATE credentials SET status='active', revoked_at=NULL")
    assert verify(factories, issued.credential.credential_id, at=ts("2027-01-01 00:00:00")).result == "revoked"


def test_retired_issuer_credentials_still_verify(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    execute(issuable, "UPDATE issuers SET status='retired', valid_to='2026-11-14 13:00:00'")
    assert verify(factories, issued.credential.credential_id).result == "valid"


def test_persistence_and_channel(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    verify(factories, issued.credential.credential_id, purpose="employment")
    verify(factories, issued.credential.credential_id, actor=Actor("SUP-1", Role.SUPPORT_STAFF))
    with issuable.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT verification_id, channel, purpose, verifier_name, hash_match, signature_valid, "
            "is_revoked, is_expired, result FROM verification_requests ORDER BY verification_id").all()
    assert [tuple(r) for r in rows] == [
        ("VER-0000001", "api", "employment", "Jordan Lee", 1, 1, 0, 0, "valid"),
        ("VER-0000002", "portal", None, "Jordan Lee", 1, 1, 0, 0, "valid"),
    ]
    assert scalar(issuable, "SELECT count(*) FROM audit_events WHERE event_type='verification.performed'") == 2


# ------------------------------------------------------------------ revocation ---------------------
def test_successful_revocation_and_immediate_effect(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    outcome = revoke(factories, issued.credential.credential_id, reason_code="academic_misconduct",
                     reason_text="Panel decision", requested_by="SUP-3")
    assert (outcome.credential.status, outcome.revocation.revocation_id) == ("revoked", "REV-000001")
    assert outcome.credential.revoked_at == outcome.revocation.revoked_at == ts("2026-11-15 09:00:00")
    assert scalar(issuable, "SELECT status FROM credentials") == "revoked"
    assert scalar(issuable, "SELECT event_type FROM audit_events ORDER BY event_id DESC LIMIT 1") == "credential.revoked"
    result = verify(factories, issued.credential.credential_id, at=ts("2026-11-15 09:00:00"))
    assert result.result == "revoked" and result.message == "This credential was revoked on 2026-11-15."
    assert "academic_misconduct" not in json.dumps(result.summary)  # reason not disclosed


def test_already_revoked_is_rejected_and_audited(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    revoke(factories, issued.credential.credential_id)
    with pytest.raises(DomainError) as caught:
        revoke(factories, issued.credential.credential_id)
    assert (caught.value.code, caught.value.status_code) == ("credential_already_revoked", 409)
    assert scalar(issuable, "SELECT event_type || '|' || reason_code FROM audit_events ORDER BY event_id DESC LIMIT 1") == (
        "credential.revoke_rejected|credential_already_revoked")


@pytest.mark.parametrize(("code", "text_value"), [(None, None), ("changed_mind", None), ("other", None)])
def test_reason_is_required(issued, factories, issuable: Engine, code, text_value) -> None:  # noqa: ANN001
    with pytest.raises(DomainError) as caught:
        revoke(factories, issued.credential.credential_id, reason_code=code, reason_text=text_value)
    assert (caught.value.code, caught.value.status_code) == ("revocation_reason_required", 422)
    assert scalar(issuable, "SELECT status FROM credentials") == "active"
    assert scalar(issuable, "SELECT reason_code FROM audit_events ORDER BY event_id DESC LIMIT 1") == "revocation_reason_required"


def test_unknown_credential_is_404_without_audit(issuable: Engine, factories) -> None:  # noqa: ANN001
    with pytest.raises(DomainError) as caught:
        revoke(factories, "CRED-UNKNOWN0000X")
    assert (caught.value.code, caught.value.status_code) == ("credential_not_found", 404)
    assert scalar(issuable, "SELECT count(*) FROM audit_events") == 0


def test_permission_is_checked(issued, factories) -> None:  # noqa: ANN001
    with pytest.raises(DomainError) as caught:
        revoke(factories, issued.credential.credential_id, actor=Actor("PA-1", Role.PLATFORM_ADMIN))
    assert caught.value.code == "permission_denied"


def test_failed_revocation_rolls_back_atomically(issued, factories, issuable: Engine, monkeypatch) -> None:  # noqa: ANN001
    real_record = audit.record_event

    def failing(session, **event):  # noqa: ANN001, ANN202
        if event["event_type"] == "credential.revoked":
            raise DomainError("database_busy")
        return real_record(session, **event)

    monkeypatch.setattr(audit, "record_event", failing)
    with pytest.raises(DomainError):
        revoke(factories, issued.credential.credential_id)
    assert scalar(issuable, "SELECT count(*) FROM revocations") == 0
    assert scalar(issuable, "SELECT status FROM credentials") == "active"


def test_reinstatement_is_impossible(issued, factories, issuable: Engine) -> None:  # noqa: ANN001
    from sqlalchemy.exc import IntegrityError

    revoke(factories, issued.credential.credential_id)
    with pytest.raises(IntegrityError, match="only active -> revoked"):
        execute(issuable, "UPDATE credentials SET status='active', revoked_at=NULL")
    assert not hasattr(revocation, "reinstate_credential")
