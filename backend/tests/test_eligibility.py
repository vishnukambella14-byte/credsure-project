"""Eligibility (BR-04 every outcome, BR-07 / D5 rule selection, AS-04, SR-12)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.models import CompletionRecord, Course, Credential, CredentialRule
from app.services import eligibility as el
from app.services.crypto import KeyStore, generate_private_key
from tests.support import (
    completion_row,
    course_row,
    credential_row,
    execute,
    insert,
    read_sessions,
    rule_row,
    scalar,
    seed_core,
    ts,
)

NOW = ts("2026-11-14 12:00:00")


@pytest.fixture
def keys(tmp_path: Path) -> KeyStore:
    store = KeyStore(tmp_path / "platform", tmp_path / "synthetic")
    store.store_new_platform_key("ISS-001-K1", generate_private_key())
    return store


@pytest.fixture
def world(migrated_engine: Engine) -> Engine:
    """seed_core: LRN-0000001 completed CRS-00001 on 2026-03-01 (82.5 / 95%) under RUL-00001-V1
    (min_score 70, effective 2026-01-01); LRN-0000002 has no completion."""
    seed_core(migrated_engine)
    return migrated_engine


def evaluate(engine: Engine, keys: KeyStore, learner: str = "LRN-0000001", course: str = "CRS-00001",
             at=NOW) -> el.EligibilityResult:  # noqa: ANN001
    with read_sessions(engine)() as session:
        return el.evaluate(session, learner, course, at=at, key_store=keys)


def test_eligible(world: Engine, keys: KeyStore) -> None:
    result = evaluate(world, keys)
    assert result.eligible and result.reasons == ()
    assert all(result.checks[k] is True for k in el.CHECK_KEYS)
    assert (result.rule_id, result.rule_version, result.completion_id) == ("RUL-00001-V1", 1, "CMP-0000001")
    assert (result.issuer_id, result.signing_key_id) == ("ISS-001", "ISS-001-K1")
    assert el.ineligibility_error(result) is None


# ------------------------------------------------------------------ one test per BR-04 reason -----
def test_learner_not_found(world: Engine, keys: KeyStore) -> None:
    result = evaluate(world, keys, learner="LRN-0009999")
    assert el.LEARNER_NOT_FOUND in result.reasons and result.checks["learner_active"] is False


def test_learner_inactive(world: Engine, keys: KeyStore) -> None:
    execute(world, "UPDATE learners SET status='inactive' WHERE learner_id='LRN-0000001'")
    assert evaluate(world, keys).reasons == (el.LEARNER_INACTIVE,)


def test_course_not_found(world: Engine, keys: KeyStore) -> None:
    result = evaluate(world, keys, course="CRS-09999")
    assert el.COURSE_NOT_FOUND in result.reasons and el.NO_COMPLETED_RECORD in result.reasons
    assert result.checks["issuer_ready"] is None  # cannot be evaluated without a course


def test_course_inactive(world: Engine, keys: KeyStore) -> None:
    execute(world, "UPDATE courses SET status='archived' WHERE course_id='CRS-00001'")
    assert evaluate(world, keys).reasons == (el.COURSE_INACTIVE,)


def test_no_completed_record(world: Engine, keys: KeyStore) -> None:
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", learner_id="LRN-0000002", status="in_progress", completed_at=None))
    result = evaluate(world, keys, learner="LRN-0000002")
    assert result.reasons == (el.NO_COMPLETED_RECORD,)
    assert result.checks["rule_found"] is None and result.checks["score_ok"] is None


def test_completion_in_future(world: Engine, keys: KeyStore) -> None:
    result = evaluate(world, keys, at=ts("2026-02-01 00:00:00"))
    assert result.reasons == (el.COMPLETION_IN_FUTURE,)


def test_no_applicable_rule(world: Engine, keys: KeyStore) -> None:
    insert(world, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(world, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002",
                                           effective_from=ts("2026-06-01 00:00:00")))
    insert(world, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
    result = evaluate(world, keys, course="CRS-00002")
    assert result.reasons == (el.NO_APPLICABLE_RULE,) and result.rule_id is None
    assert result.checks["score_ok"] is None  # no rule: thresholds not evaluated, no fallback


def test_score_below_minimum(world: Engine, keys: KeyStore) -> None:
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", learner_id="LRN-0000002", score=69.99))
    assert evaluate(world, keys, learner="LRN-0000002").reasons == (el.SCORE_BELOW_MINIMUM,)


def test_score_at_minimum_passes_and_null_score_fails(world: Engine, keys: KeyStore) -> None:
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", learner_id="LRN-0000002", score=70.0))
    assert evaluate(world, keys, learner="LRN-0000002").eligible
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000003", learner_id="LRN-0000002", attempt_number=2, score=None))
    assert evaluate(world, keys, learner="LRN-0000002").reasons == (el.SCORE_BELOW_MINIMUM,)


def test_attendance_below_minimum(world: Engine, keys: KeyStore) -> None:
    insert(world, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(world, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002",
                                           min_score=None, min_attendance_pct=80.0))
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", course_id="CRS-00002", score=None, attendance_pct=79.0))
    result = evaluate(world, keys, course="CRS-00002")
    assert result.reasons == (el.ATTENDANCE_BELOW_MINIMUM,)
    assert result.checks["score_ok"] is True  # no score requirement on this rule


def test_already_issued_maps_to_409_credential_already_issued(world: Engine, keys: KeyStore) -> None:
    insert(world, Credential, credential_row())
    result = evaluate(world, keys)
    assert result.reasons == (el.ALREADY_ISSUED,)
    error = el.ineligibility_error(result)
    assert (error.code, error.status_code, error.details) == (
        "credential_already_issued", 409, {"reasons": ["already_issued"]})


def test_issuer_inactive(world: Engine, keys: KeyStore) -> None:
    execute(world, "UPDATE issuers SET status='retired', valid_to='2026-06-01 00:00:00'")
    assert evaluate(world, keys).reasons == (el.ISSUER_INACTIVE,)


def test_signing_key_unavailable(world: Engine, tmp_path: Path) -> None:
    empty = KeyStore(tmp_path / "none", tmp_path / "none-synthetic")
    result = evaluate(world, empty)
    assert result.reasons == (el.SIGNING_KEY_UNAVAILABLE,) and result.checks["issuer_ready"] is False


def test_all_failing_reasons_are_returned(world: Engine, tmp_path: Path) -> None:
    execute(world, "UPDATE learners SET status='inactive' WHERE learner_id='LRN-0000002'")
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", learner_id="LRN-0000002", score=10.0))
    result = evaluate(world, KeyStore(tmp_path / "x", tmp_path / "y"), learner="LRN-0000002")
    assert result.reasons == (el.LEARNER_INACTIVE, el.SCORE_BELOW_MINIMUM, el.SIGNING_KEY_UNAVAILABLE)
    error = el.ineligibility_error(result)
    assert (error.code, error.status_code) == ("not_eligible", 422)
    assert error.details == {"reasons": list(result.reasons)}


def test_already_issued_wins_over_other_reasons(world: Engine, keys: KeyStore) -> None:
    insert(world, Credential, credential_row())
    execute(world, "UPDATE learners SET status='inactive' WHERE learner_id='LRN-0000001'")
    error = el.ineligibility_error(evaluate(world, keys))
    assert error.code == "credential_already_issued"
    assert error.details["reasons"] == ["learner_inactive", "already_issued"]


# ------------------------------------------------------------------ AS-04 and D5 -----------------
def test_latest_completed_attempt_is_used(world: Engine, keys: KeyStore) -> None:
    for number, status, score in [(1, "completed", 65.0), (2, "completed", 82.0), (3, "in_progress", None)]:
        insert(world, CompletionRecord, completion_row(
            completion_id=f"CMP-000010{number}", learner_id="LRN-0000002", attempt_number=number,
            status=status, score=score, completed_at=ts("2026-03-01 10:00:00") if status == "completed" else None))
    result = evaluate(world, keys, learner="LRN-0000002")
    assert result.eligible and result.completion_id == "CMP-0000102"


def _activate_v2(engine: Engine, effective_from: str, min_score: float = 90.0) -> None:
    insert(engine, CredentialRule, rule_row(rule_id="RUL-00001-V2", version=2, status="draft",
                                            effective_from=None, activated_at=None, min_score=min_score))
    with engine.begin() as conn:
        conn.execute(text("UPDATE credential_rules SET status='retired', effective_to=:t WHERE rule_id='RUL-00001-V1'"),
                     {"t": effective_from})
        conn.execute(text("UPDATE credential_rules SET status='active', effective_from=:t, activated_at=:t "
                          "WHERE rule_id='RUL-00001-V2'"), {"t": effective_from})


def test_rule_is_selected_by_completion_time(world: Engine, keys: KeyStore) -> None:
    _activate_v2(world, "2026-04-01 00:00:00")  # after the 2026-03-01 completion: allowed
    insert(world, CompletionRecord, completion_row(
        completion_id="CMP-0000002", learner_id="LRN-0000002", score=82.0,
        completed_at=ts("2026-05-01 10:00:00"), recorded_at=ts("2026-05-01 10:00:00")))

    march = evaluate(world, keys)  # completed under v1 (min 70)
    assert march.eligible and (march.rule_id, march.rule_version) == ("RUL-00001-V1", 1)

    may = evaluate(world, keys, learner="LRN-0000002")  # completed under v2 (min 90)
    assert (may.rule_id, may.rule_version) == ("RUL-00001-V2", 2)
    assert may.reasons == (el.SCORE_BELOW_MINIMUM,)


def test_window_boundaries_are_half_open(world: Engine) -> None:
    _activate_v2(world, "2026-04-01 00:00:00")
    with read_sessions(world)() as session:
        assert el.applicable_rule(session, "CRS-00001", ts("2025-12-31 23:59:59")) is None
        assert el.applicable_rule(session, "CRS-00001", ts("2026-01-01 00:00:00")).rule_id == "RUL-00001-V1"
        assert el.applicable_rule(session, "CRS-00001", ts("2026-03-31 23:59:59")).rule_id == "RUL-00001-V1"
        assert el.applicable_rule(session, "CRS-00001", ts("2026-04-01 00:00:00")).rule_id == "RUL-00001-V2"


def test_later_rule_does_not_rebind_a_recorded_completion(world: Engine, keys: KeyStore) -> None:
    before = evaluate(world, keys)
    with pytest.raises(IntegrityError, match="rule_effective_date_conflict"):
        _activate_v2(world, "2026-02-01 00:00:00")  # backdated over the 2026-03-01 completion
    after = evaluate(world, keys)
    assert (after.rule_id, after.eligible) == (before.rule_id, before.eligible) == ("RUL-00001-V1", True)


def test_overlapping_windows_are_a_defect_not_a_fallback(world: Engine, keys: KeyStore) -> None:
    insert(world, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(world, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002", status="retired",
                                           effective_to=ts("2026-06-01 00:00:00")))
    insert(world, CredentialRule, rule_row(rule_id="RUL-00002-V2", course_id="CRS-00002", version=2,
                                           effective_from=ts("2026-02-01 00:00:00")))
    insert(world, CompletionRecord, completion_row(completion_id="CMP-0000002", course_id="CRS-00002"))
    with pytest.raises(el.RuleWindowConflict):
        evaluate(world, keys, course="CRS-00002")


def test_evaluation_is_read_only(world: Engine, keys: KeyStore) -> None:
    tables = ["credentials", "audit_events", "completion_records", "credential_rules"]
    before = {t: scalar(world, f"SELECT count(*) FROM {t}") for t in tables}
    with read_sessions(world)() as session:
        el.evaluate(session, "LRN-0000001", "CRS-00001", at=NOW, key_store=keys)
        assert not session.new and not session.dirty and not session.deleted
    assert {t: scalar(world, f"SELECT count(*) FROM {t}") for t in tables} == before
