"""Triggers and non-model indexes for the CredSure schema (data_dictionary §6, D1/D4/D5/D7).

Source of truth: the validated SQL block in ``docs/data_dictionary.md`` §6. These statements were
generated from that block verbatim; ``tests/test_migrations.py`` asserts they still match it.

Alembic autogenerate never creates triggers, and SQLite batch ("move and copy") table rebuilds
silently drop every trigger on the rebuilt table. Therefore:

* Migration ``0001`` applies ``SCHEMA_0001_CREATE`` explicitly and drops ``SCHEMA_0001_DROP``
  first in ``downgrade()``. These constants are frozen history: never edit them. A later
  revision that changes triggers adds a new constant and points ``CURRENT_*`` at it.
* Any later migration that batch-rebuilds a table listed in ``TRIGGER_TABLES`` must call
  ``reapply_for_table()`` afterwards; ``tests/test_migrations.py`` asserts the inventory at head.
* Application code uses plain ``INSERT`` only on these tables (no ``INSERT OR REPLACE``).
"""

from __future__ import annotations

import re
from collections.abc import Callable

SCHEMA_0001_CREATE: tuple[str, ...] = (
    """
CREATE UNIQUE INDEX uq_audit_events_single_genesis ON audit_events(ifnull(previous_hash, 'GENESIS'))
""",
    """
CREATE UNIQUE INDEX uq_audit_events_attempt_outcome ON audit_events(attempt_ref)
  WHERE event_type IN ('credential.issued','credential.issue_rejected')
""",
    """
CREATE TRIGGER trg_audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
""",
    """
CREATE TRIGGER trg_audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
""",
    """
CREATE TRIGGER trg_audit_events_chain_link BEFORE INSERT ON audit_events
WHEN NEW.previous_hash IS NOT (SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1)
BEGIN SELECT RAISE(ABORT, 'audit_events: previous_hash must equal the current chain head'); END
""",
    """
CREATE TRIGGER trg_revocations_no_update BEFORE UPDATE ON revocations
BEGIN SELECT RAISE(ABORT, 'revocations are immutable'); END
""",
    """
CREATE TRIGGER trg_revocations_no_delete BEFORE DELETE ON revocations
BEGIN SELECT RAISE(ABORT, 'revocations are immutable'); END
""",
    """
CREATE TRIGGER trg_credentials_no_delete BEFORE DELETE ON credentials
BEGIN SELECT RAISE(ABORT, 'credentials cannot be deleted'); END
""",
    """
CREATE TRIGGER trg_credentials_immutable BEFORE UPDATE ON credentials
WHEN NEW.credential_id IS NOT OLD.credential_id OR NEW.learner_id IS NOT OLD.learner_id
  OR NEW.course_id IS NOT OLD.course_id OR NEW.completion_id IS NOT OLD.completion_id
  OR NEW.rule_id IS NOT OLD.rule_id OR NEW.issuer_id IS NOT OLD.issuer_id
  OR NEW.credential_type IS NOT OLD.credential_type OR NEW.issued_at IS NOT OLD.issued_at
  OR NEW.expires_at IS NOT OLD.expires_at OR NEW.payload_json IS NOT OLD.payload_json
  OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.signature IS NOT OLD.signature
  OR NEW.signing_key_id IS NOT OLD.signing_key_id OR NEW.schema_version IS NOT OLD.schema_version
  OR NEW.issued_by IS NOT OLD.issued_by OR NEW.record_source IS NOT OLD.record_source
  OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'credentials: only status and revoked_at may change'); END
""",
    """
CREATE TRIGGER trg_credentials_status_transition BEFORE UPDATE OF status, revoked_at ON credentials
WHEN NOT (OLD.status = 'active' AND NEW.status = 'revoked'
          AND EXISTS (SELECT 1 FROM revocations r
                      WHERE r.credential_id = NEW.credential_id AND r.revoked_at = NEW.revoked_at))
BEGIN SELECT RAISE(ABORT, 'credentials: only active -> revoked with a matching revocation'); END
""",
    """
CREATE TRIGGER trg_issuers_no_delete BEFORE DELETE ON issuers
BEGIN SELECT RAISE(ABORT, 'issuers cannot be deleted'); END
""",
    """
CREATE TRIGGER trg_issuers_immutable BEFORE UPDATE ON issuers
WHEN NEW.issuer_id IS NOT OLD.issuer_id OR NEW.name IS NOT OLD.name
  OR NEW.legal_name IS NOT OLD.legal_name OR NEW.country_code IS NOT OLD.country_code
  OR NEW.website_url IS NOT OLD.website_url OR NEW.signing_key_id IS NOT OLD.signing_key_id
  OR NEW.public_key IS NOT OLD.public_key OR NEW.key_algorithm IS NOT OLD.key_algorithm
  OR NEW.supersedes_issuer_id IS NOT OLD.supersedes_issuer_id OR NEW.valid_from IS NOT OLD.valid_from
  OR NEW.record_source IS NOT OLD.record_source OR NEW.created_by IS NOT OLD.created_by
  OR NEW.created_at IS NOT OLD.created_at
  OR NOT ((NEW.status = OLD.status AND NEW.valid_to IS OLD.valid_to)
          OR (OLD.status = 'active' AND NEW.status = 'retired' AND OLD.valid_to IS NULL))
BEGIN SELECT RAISE(ABORT, 'issuers: only active -> retired (with valid_to) may change'); END
""",
    """
CREATE TRIGGER trg_credential_rules_immutable BEFORE UPDATE ON credential_rules
WHEN NEW.rule_id IS NOT OLD.rule_id OR NEW.course_id IS NOT OLD.course_id
  OR NEW.version IS NOT OLD.version OR NEW.record_source IS NOT OLD.record_source
  OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
  OR (OLD.status <> 'draft' AND (NEW.credential_type IS NOT OLD.credential_type
        OR NEW.min_score IS NOT OLD.min_score OR NEW.min_attendance_pct IS NOT OLD.min_attendance_pct
        OR NEW.validity_days IS NOT OLD.validity_days OR NEW.description IS NOT OLD.description
        OR NEW.effective_from IS NOT OLD.effective_from OR NEW.activated_at IS NOT OLD.activated_at))
  OR NOT ((NEW.status = OLD.status AND NEW.effective_to IS OLD.effective_to)
          OR (OLD.status = 'draft' AND NEW.status = 'active')
          OR (OLD.status = 'active' AND NEW.status = 'retired' AND OLD.effective_to IS NULL))
BEGIN SELECT RAISE(ABORT, 'credential_rules: criteria are immutable once activated; transitions are draft -> active -> retired'); END
""",
    """
CREATE TRIGGER trg_credential_rules_effective_date_guard_update BEFORE UPDATE OF status ON credential_rules
WHEN OLD.status = 'draft' AND NEW.status = 'active' AND EXISTS (
  SELECT 1 FROM completion_records cr
  WHERE cr.course_id = NEW.course_id AND cr.completed_at >= NEW.effective_from)
BEGIN SELECT RAISE(ABORT, 'rule_effective_date_conflict'); END
""",
    """
CREATE TRIGGER trg_credential_rules_effective_date_guard_insert BEFORE INSERT ON credential_rules
WHEN NEW.status <> 'draft' AND EXISTS (
  SELECT 1 FROM completion_records cr
  WHERE cr.course_id = NEW.course_id AND cr.completed_at >= NEW.effective_from)
BEGIN SELECT RAISE(ABORT, 'rule_effective_date_conflict'); END
""",
    """
CREATE TRIGGER trg_completion_records_locked BEFORE UPDATE ON completion_records
WHEN EXISTS (SELECT 1 FROM credentials c
             WHERE c.completion_id = OLD.completion_id AND c.status = 'active')
BEGIN SELECT RAISE(ABORT, 'completion_locked'); END
""",
    """
CREATE TRIGGER trg_verification_requests_no_update BEFORE UPDATE ON verification_requests
BEGIN SELECT RAISE(ABORT, 'verification_requests are immutable'); END
""",
    """
CREATE TRIGGER trg_verification_requests_no_delete BEFORE DELETE ON verification_requests
BEGIN SELECT RAISE(ABORT, 'verification_requests are immutable'); END
""",
)

