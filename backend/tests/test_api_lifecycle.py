"""T04 HTTP API: status/error mapping, roles and ownership, response shapes (api_contract §3–§4)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.models import CredentialRule
from tests.support import execute, insert, rule_row, scalar

AA = {"X-Actor-Id": "AA-1", "X-Actor-Role": "academic_admin"}
SS = {"X-Actor-Id": "SUP-1", "X-Actor-Role": "support_staff"}
PA = {"X-Actor-Id": "PA-1", "X-Actor-Role": "platform_admin"}
L1 = {"X-Actor-Id": "LRN-0000001", "X-Actor-Role": "learner"}
L2 = {"X-Actor-Id": "LRN-0000002", "X-Actor-Role": "learner"}
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
V1 = "/api/v1"

ISSUED_KEYS = {"credential_id", "learner_id", "course_id", "completion_id", "rule_id", "issuer_id",
               "credential_type", "status", "issued_at", "expires_at", "payload", "payload_hash",
               "signature", "signing_key_id", "attempt_ref"}
VERIFY_KEYS = {"verification_id", "credential_id", "result", "checks", "message", "credential", "requested_at"}


def issue(api: TestClient, **body) -> dict:  # noqa: ANN003
    response = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000001", "course_id": "CRS-00001", **body},
                        headers=AA)
    assert response.status_code == 201, response.text
    return response.json()


def denied_events(engine: Engine) -> int:
    return scalar(engine, "SELECT count(*) FROM audit_events WHERE event_type='access.denied'")


# ------------------------------------------------------------------ issuance ---------------------
def test_issue_returns_documented_body(api: TestClient) -> None:
    body = issue(api)
    assert set(body) == ISSUED_KEYS
    assert body["status"] == "active" and TS.match(body["issued_at"]) and body["expires_at"] is None
    assert body["payload"]["score"] == "82.50" and body["payload"]["credential_id"] == body["credential_id"]
    assert len(body["payload_hash"]) == 64 and len(body["signature"]) == 88


def test_issue_error_mapping(api: TestClient, issuable: Engine) -> None:
    not_eligible = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000002", "course_id": "CRS-00001"},
                            headers=AA)
    assert not_eligible.status_code == 422
    error = not_eligible.json()["error"]
    assert error["code"] == "not_eligible" and error["details"]["reasons"] == ["no_completed_record"]
    assert error["details"]["attempt_ref"].startswith("ATT-")

    first = issue(api)
    again = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000001", "course_id": "CRS-00001"}, headers=AA)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "credential_already_issued")

    dup = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000002", "course_id": "CRS-00001",
                                              "credential_id": first["credential_id"].lower()}, headers=AA)
    assert (dup.status_code, dup.json()["error"]["code"]) == (409, "duplicate_credential_id")

    reused = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000002", "course_id": "CRS-00001",
                                                 "attempt_ref": first["attempt_ref"]}, headers=AA)
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "duplicate_attempt_ref")

    bad_ref = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000002", "course_id": "CRS-00001",
                                                  "attempt_ref": "attempt-1"}, headers=AA)
    assert (bad_ref.status_code, bad_ref.json()["error"]["code"]) == (422, "validation_error")


def test_issue_requires_actor_and_role(api: TestClient, issuable: Engine) -> None:
    missing = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000001", "course_id": "CRS-00001"})
    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "actor_required")
    for headers in (PA, SS, L1):
        response = api.post(f"{V1}/credentials", json={"learner_id": "LRN-0000001", "course_id": "CRS-00001"},
                            headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "permission_denied")
    assert denied_events(issuable) == 3
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 0


# ------------------------------------------------------------------ retrieval and ownership -------
def test_credential_detail_and_ownership(api: TestClient, issuable: Engine) -> None:
    cid = issue(api)["credential_id"]
    detail = api.get(f"{V1}/credentials/{cid}", headers=AA).json()
    assert detail["revocation"] is None and detail["payload"]["learner_id"] == "LRN-0000001"
    assert set(detail) >= {"credential_id", "payload", "signature", "payload_hash", "revocation", "status"}
    assert api.get(f"{V1}/credentials/{cid}", headers=L1).status_code == 200
    other = api.get(f"{V1}/credentials/{cid}", headers=L2)
    assert (other.status_code, other.json()["error"]["code"]) == (403, "permission_denied")
    assert denied_events(issuable) == 1
    missing = api.get(f"{V1}/credentials/CRED-0000000000ZZ", headers=AA)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "credential_not_found")


def test_instructor_course_scope(api: TestClient, issuable: Engine) -> None:
    cid = issue(api)["credential_id"]
    ins7 = {"X-Actor-Id": "INS-007", "X-Actor-Role": "instructor"}
    assert api.get(f"{V1}/credentials/{cid}", headers=ins7).status_code == 200  # no lead set (AS-10)
    execute(issuable, "UPDATE courses SET lead_instructor_id='INS-008'")
    scoped = api.get(f"{V1}/credentials/{cid}", headers=ins7)
    assert (scoped.status_code, scoped.json()["error"]["code"]) == (403, "course_scope_denied")
    assert api.get(f"{V1}/credentials", headers=ins7).json()["total"] == 0
    assert api.get(f"{V1}/credentials", headers={**ins7, "X-Actor-Id": "INS-008"}).json()["total"] == 1


def test_list_credentials_filters_pagination_and_learner_scope(api: TestClient) -> None:
    issue(api)
    page = api.get(f"{V1}/credentials", headers=AA, params={"status": "active", "limit": 10}).json()
    assert set(page) == {"items", "total", "limit", "offset"} and page["total"] == 1 and page["limit"] == 10
    assert api.get(f"{V1}/credentials", headers=AA, params={"status": "revoked"}).json()["total"] == 0
    assert api.get(f"{V1}/credentials", headers=L1).json()["total"] == 1
    assert api.get(f"{V1}/credentials", headers=L2).json()["total"] == 0
    assert api.get(f"{V1}/credentials", headers=L2, params={"learner_id": "LRN-0000001"}).status_code == 403
    assert api.get(f"{V1}/credentials", headers=AA, params={"limit": 500}).status_code == 422


def test_share_bundle(api: TestClient) -> None:
    issued = issue(api)
    bundle = api.get(f"{V1}/credentials/{issued['credential_id']}/share", headers=L1)
    assert bundle.status_code == 200
    assert bundle.json() == {"payload": issued["payload"], "payload_hash": issued["payload_hash"],
                             "signature": issued["signature"], "issuer_id": "ISS-001", "signing_key_id": "ISS-001-K1"}
    ins = {"X-Actor-Id": "INS-007", "X-Actor-Role": "instructor"}
    assert api.get(f"{V1}/credentials/{issued['credential_id']}/share", headers=ins).status_code == 403


# ------------------------------------------------------------------ verification -----------------
def test_public_verification(api: TestClient) -> None:
    issued = issue(api)
    response = api.post(f"{V1}/verifications", json={
        "credential_id": issued["credential_id"], "verifier": {"name": "Jordan Lee", "organization": "Acme"},
        "purpose": "employment"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == VERIFY_KEYS and body["result"] == "valid" and TS.match(body["requested_at"])
    assert body["checks"] == {"found": True, "hash_match": True, "signature_valid": True,
                              "is_revoked": False, "is_expired": False}
    assert set(body["credential"]) == {"credential_type", "course_title", "learner_name", "issuer_name",
                                       "issued_at", "expires_at"}
    text = response.text
    for secret in ("PRIVATE KEY", issued["signature"], issued["payload_hash"], "academic_admin", "LRN-0000001"):
        assert secret not in text

    tampered = api.post(f"{V1}/verifications", json={
        "credential_id": issued["credential_id"], "verifier": {"name": "X"},
        "payload": {**issued["payload"], "score": "99.00"}, "signature": issued["signature"]}).json()
    assert tampered["result"] == "tampered" and tampered["credential"] is None

    unknown = api.post(f"{V1}/verifications", json={"credential_id": "nope", "verifier": {"name": "X"}}).json()
    assert unknown["result"] == "not_found" and unknown["credential_id"] is None
    assert api.post(f"{V1}/verifications", json={"credential_id": "x"}).status_code == 422  # verifier required


def test_verification_history_and_detail(api: TestClient) -> None:
    issued = issue(api)
    api.post(f"{V1}/verifications", json={"credential_id": issued["credential_id"], "verifier": {"name": "V"}})
    history = api.get(f"{V1}/verifications", headers=AA).json()
    assert history["total"] == 1 and history["items"][0]["channel"] == "api"
    assert api.get(f"{V1}/verifications", headers=L1).json()["total"] == 1
    assert api.get(f"{V1}/verifications", headers=L2).json()["total"] == 0
    vid = history["items"][0]["verification_id"]
    assert api.get(f"{V1}/verifications/{vid}", headers=SS).status_code == 200
    assert api.get(f"{V1}/verifications/{vid}", headers=L1).status_code == 403
    missing = api.get(f"{V1}/verifications/VER-9999999", headers=AA)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "http_404")


# ------------------------------------------------------------------ revocation -------------------
def test_revoke_api(api: TestClient) -> None:
    cid = issue(api)["credential_id"]
    denied = api.post(f"{V1}/credentials/{cid}/revoke", json={"reason_code": "fraud"}, headers=SS)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    bad = api.post(f"{V1}/credentials/{cid}/revoke", json={"reason_code": "other"}, headers=AA)
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "revocation_reason_required")

    ok = api.post(f"{V1}/credentials/{cid}/revoke",
                  json={"reason_code": "academic_misconduct", "reason_text": "Panel", "requested_by": "SUP-3"},
                  headers=AA)
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"credential", "revocation"} and body["credential"]["status"] == "revoked"
    assert body["revocation"]["revocation_id"] == "REV-000001" and TS.match(body["revocation"]["revoked_at"])
    again = api.post(f"{V1}/credentials/{cid}/revoke", json={"reason_code": "fraud"}, headers=AA)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "credential_already_revoked")

    result = api.post(f"{V1}/verifications", json={"credential_id": cid, "verifier": {"name": "V"}}).json()
    assert result["result"] == "revoked"
    assert api.get(f"{V1}/revocations", headers=PA).json()["total"] == 1
    assert api.get(f"{V1}/credentials/{cid}", headers=AA).json()["revocation"]["reason_code"] == "academic_misconduct"
    assert api.post(f"{V1}/credentials/{cid}/reinstate", headers=AA).status_code in (404, 405)


# ------------------------------------------------------------------ eligibility ------------------
def test_eligibility_api(api: TestClient, issuable: Engine) -> None:
    body = api.get(f"{V1}/eligibility", params={"learner_id": "LRN-0000001", "course_id": "CRS-00001"},
                   headers=AA).json()
    assert set(body) == {"learner_id", "course_id", "eligible", "rule_id", "rule_version", "completion_id",
                         "reasons", "checks", "evaluated_at"}
    assert body["eligible"] and body["rule_id"] == "RUL-00001-V1" and body["evaluated_at"] == "2026-11-14T12:00:00Z"
    own = api.get(f"{V1}/eligibility", params={"learner_id": "LRN-0000002", "course_id": "CRS-00001"}, headers=L2)
    assert own.status_code == 200 and own.json()["reasons"] == ["no_completed_record"]
    other = api.get(f"{V1}/eligibility", params={"learner_id": "LRN-0000001", "course_id": "CRS-00001"}, headers=L2)
    assert other.status_code == 403
    assert scalar(issuable, "SELECT count(*) FROM credentials") == 0  # no side effects


# ------------------------------------------------------------------ rule activation ---------------
def test_activate_rule_api(api: TestClient, issuable: Engine) -> None:
    insert(issuable, CredentialRule, rule_row(rule_id="RUL-00001-V2", version=2, status="draft",
                                              effective_from=None, activated_at=None))
    conflict = api.post(f"{V1}/rules/RUL-00001-V2/activate", json={"effective_from": "2026-02-01T00:00:00Z"},
                        headers=AA)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"] == {"conflicting_completion_ids": ["CMP-0000001"]}
    naive = api.post(f"{V1}/rules/RUL-00001-V2/activate", json={"effective_from": "2026-04-01T00:00:00"},
                     headers=AA)
    assert (naive.status_code, naive.json()["error"]["code"]) == (422, "validation_error")
    assert api.post(f"{V1}/rules/RUL-00001-V2/activate", headers=PA).status_code == 403
    ok = api.post(f"{V1}/rules/RUL-00001-V2/activate", json={"effective_from": "2026-04-01T00:00:00Z"}, headers=AA)
    assert ok.status_code == 200
    assert ok.json() == {"rule_id": "RUL-00001-V2", "status": "active", "effective_from": "2026-04-01T00:00:00Z",
                         "activated_at": "2026-11-14T12:00:00Z", "retired_rule_id": "RUL-00001-V1"}


def test_activate_without_body_uses_now(api: TestClient, issuable: Engine) -> None:
    insert(issuable, CredentialRule, rule_row(rule_id="RUL-00001-V2", version=2, status="draft",
                                              effective_from=None, activated_at=None))
    assert api.post(f"{V1}/rules/RUL-00001-V2/activate", headers=AA).json()["effective_from"] == "2026-11-14T12:00:00Z"


# ------------------------------------------------------------------ audit ------------------------
def test_audit_events_and_chain_verification(api: TestClient, issuable: Engine) -> None:
    issued = issue(api)
    page = api.get(f"{V1}/audit-events", headers=SS).json()
    assert page["total"] == 2 and [e["event_type"] for e in page["items"]] == ["credential.issued",
                                                                                "eligibility.evaluated"]
    item = page["items"][0]
    assert item["attempt_ref"] == issued["attempt_ref"] and isinstance(item["details"], dict)
    assert TS.match(item["occurred_at"])
    filtered = api.get(f"{V1}/audit-events", headers=AA,
                       params={"entity_id": issued["credential_id"], "event_type": "credential.issued"}).json()
    assert filtered["total"] == 1
    assert api.get(f"{V1}/audit-events", headers=L1).status_code == 403

    assert api.get(f"{V1}/audit-events/verify-chain", headers=AA).status_code == 403
    chain = api.get(f"{V1}/audit-events/verify-chain", headers=PA)
    assert chain.status_code == 200 and chain.json() == {"valid": True, "events_checked": 4,
                                                         "first_broken_event_id": None}
    # 2 issuance events + 2 access.denied (L1 audit read, AA verify-chain) before the check
    assert scalar(issuable, "SELECT event_type FROM audit_events ORDER BY event_id DESC LIMIT 1") == "audit.chain_verified"


@pytest.mark.parametrize("path", ["/api/health"])
def test_health_unchanged(api: TestClient, path: str) -> None:
    assert api.get(path).json()["status"] == "healthy"
