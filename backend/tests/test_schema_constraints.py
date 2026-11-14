"""Schema constraints of migration 0001: PKs, FKs, composite FKs, CHECKs, UNIQUEs, partial indexes."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    CompletionRecord,
    Course,
    Credential,
    CredentialRule,
    Issuer,
    Learner,
    Revocation,
    VerificationRequest,
)
from tests.support import (
    CRED_ID,
    CRED_ID_2,
    audit_row,
    completion_row,
    course_row,
    credential_row,
    insert,
    issuer_row,
    learner_row,
    revocation_row,
    revoke,
    rule_row,
    scalar,
    seed_core,
    seed_with_credential,
    ts,
    verification_row,
)

# ------------------------------------------------------------------ keys --------------------------


def test_text_primary_keys_are_not_null(migrated_engine: Engine) -> None:
    with pytest.raises(IntegrityError, match="NOT NULL"):
        insert(migrated_engine, Learner, learner_row(learner_id=None))


def test_primary_key_uniqueness(migrated_engine: Engine) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    with pytest.raises(IntegrityError, match="UNIQUE"):
        insert(migrated_engine, Issuer, issuer_row(signing_key_id="ISS-001-K2", public_key="PK2"))


def test_foreign_key_rejects_unknown_parent(migrated_engine: Engine) -> None:
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        insert(migrated_engine, Course, course_row(issuer_id="ISS-404"))


def test_foreign_key_restricts_parent_delete(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with migrated_engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM learners WHERE learner_id = 'LRN-0000001'")


# ------------------------------------------------------------------ composite FKs (D1) --------------


def test_composite_fk_rejects_completion_of_another_learner(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        insert(migrated_engine, Credential, credential_row(learner_id="LRN-0000002"))


def test_composite_fk_rejects_rule_of_another_course(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    insert(migrated_engine, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(migrated_engine, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002"))
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        insert(migrated_engine, Credential, credential_row(rule_id="RUL-00002-V1"))


def test_composite_fk_rejects_signing_key_of_another_issuer(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    insert(
        migrated_engine,
        Issuer,
        issuer_row(issuer_id="ISS-901", signing_key_id="ISS-901-K1", public_key="PK-901",
                   record_source="synthetic_import"),
    )
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        insert(migrated_engine, Credential, credential_row(signing_key_id="ISS-901-K1"))


def test_valid_credential_satisfies_all_composite_fks(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    assert scalar(migrated_engine, "SELECT count(*) FROM credentials") == 1


# ------------------------------------------------------------------ CHECK constraints ----------------

CHECK_CASES = [
    ("learner_id_format", Learner, lambda: learner_row(learner_id="LRN-1abc")),
    ("email_lower", Learner, lambda: learner_row(email="Asha@Example.org")),
    ("learner_status", Learner, lambda: learner_row(status="deleted")),
    ("issuer_id_format", Issuer, lambda: issuer_row(issuer_id="ISS-1", signing_key_id="ISS-1-K1")),
    ("synthetic_range: platform issuer in 9xx", Issuer,
     lambda: issuer_row(issuer_id="ISS-902", signing_key_id="ISS-902-K1")),
    ("synthetic_range: synthetic issuer outside 9xx", Issuer,
     lambda: issuer_row(record_source="synthetic_import")),
    ("signing_key_id_format", Issuer, lambda: issuer_row(signing_key_id="ISS-002-K1")),
    ("retired_valid_to", Issuer, lambda: issuer_row(status="retired")),
    ("key_algorithm", Issuer, lambda: issuer_row(key_algorithm="RSA")),
    ("course_id_format", Course, lambda: course_row(course_id="CRS-1")),
]


@pytest.mark.parametrize(("label", "model", "row"), CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_master_data_checks_reject_invalid_values(migrated_engine: Engine, label, model, row) -> None:  # noqa: ANN001
    if model is Course:
        insert(migrated_engine, Issuer, issuer_row())
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, model, row())


RULE_CASES = [
    ("rule_id_derived", rule_row(rule_id="RUL-00001-V9")),
    ("draft_effective_from", rule_row(status="draft")),  # draft must not have effective_from
    ("active_without_effective_from", rule_row(effective_from=None)),
    ("retired_effective_to", rule_row(status="retired")),
    ("active_open", rule_row(effective_to=ts("2026-06-01 00:00:00"))),
    ("has_criteria", rule_row(min_score=None, min_attendance_pct=None)),
    ("window", rule_row(status="retired", effective_to=ts("2025-01-01 00:00:00"))),
    ("min_score_range", rule_row(min_score=101.0)),
    ("version", rule_row(rule_id="RUL-00001-V0", version=0)),
]


@pytest.mark.parametrize(("label", "row"), RULE_CASES, ids=[c[0] for c in RULE_CASES])
def test_rule_state_checks(migrated_engine: Engine, label: str, row: dict) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    insert(migrated_engine, Course, course_row())
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, CredentialRule, row)


COMPLETION_CASES = [
    ("score_range", completion_row(score=100.5)),
    ("attendance_pct_range", completion_row(attendance_pct=-1.0)),
    ("completed_requires_completed_at", completion_row(completed_at=None)),
    ("recorded_after", completion_row(recorded_at=ts("2026-02-01 00:00:00"))),
    ("attempt_number", completion_row(attempt_number=0)),
]


@pytest.mark.parametrize(("label", "row"), COMPLETION_CASES, ids=[c[0] for c in COMPLETION_CASES])
def test_completion_checks(migrated_engine: Engine, label: str, row: dict) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    insert(migrated_engine, Learner, learner_row())
    insert(migrated_engine, Course, course_row())
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, CompletionRecord, row)


CREDENTIAL_CASES = [
    ("lower-case id", credential_row(credential_id=CRED_ID.lower())),
    ("crockford excludes I/L/O/U", credential_row(credential_id="CRED-ILOU00000000")),
    ("id too short", credential_row(credential_id="CRED-7K2M9QXR4T1")),
    ("payload_hash upper-case hex", credential_row(payload_hash="A" * 64)),
    ("payload_hash length", credential_row(payload_hash="a" * 63)),
    ("signature_length", credential_row(signature="S" * 87)),
    ("revoked without revoked_at", credential_row(status="revoked")),
    ("active with revoked_at", credential_row(revoked_at=ts("2026-05-01 00:00:00"))),
    ("expiry before issue", credential_row(expires_at=ts("2026-03-01 00:00:00"))),
    ("credential_type", credential_row(credential_type="diploma")),
]


@pytest.mark.parametrize(("label", "row"), CREDENTIAL_CASES, ids=[c[0] for c in CREDENTIAL_CASES])
def test_credential_checks(migrated_engine: Engine, label: str, row: dict) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, Credential, row)


VERIFICATION_CASES = [
    ("valid despite hash mismatch flag", verification_row(hash_match=False)),
    ("valid despite revoked flag", verification_row(is_revoked=True)),
    ("tampered must outrank revoked", verification_row(hash_match=False, is_revoked=True, result="revoked")),
    ("found row needs all flags", verification_row(is_expired=None)),
    ("not_found requires null credential_id", verification_row(result="not_found")),
    ("unknown channel", verification_row(channel="email")),
]


@pytest.mark.parametrize(("label", "row"), VERIFICATION_CASES, ids=[c[0] for c in VERIFICATION_CASES])
def test_verification_consistency_checks(migrated_engine: Engine, label: str, row: dict) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, VerificationRequest, row)


def test_verification_rows_consistent_with_precedence_are_accepted(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    insert(migrated_engine, VerificationRequest, verification_row())
    insert(migrated_engine, VerificationRequest, verification_row(
        verification_id="VER-0000002", hash_match=False, is_revoked=True, result="tampered"))
    insert(migrated_engine, VerificationRequest, verification_row(
        verification_id="VER-0000003", submitted_credential_id="CRED-UNKNOWN", credential_id=None,
        hash_match=None, signature_valid=None, is_revoked=None, is_expired=None, result="not_found"))
    assert scalar(migrated_engine, "SELECT count(*) FROM verification_requests") == 3


def test_audit_attempt_ref_checks(migrated_engine: Engine) -> None:
    with pytest.raises(IntegrityError, match="ck_audit_events_attempt_ref_required"):
        insert(migrated_engine, AuditEvent, audit_row(1, None, event_type="credential.issued"))
    with pytest.raises(IntegrityError, match="ck_audit_events_attempt_ref_format"):
        insert(migrated_engine, AuditEvent, audit_row(1, None, attempt_ref="attempt-1"))


# ------------------------------------------------------------------ UNIQUE constraints ---------------


def test_unique_email_course_code_rule_version_and_attempt(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="learners.email"):
        insert(migrated_engine, Learner, learner_row(learner_id="LRN-0000003"))
    with pytest.raises(IntegrityError, match="courses.course_code"):
        insert(migrated_engine, Course, course_row(course_id="CRS-00002"))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        insert(migrated_engine, CredentialRule, rule_row(status="draft", effective_from=None, activated_at=None))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        insert(migrated_engine, CompletionRecord, completion_row(completion_id="CMP-0000002"))


def test_issuer_supersession_is_linear(migrated_engine: Engine) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    insert(migrated_engine, Issuer, issuer_row(
        issuer_id="ISS-002", signing_key_id="ISS-002-K1", public_key="PK-2", supersedes_issuer_id="ISS-001"))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        insert(migrated_engine, Issuer, issuer_row(
            issuer_id="ISS-003", signing_key_id="ISS-003-K1", public_key="PK-3", supersedes_issuer_id="ISS-001"))
    with pytest.raises(IntegrityError, match="CHECK"):
        insert(migrated_engine, Issuer, issuer_row(
            issuer_id="ISS-004", signing_key_id="ISS-004-K1", public_key="PK-4", supersedes_issuer_id="ISS-004"))


def test_at_most_one_active_credential_per_learner_course(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="credentials.learner_id, credentials.course_id"):
        insert(migrated_engine, Credential, credential_row(credential_id=CRED_ID_2))


def test_reissue_allowed_after_revocation(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    revoke(migrated_engine)
    insert(migrated_engine, Credential, credential_row(credential_id=CRED_ID_2))
    assert scalar(migrated_engine, "SELECT count(*) FROM credentials WHERE status='active'") == 1


def test_at_most_one_active_rule_per_course(migrated_engine: Engine) -> None:
    # A course without completions, so the D5 effective-date guard cannot fire first.
    insert(migrated_engine, Issuer, issuer_row())
    insert(migrated_engine, Course, course_row())
    insert(migrated_engine, CredentialRule, rule_row())
    with pytest.raises(IntegrityError, match="credential_rules.course_id"):
        insert(migrated_engine, CredentialRule, rule_row(rule_id="RUL-00001-V2", version=2))


# ------------------------------------------------------------------ revocation constraints ----------


def test_one_revocation_per_credential(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    revoke(migrated_engine)
    with pytest.raises(IntegrityError, match="revocations.credential_id"):
        insert(migrated_engine, Revocation, revocation_row(revocation_id="REV-000002"))


def test_revocation_reason_checks(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="ck_revocations_reason_code"):
        insert(migrated_engine, Revocation, revocation_row(reason_code="changed_mind"))
    with pytest.raises(IntegrityError, match="ck_revocations_reason_text"):
        insert(migrated_engine, Revocation, revocation_row(reason_code="other"))
    insert(migrated_engine, Revocation, revocation_row(reason_code="other", reason_text="Panel decision"))


def test_revocation_requires_existing_credential(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        insert(migrated_engine, Revocation, revocation_row(credential_id=CRED_ID))
