"""T05 master-data APIs: CRUD (create/retrieve) and rule versioning (roadmap T05)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.services.crypto import sign, verify_signature
from tests.support import execute, scalar

AA = {"X-Actor-Id": "AA-1", "X-Actor-Role": "academic_admin"}
SS = {"X-Actor-Id": "SUP-1", "X-Actor-Role": "support_staff"}
PA = {"X-Actor-Id": "PA-1", "X-Actor-Role": "platform_admin"}
L1 = {"X-Actor-Id": "LRN-0000001", "X-Actor-Role": "learner"}
INS = {"X-Actor-Id": "INS-007", "X-Actor-Role": "instructor"}
V1 = "/api/v1"


def last_event(engine: Engine) -> str:
    return scalar(engine, "SELECT event_type FROM audit_events ORDER BY event_id DESC LIMIT 1")


# ------------------------------------------------------------------ issuers ----------------------
def test_create_issuer_generates_and_separates_keys(api: TestClient, issuable: Engine, key_store) -> None:  # noqa: ANN001
    response = api.post(f"{V1}/issuers", json={"name": "Second College", "legal_name": "Second College Ltd",
                                                "country_code": "GB"}, headers=PA)
    assert response.status_code == 201
    issuer = response.json()
    assert (issuer["issuer_id"], issuer["signing_key_id"], issuer["record_source"], issuer["status"]) == (
        "ISS-002", "ISS-002-K1", "platform", "active")
    assert len(issuer["public_key"]) == 44 and issuer["key_algorithm"] == "Ed25519"
    assert "PRIVATE" not in response.text and "private" not in response.text.lower()
    # D8: platform directory only; synthetic directory untouched; key matches the stored public key
    assert key_store.is_available("ISS-002-K1", "platform")
    assert not key_store.synthetic_dir.exists()
    private_key = key_store.load("ISS-002-K1", "platform")
    assert verify_signature(issuer["public_key"], b"probe", sign(private_key, b"probe"))
    assert last_event(issuable) == "issuer.created"


def test_issuer_retrieval_and_public_key(api: TestClient) -> None:
    listed = api.get(f"{V1}/issuers", headers=SS).json()
    assert listed["total"] == 1 and listed["items"][0]["issuer_id"] == "ISS-001"
    assert api.get(f"{V1}/issuers/ISS-001", headers=AA).json()["signing_key_id"] == "ISS-001-K1"
    public = api.get(f"{V1}/issuers/ISS-001/public-key")  # public: no headers
    assert public.status_code == 200
    assert set(public.json()) == {"issuer_id", "signing_key_id", "algorithm", "public_key"}
    missing = api.get(f"{V1}/issuers/ISS-404", headers=AA)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "issuer_not_found")


def test_issuer_permissions_and_supersession(api: TestClient, issuable: Engine) -> None:
    body = {"name": "X", "legal_name": "X Ltd"}
    for headers in (AA, SS):
        assert api.post(f"{V1}/issuers", json=body, headers=headers).status_code == 403
    assert api.get(f"{V1}/issuers", headers=L1).status_code == 403
    first = api.post(f"{V1}/issuers", json={**body, "supersedes_issuer_id": "ISS-001"}, headers=PA)
    assert first.status_code == 201 and first.json()["supersedes_issuer_id"] == "ISS-001"
    again = api.post(f"{V1}/issuers", json={**body, "supersedes_issuer_id": "ISS-001"}, headers=PA)
    assert (again.status_code, again.json()["error"]["code"]) == (422, "validation_error")
    unknown = api.post(f"{V1}/issuers", json={**body, "supersedes_issuer_id": "ISS-404"}, headers=PA)
    assert unknown.json()["error"]["code"] == "issuer_not_found"


def test_existing_key_file_is_never_overwritten(api: TestClient, issuable: Engine, key_store) -> None:  # noqa: ANN001
    key_store.platform_dir.mkdir(parents=True, exist_ok=True)
    (key_store.platform_dir / "ISS-002-K1.pem").write_bytes(b"pre-existing")
    response = api.post(f"{V1}/issuers", json={"name": "X", "legal_name": "X Ltd"}, headers=PA)
    assert response.status_code == 500
    assert (key_store.platform_dir / "ISS-002-K1.pem").read_bytes() == b"pre-existing"
    assert scalar(issuable, "SELECT count(*) FROM issuers") == 1


# ------------------------------------------------------------------ learners ---------------------
def test_create_and_retrieve_learners(api: TestClient, issuable: Engine) -> None:
    created = api.post(f"{V1}/learners", json={"given_name": "Asha", "family_name": "Rao",
                                                "email": "Asha.Two@Example.org", "external_ref": "S-100"}, headers=AA)
    assert created.status_code == 201
    learner = created.json()
    assert learner["learner_id"] == "LRN-0000003" and learner["email"] == "asha.two@example.org"
    assert last_event(issuable) == "learner.created"
    # BR-01: same name, distinct identity
    assert api.get(f"{V1}/learners", headers=AA, params={"search": "Rao"}).json()["total"] == 2
    dup_email = api.post(f"{V1}/learners", json={"given_name": "A", "family_name": "B",
                                                  "email": "asha.two@example.org"}, headers=AA)
    assert (dup_email.status_code, dup_email.json()["error"]["code"]) == (409, "learner_email_exists")
    dup_ref = api.post(f"{V1}/learners", json={"given_name": "A", "family_name": "B", "email": "c@d.org",
                                                "external_ref": "S-100"}, headers=AA)
    assert dup_ref.json()["error"]["code"] == "learner_external_ref_exists"
    assert api.get(f"{V1}/learners/LRN-0000003", headers=INS).json()["external_ref"] == "S-100"
    assert api.get(f"{V1}/learners/LRN-0009999", headers=AA).json()["error"]["code"] == "learner_not_found"


def test_learner_access_rules(api: TestClient) -> None:
    assert api.get(f"{V1}/learners/LRN-0000001", headers=L1).status_code == 200
    assert api.get(f"{V1}/learners/LRN-0000002", headers=L1).status_code == 403
    assert api.get(f"{V1}/learners", headers=L1).status_code == 403
    assert api.post(f"{V1}/learners", json={"given_name": "A", "family_name": "B", "email": "x@y.org"},
                    headers=SS).status_code == 403
    page = api.get(f"{V1}/learners", headers=SS, params={"status": "active", "limit": 1}).json()
    assert (page["total"], len(page["items"]), page["limit"]) == (2, 1, 1)


# ------------------------------------------------------------------ courses ----------------------
def test_create_and_retrieve_courses(api: TestClient, issuable: Engine) -> None:
    created = api.post(f"{V1}/courses", json={"course_code": "DATA-201", "title": "Data Engineering",
                                               "issuer_id": "ISS-001", "credit_hours": 10}, headers=AA)
    assert created.status_code == 201 and created.json()["course_id"] == "CRS-00002"
    assert last_event(issuable) == "course.created"
    detail = api.get(f"{V1}/courses/CRS-00001", headers=INS).json()
    assert detail["active_rule"]["rule_id"] == "RUL-00001-V1"
    assert api.get(f"{V1}/courses/CRS-00002", headers=SS).json()["active_rule"] is None
    assert api.get(f"{V1}/courses", headers=PA).json()["total"] == 2
    assert api.get(f"{V1}/courses", headers=L1).status_code == 403


def test_course_errors(api: TestClient, issuable: Engine) -> None:
    body = {"course_code": "DATA-101", "title": "T", "issuer_id": "ISS-001"}
    assert api.post(f"{V1}/courses", json=body, headers=AA).json()["error"]["code"] == "course_code_exists"
    missing = api.post(f"{V1}/courses", json={**body, "course_code": "N-1", "issuer_id": "ISS-404"}, headers=AA)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "issuer_not_found")
    execute(issuable, "UPDATE issuers SET status='retired', valid_to='2026-06-01 00:00:00'")
    retired = api.post(f"{V1}/courses", json={**body, "course_code": "N-2"}, headers=AA)
    assert (retired.status_code, retired.json()["error"]["code"]) == (422, "issuer_inactive")
    assert api.get(f"{V1}/courses/CRS-09999", headers=AA).json()["error"]["code"] == "course_not_found"


# ------------------------------------------------------------------ rule versioning --------------
def test_draft_rules_get_next_version_and_reuse_t04_activation(api: TestClient, issuable: Engine) -> None:
    body = {"credential_type": "certificate", "min_score": 75, "description": "Stricter"}
    v2 = api.post(f"{V1}/courses/CRS-00001/rules", json=body, headers=AA)
    assert v2.status_code == 201
    assert (v2.json()["rule_id"], v2.json()["version"], v2.json()["status"], v2.json()["effective_from"]) == (
        "RUL-00001-V2", 2, "draft", None)
    assert last_event(issuable) == "rule.created"
    v3 = api.post(f"{V1}/courses/CRS-00001/rules", json=body, headers=AA).json()
    assert (v3["rule_id"], v3["version"]) == ("RUL-00001-V3", 3)

    activated = api.post(f"{V1}/rules/RUL-00001-V2/activate", json={"effective_from": "2026-04-01T00:00:00Z"},
                         headers=AA)  # the existing T04 endpoint
    assert activated.status_code == 200
    versions = api.get(f"{V1}/courses/CRS-00001/rules", headers=INS).json()
    assert [(r["version"], r["status"]) for r in versions] == [(1, "retired"), (2, "active"), (3, "draft")]
    assert versions[0]["min_score"] == 70.0 and versions[0]["effective_to"] == "2026-04-01T00:00:00Z"
    assert versions[1]["min_score"] == 75.0  # active criteria as created, unchanged


def test_creating_a_draft_never_changes_existing_versions(api: TestClient, issuable: Engine) -> None:
    before = scalar(issuable, "SELECT status || min_score || effective_from FROM credential_rules "
                              "WHERE rule_id='RUL-00001-V1'")
    api.post(f"{V1}/courses/CRS-00001/rules", json={"credential_type": "badge", "min_attendance_pct": 90},
             headers=AA)
    after = scalar(issuable, "SELECT status || min_score || effective_from FROM credential_rules "
                             "WHERE rule_id='RUL-00001-V1'")
    assert before == after


def test_rule_validation_and_permissions(api: TestClient) -> None:
    assert api.post(f"{V1}/courses/CRS-00001/rules", json={"credential_type": "certificate", "min_score": 120},
                    headers=AA).status_code == 422
    assert api.post(f"{V1}/courses/CRS-00001/rules", json={"credential_type": "diploma"},
                    headers=AA).status_code == 422
    assert api.post(f"{V1}/courses/CRS-09999/rules", json={"credential_type": "badge"},
                    headers=AA).json()["error"]["code"] == "course_not_found"
    assert api.post(f"{V1}/courses/CRS-00001/rules", json={"credential_type": "badge"},
                    headers=PA).status_code == 403
    assert api.get(f"{V1}/courses/CRS-00001/rules", headers=L1).status_code == 403


# ------------------------------------------------------------------ completions ------------------
def _completion(**overrides) -> dict:  # noqa: ANN003
    return {"learner_id": "LRN-0000002", "course_id": "CRS-00001", "attempt_number": 1,
            "status": "completed", "score": 81.234, "attendance_pct": 90, "completed_at": "2026-05-01T10:00:00Z",
            **overrides}


def test_record_completion_and_eligibility_follows(api: TestClient, issuable: Engine) -> None:
    created = api.post(f"{V1}/completions", json=_completion(), headers=INS)
    assert created.status_code == 201
    body = created.json()
    assert (body["completion_id"], body["applicable_rule_id"], body["warnings"]) == ("CMP-0000002", "RUL-00001-V1", [])
    assert body["score"] == 81.23 and body["assessed_by"] == "INS-007"
    assert body["recorded_at"] == "2026-11-14T12:00:00Z"
    assert last_event(issuable) == "completion.recorded"
    eligibility = api.get(f"{V1}/eligibility", params={"learner_id": "LRN-0000002", "course_id": "CRS-00001"},
                          headers=AA).json()
    assert eligibility["eligible"] and eligibility["completion_id"] == "CMP-0000002"


def test_completion_without_applicable_rule_is_recorded_with_warning(api: TestClient) -> None:
    body = api.post(f"{V1}/completions", json=_completion(completed_at="2025-12-01T10:00:00Z"), headers=AA).json()
    assert (body["applicable_rule_id"], body["warnings"]) == (None, ["no_applicable_rule"])


def test_completion_errors(api: TestClient) -> None:
    cases = [
        (_completion(completed_at=None), 422, "completion_invalid_state"),
        (_completion(score=101), 422, "completion_invalid_state"),
        (_completion(completed_at="2026-12-01T00:00:00Z"), 422, "completion_in_future"),
        (_completion(learner_id="LRN-0009999"), 404, "learner_not_found"),
        (_completion(course_id="CRS-09999"), 404, "course_not_found"),
        (_completion(learner_id="LRN-0000001"), 409, "completion_attempt_exists"),
        (_completion(completed_at="2026-05-01T10:00:00"), 422, "validation_error"),  # naive timestamp
    ]
    for body, status_code, code in cases:
        response = api.post(f"{V1}/completions", json=body, headers=AA)
        assert (response.status_code, response.json()["error"]["code"]) == (status_code, code), body
    in_progress = api.post(f"{V1}/completions", json=_completion(status="in_progress", completed_at=None),
                           headers=AA).json()
    assert (in_progress["applicable_rule_id"], in_progress["warnings"]) == (None, [])


def test_completion_scopes(api: TestClient, issuable: Engine) -> None:
    assert api.post(f"{V1}/completions", json=_completion(), headers=L1).status_code == 403
    execute(issuable, "UPDATE courses SET lead_instructor_id='INS-008'")
    scoped = api.post(f"{V1}/completions", json=_completion(), headers=INS)
    assert (scoped.status_code, scoped.json()["error"]["code"]) == (403, "course_scope_denied")
    assert scalar(issuable, "SELECT count(*) FROM completion_records") == 1
    lead = {**INS, "X-Actor-Id": "INS-008"}
    assert api.post(f"{V1}/completions", json=_completion(), headers=lead).status_code == 201

    assert api.get(f"{V1}/completions", headers=L1).json()["total"] == 1  # own only
    assert api.get(f"{V1}/completions", headers=L1, params={"learner_id": "LRN-0000002"}).status_code == 403
    assert api.get(f"{V1}/completions", headers=INS).json()["total"] == 0  # not their course
    assert api.get(f"{V1}/completions", headers=lead).json()["total"] == 2
    listed = api.get(f"{V1}/completions", headers=SS, params={"course_id": "CRS-00001"}).json()
    assert listed["total"] == 2 and listed["items"][0]["applicable_rule_id"] == "RUL-00001-V1"
