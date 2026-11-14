"""Audit service: ADR-020 hash, genesis, linkage, serialised writes, chain verification."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import Engine, text

from app.models import AuditEvent
from app.services import audit
from app.services.crypto import CanonicalizationError
from tests.support import execute, read_sessions, scalar, ts, write_sessions

EVENT = dict(
    event_type="credential.issued", entity_type="credential", entity_id="CRED-7K2M9QXR4T1B",
    actor_id="AA-1", actor_role="academic_admin", attempt_ref="ATT-7K2M9QXR4T1B",
    details={"payload_hash": "a" * 64, "score": "82.50"},
)


def _record(engine: Engine, n: int = 1, **overrides: object) -> list[AuditEvent]:
    events = []
    with write_sessions(engine)() as session:
        for i in range(n):
            fields = {**EVENT, "entity_id": f"E-{i}", "attempt_ref": None, "event_type": "dataset.imported",
                      "occurred_at": ts("2026-04-01 12:00:00"), **overrides}
            events.append(audit.record_event(session, **fields))
        session.commit()
    return events


def test_first_event_is_genesis(migrated_engine: Engine) -> None:
    (first,) = _record(migrated_engine)
    assert first.event_id == 1 and first.previous_hash is None


def test_subsequent_events_link_to_their_predecessor(migrated_engine: Engine) -> None:
    events = _record(migrated_engine, 3)
    assert [e.previous_hash for e in events] == [None, events[0].event_hash, events[1].event_hash]


def test_hash_matches_adr_020_formula(migrated_engine: Engine) -> None:
    with write_sessions(migrated_engine)() as session:
        event = audit.record_event(session, **EVENT, occurred_at=ts("2026-03-20 09:30:00"))
        session.commit()
    expected_input = json.dumps(
        {
            "previous_hash": None, "event_uuid": event.event_uuid, "event_type": "credential.issued",
            "entity_type": "credential", "entity_id": "CRED-7K2M9QXR4T1B", "actor_id": "AA-1",
            "actor_role": "academic_admin", "outcome": "success", "reason_code": None,
            "attempt_ref": "ATT-7K2M9QXR4T1B",
            "details_json": '{"payload_hash":"' + "a" * 64 + '","score":"82.50"}',
            "occurred_at": "2026-03-20T09:30:00Z",
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    assert event.event_hash == hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
    assert event.details_json == '{"payload_hash":"' + "a" * 64 + '","score":"82.50"}'


def test_writes_require_a_write_transaction(migrated_engine: Engine) -> None:
    with read_sessions(migrated_engine)() as session, pytest.raises(RuntimeError, match="BEGIN IMMEDIATE"):
        audit.record_event(session, **EVENT)
    assert scalar(migrated_engine, "SELECT count(*) FROM audit_events") == 0


def test_event_stays_in_callers_transaction(migrated_engine: Engine) -> None:
    with write_sessions(migrated_engine)() as session:
        audit.record_event(session, **EVENT)
        session.rollback()  # caller's business transaction fails
    assert scalar(migrated_engine, "SELECT count(*) FROM audit_events") == 0


def test_rejection_event_in_its_own_transaction(migrated_engine: Engine) -> None:
    event = audit.record_event_in_new_transaction(
        write_sessions(migrated_engine), **{**EVENT, "event_type": "credential.issue_rejected",
                                            "outcome": "rejected", "reason_code": "duplicate_credential_id"})
    assert scalar(migrated_engine, "SELECT attempt_ref FROM audit_events WHERE event_id = :i",
                  {"i": event.event_id}) == "ATT-7K2M9QXR4T1B"


def test_floats_in_details_are_rejected_and_nothing_is_written(migrated_engine: Engine) -> None:
    with write_sessions(migrated_engine)() as session, pytest.raises(CanonicalizationError):
        audit.record_event(session, **{**EVENT, "details": {"score": 82.5}})
    assert scalar(migrated_engine, "SELECT count(*) FROM audit_events") == 0


def test_service_never_modifies_existing_events(migrated_engine: Engine) -> None:
    _record(migrated_engine, 3)
    with migrated_engine.connect() as conn:
        before = conn.execute(text("SELECT * FROM audit_events ORDER BY event_id")).all()
    _record(migrated_engine, 2)
    with migrated_engine.connect() as conn:
        after = conn.execute(text("SELECT * FROM audit_events ORDER BY event_id")).all()
    assert after[:3] == before and len(after) == 5


# ------------------------------------------------------------------ verification ------------------
def _verify(engine: Engine) -> audit.ChainVerification:
    with read_sessions(engine)() as session:
        return audit.verify_chain(session)


def test_empty_and_valid_chains_pass(migrated_engine: Engine) -> None:
    assert _verify(migrated_engine) == audit.ChainVerification(True, 0)
    _record(migrated_engine, 4)
    assert _verify(migrated_engine) == audit.ChainVerification(True, 4)


def _drop_audit_triggers(engine: Engine) -> None:
    for name in ("trg_audit_events_no_update", "trg_audit_events_no_delete"):
        execute(engine, f"DROP TRIGGER {name}")


def test_modified_event_is_detected(migrated_engine: Engine) -> None:
    _record(migrated_engine, 3)
    _drop_audit_triggers(migrated_engine)  # simulate tampering that bypasses the triggers
    execute(migrated_engine, "UPDATE audit_events SET details_json = :d WHERE event_id = 2",
            {"d": '{"forged":true}'})
    assert _verify(migrated_engine) == audit.ChainVerification(False, 1, 2, "modified_event")


def test_incorrectly_linked_event_is_detected(migrated_engine: Engine) -> None:
    _record(migrated_engine, 3)
    _drop_audit_triggers(migrated_engine)
    execute(migrated_engine, "UPDATE audit_events SET previous_hash = :h WHERE event_id = 3", {"h": "f" * 64})
    assert _verify(migrated_engine) == audit.ChainVerification(False, 2, 3, "broken_link")


def test_missing_event_is_detected(migrated_engine: Engine) -> None:
    _record(migrated_engine, 3)
    _drop_audit_triggers(migrated_engine)
    execute(migrated_engine, "DELETE FROM audit_events WHERE event_id = 2")
    assert _verify(migrated_engine) == audit.ChainVerification(False, 1, 3, "missing_events")


def test_deleted_genesis_is_detected(migrated_engine: Engine) -> None:
    _record(migrated_engine, 2)
    _drop_audit_triggers(migrated_engine)
    execute(migrated_engine, "DELETE FROM audit_events WHERE event_id = 1")
    assert _verify(migrated_engine) == audit.ChainVerification(False, 0, 2, "invalid_genesis")


def test_naive_tail_deletion_is_detected(migrated_engine: Engine) -> None:
    _record(migrated_engine, 3)
    _drop_audit_triggers(migrated_engine)
    execute(migrated_engine, "DELETE FROM audit_events WHERE event_id = 3")
    assert _verify(migrated_engine) == audit.ChainVerification(False, 2, 3, "truncated_tail")
