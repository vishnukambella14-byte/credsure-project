"""Append-only, hash-chained audit events (ADR-009 amended by ADR-020, BR-21, BR-22, D4).

Hash (ADR-020)::

    event_hash = sha256_hex(canonical_json({
        "previous_hash", "event_uuid", "event_type", "entity_type", "entity_id", "actor_id",
        "actor_role", "outcome", "reason_code", "attempt_ref",
        "details_json": <stored canonical string>, "occurred_at": format_utc(...)
    }))

* ``record_event`` inserts inside the **caller's** write transaction (``BEGIN IMMEDIATE``): it
  reads the chain head and inserts in that same transaction, so writes are serialised and the
  success event commits atomically with the business change. It never commits.
* ``record_event_in_new_transaction`` is for rejection events, written after the business
  transaction rolled back (architecture §4 "Rejection auditing").
* Existing events are never updated or deleted (plain INSERT only; triggers enforce it).
* ``verify_chain`` recomputes every hash and link. It detects application-level tampering and
  edits that bypass the triggers without rewriting the whole chain. It does **not** protect
  against an attacker with unrestricted access to the SQLite file, who can rewrite or truncate
  the whole chain consistently or restore an older file (ADR-020).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.timeutil import format_utc, utc_now
from app.database.session import WRITE_OPTION, WriteSessionLocal
from app.models import AuditEvent
from app.services.crypto import canonical_json, sha256_hex

HASHED_FIELDS: tuple[str, ...] = (
    "previous_hash", "event_uuid", "event_type", "entity_type", "entity_id", "actor_id",
    "actor_role", "outcome", "reason_code", "attempt_ref", "details_json", "occurred_at",
)
SYSTEM_ACTOR_ID = "system"
SYSTEM_ACTOR_ROLE = "system"


def compute_event_hash(
    *,
    previous_hash: str | None,
    event_uuid: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_role: str,
    outcome: str,
    reason_code: str | None,
    attempt_ref: str | None,
    details_json: str,
    occurred_at: datetime,
) -> str:
    """ADR-020 event hash. ``details_json`` is the stored canonical string, hashed as a string."""
    return sha256_hex(
        canonical_json(
            {
                "previous_hash": previous_hash,
                "event_uuid": event_uuid,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "outcome": outcome,
                "reason_code": reason_code,
                "attempt_ref": attempt_ref,
                "details_json": details_json,
                "occurred_at": format_utc(occurred_at),
            }
        )
    )


def hash_of(event: AuditEvent) -> str:
    """Recompute the hash of a stored event from its columns."""
    return compute_event_hash(**{field: getattr(event, field) for field in HASHED_FIELDS})


def _require_write_transaction(session: Session) -> None:
    if not session.connection().get_execution_options().get(WRITE_OPTION, False):
        raise RuntimeError(
            "audit events must be written in a BEGIN IMMEDIATE write session (WriteSessionLocal); "
            "D4 requires serialised audit writes"
        )


def current_head_hash(session: Session) -> str | None:
    """Hash of the newest event, or None for an empty chain. Read inside the write transaction."""
    return session.execute(
        select(AuditEvent.event_hash).order_by(AuditEvent.event_id.desc()).limit(1)
    ).scalar_one_or_none()


def record_event(
    session: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_role: str,
    outcome: str = "success",
    reason_code: str | None = None,
    attempt_ref: str | None = None,
    details: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> AuditEvent:
    """Append one event to the chain inside the caller's write transaction (flushes, never commits).

    ``details`` must be canonicalisable (no floats: format scores with ``format_score``).
    """
    _require_write_transaction(session)
    previous_hash = current_head_hash(session)  # genesis: None
    fields: dict[str, Any] = {
        "previous_hash": previous_hash,
        "event_uuid": str(uuid.uuid4()),
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "outcome": outcome,
        "reason_code": reason_code,
        "attempt_ref": attempt_ref,
        "details_json": canonical_json(dict(details or {})),
        "occurred_at": occurred_at or clock(),
    }
    event = AuditEvent(**fields, event_hash=compute_event_hash(**fields))
    session.add(event)
    session.flush()  # plain INSERT; the chain-link trigger re-checks previous_hash
    return event


def record_event_in_new_transaction(
    session_factory: Callable[[], Session] | None = None, **event: Any
) -> AuditEvent:
    """Write one event in its own BEGIN IMMEDIATE transaction (rejections after rollback).

    ``session_factory`` defaults to the application's ``WriteSessionLocal`` (resolved at call time).
    """
    factory = session_factory or WriteSessionLocal
    with factory() as session:
        recorded = record_event(session, **event)
        session.commit()
        return recorded


# --------------------------------------------------------------- chain verification ---------------
@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    events_checked: int
    first_broken_event_id: int | None = None
    problem: str | None = None  # modified_event | broken_link | missing_events | invalid_genesis
    #                              | truncated_tail


def verify_chain(session: Session) -> ChainVerification:
    """Recompute and check the whole chain in ``event_id`` order. Read-only.

    Detected: modified events (hash mismatch), wrong links or duplicated/re-inserted events
    (``previous_hash`` differs from the preceding event's hash), missing events (gaps in the
    AUTOINCREMENT sequence or a non-genesis first event) and naive tail deletion
    (``sqlite_sequence`` ahead of ``MAX(event_id)``).
    """
    expected_previous: str | None = None
    expected_id = 1
    checked = 0
    for event in session.execute(select(AuditEvent).order_by(AuditEvent.event_id)).scalars():
        if event.event_id != expected_id:
            problem = "invalid_genesis" if checked == 0 and event.previous_hash is not None else "missing_events"
            return ChainVerification(False, checked, event.event_id, problem)
        if event.previous_hash != expected_previous:
            problem = "invalid_genesis" if checked == 0 else "broken_link"
            return ChainVerification(False, checked, event.event_id, problem)
        if hash_of(event) != event.event_hash:
            return ChainVerification(False, checked, event.event_id, "modified_event")
        expected_previous = event.event_hash
        expected_id += 1
        checked += 1

    sequence = session.execute(
        text("SELECT seq FROM sqlite_sequence WHERE name = 'audit_events'")
    ).scalar()
    if sequence is not None and sequence > checked:
        return ChainVerification(False, checked, checked + 1, "truncated_tail")
    return ChainVerification(True, checked)
