"""DomainError catalogue, IntegrityError mapping (exception_catalogue §1, §1.7) and safe responses."""

from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.errors import ERROR_CATALOGUE, DomainError, map_integrity_error, register_exception_handlers
from app.models import AuditEvent, CompletionRecord, Course, Credential, CredentialRule, Issuer, Learner, Revocation
from tests.support import (
    BACKEND_DIR,
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
    seed_core,
    seed_with_credential,
    ts,
)

CATALOGUE_DOC = BACKEND_DIR.parent / "docs" / "exception_catalogue.md"


def test_catalogue_matches_documentation() -> None:
    """Every code with a numeric HTTP status in exception_catalogue §1 is mapped identically."""
    section = CATALOGUE_DOC.read_text(encoding="utf-8").split("## 2.")[0]
    documented: dict[str, int] = {}
    for first_cell, http in re.findall(r"^\| (.+?) \| (\d{3}) \|", section, re.M):
        for code in re.findall(r"`([a-z_0-9]+)`", first_cell):
            documented[code] = int(http)
    documented.pop("http_404")  # handled by the existing HTTPException handler
    assert {code: status for code, (status, _) in ERROR_CATALOGUE.items()} == documented


def test_undocumented_codes_are_refused() -> None:
    with pytest.raises(ValueError):
        DomainError("made_up_code")


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/domain")
    def domain() -> None:
        raise DomainError("not_eligible", details={"reasons": ["score_below_minimum"]})

    @app.get("/locked")
    def locked() -> None:
        raise OperationalError("BEGIN IMMEDIATE", {}, sqlite3.OperationalError("database is locked"))

    return TestClient(app), app


def test_domain_error_response_envelope() -> None:
    client, _ = _client()
    response = client.get("/domain")
    assert response.status_code == 422
    assert response.json() == {"error": {
        "code": "not_eligible", "message": "Learner is not eligible for this credential.",
        "details": {"reasons": ["score_below_minimum"]}}}


def test_write_lock_timeout_maps_to_database_busy() -> None:
    client, _ = _client()
    response = client.get("/locked")
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_busy"


def test_integrity_error_response_hides_sql_and_paths(migrated_engine: Engine) -> None:
    seed_with_credential(migrated_engine)
    client, app = _client()

    @app.get("/dup")
    def dup() -> None:
        insert(migrated_engine, Credential, credential_row(status="revoked", revoked_at=ts("2026-05-01 00:00:00")))

    response = client.get("/dup")
    assert response.status_code == 409
    assert response.json() == {"error": {
        "code": "duplicate_credential_id", "message": "A credential with this ID already exists."}}
    body = response.text.lower()
    for leak in ("insert", "select", "sqlite", "constraint", ".db", "credentials.credential_id", "traceback"):
        assert leak not in body


# ------------------------------------------------------------------ §1.7 mapping with real errors ---
def _code_of(action) -> str:  # noqa: ANN001
    with pytest.raises(IntegrityError) as caught:
        action()
    return map_integrity_error(caught.value).code


def test_integrity_mapping_matches_catalogue(migrated_engine: Engine) -> None:
    e = migrated_engine
    seed_with_credential(e)

    def sql(statement: str, **params: object):  # noqa: ANN202
        def run() -> None:
            with e.begin() as conn:
                conn.execute(text(statement), params)
        return run

    cases = {
        "duplicate_credential_id": lambda: insert(e, Credential, credential_row(
            status="revoked", revoked_at=ts("2026-05-01 00:00:00"))),
        "credential_already_issued": lambda: insert(e, Credential, credential_row(credential_id="CRED-0000000000AB")),
        "learner_email_exists": lambda: insert(e, Learner, learner_row(learner_id="LRN-0000009")),
        "learner_external_ref_exists": None,
        "course_code_exists": lambda: insert(e, Course, course_row(course_id="CRS-00009")),
        "completion_attempt_exists": lambda: insert(e, CompletionRecord, completion_row(completion_id="CMP-0000009")),
        "completion_locked": sql("UPDATE completion_records SET score = 99"),
        "rule_version_immutable": sql("UPDATE credential_rules SET min_score = 50"),
        "record_immutable": sql("UPDATE credentials SET issued_at = '2026-03-03 00:00:00'"),
        "validation_error": lambda: insert(e, Credential, credential_row(credential_id="cred-lower")),
    }
    insert(e, Learner, learner_row(learner_id="LRN-0000005", email="e@example.org", external_ref="S-1"))
    cases["learner_external_ref_exists"] = lambda: insert(
        e, Learner, learner_row(learner_id="LRN-0000006", email="f@example.org", external_ref="S-1"))
    for expected, action in cases.items():
        assert _code_of(action) == expected, expected


def test_integrity_mapping_for_rules_revocations_audit_and_issuers(migrated_engine: Engine) -> None:
    e = migrated_engine
    seed_core(e)

    def sql(statement: str):  # noqa: ANN202
        def run() -> None:
            with e.begin() as conn:
                conn.execute(text(statement))
        return run

    # Active-rule uniqueness on a course without completions; effective-date guard on CRS-00001.
    insert(e, Course, course_row(course_id="CRS-00002", course_code="DATA-102"))
    insert(e, CredentialRule, rule_row(rule_id="RUL-00002-V1", course_id="CRS-00002"))
    assert _code_of(lambda: insert(e, CredentialRule, rule_row(
        rule_id="RUL-00002-V2", course_id="CRS-00002", version=2))) == "rule_version_immutable"
    assert _code_of(lambda: insert(e, CredentialRule, rule_row(
        rule_id="RUL-00001-V2", version=2, status="retired", effective_to=ts("2026-06-01 00:00:00")))) == (
        "rule_effective_date_conflict")
    assert _code_of(sql("UPDATE credential_rules SET status='retired', effective_to='2025-01-01 00:00:00' "
                        "WHERE rule_id='RUL-00002-V1'")) == "rule_effective_date_invalid"

    insert(e, Credential, credential_row())
    revoke(e)
    assert _code_of(lambda: insert(e, Revocation, revocation_row(revocation_id="REV-000002"))) == (
        "credential_already_revoked")
    assert _code_of(sql("UPDATE revocations SET reason_code='fraud'")) == "record_immutable"
    assert _code_of(sql("UPDATE credentials SET status='active', revoked_at=NULL")) == "record_immutable"
    assert _code_of(sql("DELETE FROM issuers")) == "record_immutable"
    assert _code_of(lambda: insert(e, Issuer, issuer_row(
        issuer_id="ISS-950", signing_key_id="ISS-950-K1", public_key="PK-950"))) == "validation_error"

    insert(e, AuditEvent, audit_row(1, None, event_type="credential.issued", attempt_ref="ATT-000001"))
    assert _code_of(sql("DELETE FROM audit_events")) == "audit_immutable"
    assert _code_of(lambda: insert(e, AuditEvent, audit_row(
        2, f"{1:064x}", event_type="credential.issue_rejected", attempt_ref="ATT-000001"))) == "duplicate_attempt_ref"
    assert _code_of(lambda: insert(e, AuditEvent, audit_row(2, "f" * 64))) == "database_busy"
    assert _code_of(lambda: insert(e, Credential, credential_row(
        credential_id="CRED-0000000000AC", completion_id="CMP-0009999"))) == "internal_server_error"  # FK