SCHEMA_0001_TRIGGERS: tuple[str, ...] = ('trg_audit_events_no_update', 'trg_audit_events_no_delete', 'trg_audit_events_chain_link', 'trg_revocations_no_update', 'trg_revocations_no_delete', 'trg_credentials_no_delete', 'trg_credentials_immutable', 'trg_credentials_status_transition', 'trg_issuers_no_delete', 'trg_issuers_immutable', 'trg_credential_rules_immutable', 'trg_credential_rules_effective_date_guard_update', 'trg_credential_rules_effective_date_guard_insert', 'trg_completion_records_locked', 'trg_verification_requests_no_update', 'trg_verification_requests_no_delete')
SCHEMA_0001_INDEXES: tuple[str, ...] = ('uq_audit_events_single_genesis', 'uq_audit_events_attempt_outcome')

SCHEMA_0001_DROP: tuple[str, ...] = tuple(
    [f"DROP TRIGGER IF EXISTS {name}" for name in SCHEMA_0001_TRIGGERS]
    + [f"DROP INDEX IF EXISTS {name}" for name in SCHEMA_0001_INDEXES]
)

# Current head definitions (equal to revision 0001 until a later revision changes them).
CURRENT_CREATE = SCHEMA_0001_CREATE
CURRENT_TRIGGERS = SCHEMA_0001_TRIGGERS
CURRENT_INDEXES = SCHEMA_0001_INDEXES

_ON_TABLE = re.compile(r"\bON\s+(\w+)", re.IGNORECASE)
_NAME = re.compile(r"CREATE\s+(UNIQUE\s+INDEX|TRIGGER)\s+(\w+)", re.IGNORECASE)


def table_of(statement: str) -> str:
    """Table a CREATE TRIGGER / CREATE INDEX statement is attached to."""
    match = _ON_TABLE.search(statement)
    if match is None:  # pragma: no cover - every statement names its table
        raise ValueError(f"cannot find table in statement: {statement[:60]}")
    return match.group(1)


def object_of(statement: str) -> tuple[str, str]:
    """``("TRIGGER" | "INDEX", name)`` for a CREATE statement."""
    match = _NAME.match(statement.strip())
    if match is None:  # pragma: no cover
        raise ValueError(f"not a CREATE TRIGGER/INDEX statement: {statement[:60]}")
    kind = "TRIGGER" if match.group(1).upper() == "TRIGGER" else "INDEX"
    return kind, match.group(2)


TRIGGER_TABLES: frozenset[str] = frozenset(table_of(s) for s in CURRENT_CREATE)


def reapply_for_table(execute: Callable[[str], object], table: str) -> None:
    """Re-create the current triggers/indexes of ``table`` (call after a batch rebuild of it)."""
    for statement in CURRENT_CREATE:
        if table_of(statement) == table:
            kind, name = object_of(statement)
            execute(f"DROP {kind} IF EXISTS {name}")
            execute(statement)
