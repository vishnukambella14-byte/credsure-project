"""The 16 triggers and 2 raw indexes from data_dictionary §6 (D1, D4, D5, D7)."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.models import AuditEvent, CompletionRecord, Credential, CredentialRule, Issuer, VerificationRequest
from tests.support import (
    CRED_ID,
    audit_row,
    completion_row,
    credential_row,
    execute,
    insert,
    issuer_row,
    revoke,
    rule_row,
    scalar,
    seed_core,
    seed_with_credential,
    ts,
    verification_row,
)

# ------------------------------------------------------------------ credentials (D1) -----------------


@pytest.mark.parametrize(
    "assignment",
    [
        "payload_hash = 'b' || substr(payload_hash, 2)",
        "payload_json = 'tampered'",
        "signature = 'T' || substr(signature, 2)",
        "issued_at = '2026-03-03 00:00:00'",
        "expires_at = '2030-01-01 00:00:00'",
        "learner_id = 'LRN-0000002'",
        "issuer_id = 'ISS-001'",  # same value written explicitly is fine; covered below as no-op
    ],
)
def test_credential_evidence_and_payload_are_immutable(migrated_engine: Engine, assignment: str) -> None:
    seed_with_credential(migrated_engine)
    if assignment == "issuer_id = 'ISS-001'":
        execute(migrated_engine, f"UPDATE credentials SET {assignment}")  # unchanged value: allowed
        return
    with pytest.raises(IntegrityError, match="only status and revoked_at may change|FOREIGN KEY"):
        execute(migrated_engine, f"UPDATE credentials SET {assignment}")


def test_credentials_cannot_be_deleted(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="credentials cannot be deleted"):
        execute(migrated_engine, "DELETE FROM credentials")


def test_revocation_without_revocation_row_is_rejected(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="only active -> revoked with a matching revocation"):
        execute(
            migrated_engine,
            "UPDATE credentials SET status='revoked', revoked_at='2026-05-01 00:00:00'",
        )


def test_atomic_revocation_is_the_permitted_transition(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    revoke(migrated_engine)
    assert scalar(migrated_engine, "SELECT status FROM credentials") == "revoked"
    assert scalar(migrated_engine, "SELECT count(*) FROM revocations") == 1


def test_failed_revocation_rolls_back_both_writes(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO revocations (revocation_id, credential_id, reason_code, approved_by, "
                "revoked_at, record_source, created_at) VALUES ('REV-000001', :c, 'fraud', 'AA-1', "
                "'2026-05-01 00:00:00', 'platform', '2026-05-01 00:00:00')"), {"c": CRED_ID})
            # revoked_at differs from the revocation row: the transition trigger must abort
            conn.execute(text(
                "UPDATE credentials SET status='revoked', revoked_at='2026-05-02 00:00:00'"))
    assert scalar(migrated_engine, "SELECT count(*) FROM revocations") == 0
    assert scalar(migrated_engine, "SELECT status FROM credentials") == "active"


def test_revoked_credential_cannot_be_reinstated(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    revoke(migrated_engine)
    with pytest.raises(IntegrityError, match="only active -> revoked"):
        execute(migrated_engine, "UPDATE credentials SET status='active', revoked_at=NULL")
    with pytest.raises(IntegrityError, match="only active -> revoked"):
        execute(migrated_engine, "UPDATE credentials SET revoked_at='2026-06-01 00:00:00'")


def test_revocation_records_are_immutable(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    revoke(migrated_engine)
    with pytest.raises(IntegrityError, match="revocations are immutable"):
        execute(migrated_engine, "UPDATE revocations SET reason_code='fraud'")
    with pytest.raises(IntegrityError, match="revocations are immutable"):
        execute(migrated_engine, "DELETE FROM revocations")


# ------------------------------------------------------------------ issuers, rules, completions ------


def test_issuer_only_active_to_retired(migrated_engine: Engine) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    with pytest.raises(IntegrityError, match="issuers: only"):
        execute(migrated_engine, "UPDATE issuers SET name='Renamed'")
    execute(migrated_engine, "UPDATE issuers SET status='retired', valid_to='2026-06-01 00:00:00'")
    with pytest.raises(IntegrityError, match="issuers: only"):
        execute(migrated_engine, "UPDATE issuers SET status='active', valid_to=NULL")
    with pytest.raises(IntegrityError, match="issuers cannot be deleted"):
        execute(migrated_engine, "DELETE FROM issuers")


def test_rule_criteria_frozen_after_activation(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    with pytest.raises(IntegrityError, match="credential_rules: criteria"):
        execute(migrated_engine, "UPDATE credential_rules SET min_score = 50")


def test_rule_draft_is_editable_and_transitions_are_one_way(migrated_engine: Engine) -> None:
    seed_core(migrated_engine)
    insert(migrated_engine, CredentialRule, rule_row(
        rule_id="RUL-00001-V2", version=2, status="draft", effective_from=None, activated_at=None))
    execute(migrated_engine, "UPDATE credential_rules SET min_score = 75 WHERE rule_id='RUL-00001-V2'")
    with migrated_engine.begin() as conn:  # forward activation, predecessor retired in same txn
        conn.execute(text("UPDATE credential_rules SET status='retired', effective_to='2026-04-01 00:00:00' "
                          "WHERE rule_id='RUL-00001-V1'"))
        conn.execute(text("UPDATE credential_rules SET status='active', effective_from='2026-04-01 00:00:00', "
                          "activated_at='2026-04-01 00:00:00' WHERE rule_id='RUL-00001-V2'"))
    with pytest.raises(IntegrityError, match="credential_rules"):
        execute(migrated_engine, "UPDATE credential_rules SET status='active', effective_to=NULL "
                                 "WHERE rule_id='RUL-00001-V1'")


def test_backdated_activation_over_recorded_completion_is_rejected(migrated_engine: Engine) -> None:
    """D5: the completion recorded on 2026-03-01 is governed by v1 and must stay so."""
    seed_core(migrated_engine)
    insert(migrated_engine, CredentialRule, rule_row(
        rule_id="RUL-00001-V2", version=2, status="draft", effective_from=None, activated_at=None))
    with pytest.raises(IntegrityError, match="rule_effective_date_conflict"):
        with migrated_engine.begin() as conn:
            conn.execute(text("UPDATE credential_rules SET status='retired', effective_to='2026-02-01 00:00:00' "
                              "WHERE rule_id='RUL-00001-V1'"))
            conn.execute(text("UPDATE credential_rules SET status='active', effective_from='2026-02-01 00:00:00', "
                              "activated_at='2026-11-14 00:00:00' WHERE rule_id='RUL-00001-V2'"))
    assert scalar(migrated_engine, "SELECT status FROM credential_rules WHERE rule_id='RUL-00001-V1'") == "active"


def test_insert_of_non_draft_rule_over_recorded_completion_is_rejected(migrated_engine: Engine) -> None:
    """D5 INSERT guard (loader path): rules must be loaded before the completions they govern."""
    insert(migrated_engine, Issuer, issuer_row())
    from tests.support import course_row, learner_row
    from app.models import Course, Learner

    insert(migrated_engine, Learner, learner_row())
    insert(migrated_engine, Course, course_row())
    insert(migrated_engine, CompletionRecord, completion_row())  # completed 2026-03-01
    with pytest.raises(IntegrityError, match="rule_effective_date_conflict"):
        insert(migrated_engine, CredentialRule, rule_row())  # effective from 2026-01-01
    insert(migrated_engine, CredentialRule, rule_row(effective_from=ts("2026-03-02 00:00:00")))


def test_completion_locked_while_active_credential_exists(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="completion_locked"):
        execute(migrated_engine, "UPDATE completion_records SET score = 99")
    revoke(migrated_engine)
    execute(migrated_engine, "UPDATE completion_records SET score = 99")  # unlocked after revocation


def test_verification_requests_are_immutable(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    insert(migrated_engine, VerificationRequest, verification_row())
    with pytest.raises(IntegrityError, match="verification_requests are immutable"):
        execute(migrated_engine, "UPDATE verification_requests SET verifier_name='X'")
    with pytest.raises(IntegrityError, match="verification_requests are immutable"):
        execute(migrated_engine, "DELETE FROM verification_requests")


# ------------------------------------------------------------------ audit chain (D4) and D7 ----------


def _chain(engine: Engine, length: int) -> None:
    previous = None
    for n in range(1, length + 1):
        insert(engine, AuditEvent, audit_row(n, previous))
        previous = f"{n:064x}"


def test_audit_events_are_append_only(migrated_engine: Engine) -> None:
    _chain(migrated_engine, 2)
    with pytest.raises(IntegrityError, match="append-only"):
        execute(migrated_engine, "UPDATE audit_events SET outcome='error'")
    with pytest.raises(IntegrityError, match="append-only"):
        execute(migrated_engine, "DELETE FROM audit_events")


def test_single_genesis_event(migrated_engine: Engine) -> None:
    _chain(migrated_engine, 1)
    with pytest.raises(IntegrityError, match="current chain head"):
        insert(migrated_engine, AuditEvent, audit_row(2, None))


def test_chain_is_linear(migrated_engine: Engine) -> None:
    _chain(migrated_engine, 3)  # h1 <- h2 <- h3
    with pytest.raises(IntegrityError, match="current chain head"):
        insert(migrated_engine, AuditEvent, audit_row(4, f"{1:064x}"))  # fork from h1
    with pytest.raises(IntegrityError, match="current chain head"):
        insert(migrated_engine, AuditEvent, audit_row(4, "f" * 64))  # unknown predecessor
    insert(migrated_engine, AuditEvent, audit_row(4, f"{3:064x}"))  # extends the head
    assert scalar(migrated_engine, "SELECT max(event_id) FROM audit_events") == 4
    assert scalar(migrated_engine, "SELECT seq FROM sqlite_sequence WHERE name='audit_events'") == 4


def test_unique_indexes_alone_stop_second_genesis_and_forks(migrated_engine: Engine) -> None:
    """Defence in depth: with the chain-link trigger gone, the indexes still hold."""
    _chain(migrated_engine, 2)
    execute(migrated_engine, "DROP TRIGGER trg_audit_events_chain_link")
    with pytest.raises(IntegrityError, match="uq_audit_events_single_genesis"):
        insert(migrated_engine, AuditEvent, audit_row(3, None))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        insert(migrated_engine, AuditEvent, audit_row(3, f"{1:064x}"))


def test_at_most_one_terminal_event_per_attempt_ref(migrated_engine: Engine) -> None:
    _chain(migrated_engine, 1)
    insert(migrated_engine, AuditEvent, audit_row(
        2, f"{1:064x}", event_type="eligibility.evaluated", attempt_ref="ATT-000001"))
    insert(migrated_engine, AuditEvent, audit_row(
        3, f"{2:064x}", event_type="credential.issue_rejected", attempt_ref="ATT-000001",
        outcome="rejected", reason_code="duplicate_credential_id"))
    with pytest.raises(IntegrityError, match="audit_events.attempt_ref"):
        insert(migrated_engine, AuditEvent, audit_row(
            4, f"{3:064x}", event_type="credential.issued", attempt_ref="ATT-000001"))
    insert(migrated_engine, AuditEvent, audit_row(  # platform-style reference, new attempt
        4, f"{3:064x}", event_type="credential.issued", attempt_ref="ATT-7K2M9QXR4T1B"))


# ------------------------------------------------------------------ REPLACE bypass (D3) --------------


def test_replace_cannot_bypass_audit_append_only(migrated_engine: Engine) -> None:
    _chain(migrated_engine, 2)
    # previous_hash equals the head, so only the delete trigger (recursive_triggers=ON) can stop it.
    with pytest.raises(IntegrityError, match="append-only"):
        execute(migrated_engine,
                "INSERT OR REPLACE INTO audit_events (event_id, event_uuid, event_type, entity_type, "
                "entity_id, actor_id, actor_role, outcome, occurred_at, previous_hash, event_hash) "
                "VALUES (1, 'u-x', 'x', 'x', 'x', 'x', 'x', 'success', '2026-01-01 00:00:00', :h, :n)",
                {"h": f"{2:064x}", "n": "e" * 64})
    assert scalar(migrated_engine, "SELECT event_type FROM audit_events WHERE event_id=1") == "dataset.imported"


def test_replace_cannot_overwrite_credential_or_revocation(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    with pytest.raises(IntegrityError, match="credentials cannot be deleted"):
        with migrated_engine.begin() as conn:
            conn.execute(Credential.__table__.insert().prefix_with("OR REPLACE").values(
                **credential_row(payload_hash="c" * 64)))
    assert scalar(migrated_engine, "SELECT payload_hash FROM credentials") == "a" * 64
    revoke(migrated_engine)
    with pytest.raises(IntegrityError, match="revocations are immutable"):
        execute(migrated_engine,
                "INSERT OR REPLACE INTO revocations (revocation_id, credential_id, reason_code, approved_by, "
                "revoked_at, record_source, created_at) VALUES ('REV-000001', :c, 'fraud', 'X', "
                "'2026-05-01 00:00:00', 'platform', '2026-05-01 00:00:00')", {"c": CRED_ID})


def test_replace_bypass_exists_without_recursive_triggers(migrated_engine: Engine) -> None:
    """Control: proves the REPLACE tests above depend on the D3 connection configuration."""
    _chain(migrated_engine, 2)
    db_path = migrated_engine.url.database
    raw = sqlite3.connect(db_path)  # no CredSure configuration: recursive_triggers is OFF
    try:
        raw.execute(
            "INSERT OR REPLACE INTO audit_events (event_id, event_uuid, event_type, entity_type, entity_id, "
            "actor_id, actor_role, outcome, occurred_at, previous_hash, event_hash) "
            "VALUES (1, 'u-x', 'rewritten', 'x', 'x', 'x', 'x', 'success', '2026-01-01 00:00:00', ?, ?)",
            (f"{2:064x}", "e" * 64),
        )
        rewritten = raw.execute("SELECT event_type FROM audit_events WHERE event_id=1").fetchone()[0]
        raw.rollback()
    finally:
        raw.close()
    assert rewritten == "rewritten"
