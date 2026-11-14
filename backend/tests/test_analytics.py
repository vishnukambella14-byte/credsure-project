"""T07: metrics, the four detectors, evaluation against ground truth, API routes."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_clock, get_session_factories
from app.api.routes.analytics import get_ground_truth_root
from app.core.security import Actor, Role
from app.datagen.__main__ import generate
from app.datagen.loader import load_dataset
from app.main import app
from app.models import Revocation, VerificationRequest
from app.services import audit, issuance
from app.services.anomalies import evaluate_profile, run_detectors
from app.services.anomalies.detectors import A1, A2, A3, A4, detect_a1, detect_a2, detect_a3, detect_a4
from app.services.anomalies.evaluation import evaluate, load_labels, scores
from app.services.crypto import KeyStore
from app.services.metrics import summary
from tests.support import execute, insert, read_sessions, revocation_row, scalar, ts, verification_row, write_sessions

AA = Actor("AA-1", Role.ACADEMIC_ADMIN)
NOW = ts("2026-11-14 12:00:00")


# ------------------------------------------------------------------ fixtures ----------------------
def _generated(root: Path, profile: str):  # noqa: ANN202
    return generate(profile, generated_root=root / "generated", ground_truth_root=root / "ground_truth",
                    synthetic_key_dir=root / "keys" / "synthetic", platform_key_dir=root / "keys" / "platform")


def _load(engine: Engine, root: Path, profile: str) -> None:
    load_dataset(profile, write_factory=write_sessions(engine), read_factory=read_sessions(engine),
                 key_store=KeyStore(root / "keys" / "platform", root / "keys" / "synthetic", synthetic_profile=profile),
                 generated_root=root / "generated")


@pytest.fixture(scope="module")
def starter_files(tmp_path_factory) -> Path:  # noqa: ANN001
    root = tmp_path_factory.mktemp("starter_eval")
    _generated(root, "starter")
    return root


@pytest.fixture
def starter_db(migrated_engine: Engine, starter_files: Path) -> Engine:
    _load(migrated_engine, starter_files, "starter")
    return migrated_engine


@pytest.fixture
def issued(issuable: Engine, factories, key_store):  # noqa: ANN001, ANN201
    return issuance.issue_credential(actor=AA, learner_id="LRN-0000001", course_id="CRS-00001",
                                     write_factory=factories.write, read_factory=factories.read,
                                     key_store=key_store, clock=lambda: NOW)


def _ids(findings) -> set[str]:  # noqa: ANN001
    return {f.record_id for f in findings}


def _run(engine: Engine, detector):  # noqa: ANN001, ANN202
    with read_sessions(engine)() as session:
        return detector(session)


# ------------------------------------------------------------------ each detector (unit) ----------
def test_clean_platform_data_has_no_findings(issued, issuable: Engine) -> None:  # noqa: ANN001
    assert _run(issuable, run_detectors) == []


def test_a1_detects_credential_issued_before_completion(issued, issuable: Engine) -> None:  # noqa: ANN001
    execute(issuable, "DROP TRIGGER trg_completion_records_locked")  # simulate a faulty legacy system
    execute(issuable, "UPDATE completion_records SET completed_at = '2026-12-01 00:00:00', "
                      "recorded_at = '2026-12-01 00:00:00' WHERE completion_id = 'CMP-0000001'")
    (finding,) = _run(issuable, detect_a1)
    assert (finding.record_type, finding.record_id, finding.anomaly_type) == (
        "credential", issued.credential.credential_id, A1)
    assert finding.evidence["completed_at"] == "2026-12-01T00:00:00Z"


def test_a1_detects_unfinished_evidence(issued, issuable: Engine) -> None:  # noqa: ANN001
    execute(issuable, "DROP TRIGGER trg_completion_records_locked")
    execute(issuable, "UPDATE completion_records SET status = 'in_progress', completed_at = NULL")
    assert _ids(_run(issuable, detect_a1)) == {issued.credential.credential_id}


def test_a2_detects_rejected_duplicate_attempts_and_overwrites(issued, issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    from app.core.errors import DomainError

    with pytest.raises(DomainError):
        issuance.issue_credential(actor=AA, learner_id="LRN-0000002", course_id="CRS-00001",
                                  credential_id=issued.credential.credential_id.lower(), attempt_ref="ATT-000007",
                                  write_factory=factories.write, read_factory=factories.read,
                                  key_store=key_store, clock=lambda: NOW)
    (attempt,) = _run(issuable, detect_a2)
    assert (attempt.record_type, attempt.record_id, attempt.anomaly_type) == ("issuance_attempt", "ATT-000007", A2)

    execute(issuable, "DROP TRIGGER trg_credentials_immutable")  # an upsert-style overwrite
    execute(issuable, "UPDATE credentials SET payload_hash = :h", {"h": "c" * 64})
    overwritten = [f for f in _run(issuable, detect_a2) if f.record_type == "credential"]
    assert [(f.record_id, f.evidence["check"]) for f in overwritten] == [(issued.credential.credential_id, "overwritten")]


def test_a3_detects_revoked_credential_verified_as_valid(issued, issuable: Engine) -> None:  # noqa: ANN001
    cid = issued.credential.credential_id
    with issuable.begin() as conn:  # atomic revocation, as the platform does it
        conn.execute(Revocation.__table__.insert().values(**revocation_row(credential_id=cid, revoked_at=ts("2026-11-20 00:00:00"))))
        conn.exec_driver_sql("UPDATE credentials SET status='revoked', revoked_at='2026-11-20 00:00:00'")
    insert(issuable, VerificationRequest, verification_row(  # faulty: after revocation, stored valid
        verification_id="VER-0000001", submitted_credential_id=cid, credential_id=cid, requested_at=ts("2026-11-21 00:00:00")))
    insert(issuable, VerificationRequest, verification_row(  # hard negative: before revocation
        verification_id="VER-0000002", submitted_credential_id=cid, credential_id=cid, requested_at=ts("2026-11-19 00:00:00")))
    insert(issuable, VerificationRequest, verification_row(  # hard negative: correctly revoked
        verification_id="VER-0000003", submitted_credential_id=cid, credential_id=cid, is_revoked=True,
        result="revoked", requested_at=ts("2026-11-22 00:00:00")))
    assert _ids(_run(issuable, detect_a3)) == {"VER-0000001"}


def test_a4_detects_undetected_tampering_and_storage_tampering(issued, issuable: Engine) -> None:  # noqa: ANN001
    cid, stored = issued.credential.credential_id, issued.credential.payload_hash
    insert(issuable, VerificationRequest, verification_row(  # faulty: altered payload stored valid
        verification_id="VER-0000001", submitted_credential_id=cid, credential_id=cid, presented_payload_hash="b" * 64))
    insert(issuable, VerificationRequest, verification_row(  # hard negative: intact presentation
        verification_id="VER-0000002", submitted_credential_id=cid, credential_id=cid, presented_payload_hash=stored))
    insert(issuable, VerificationRequest, verification_row(  # hard negative: tampering detected
        verification_id="VER-0000003", submitted_credential_id=cid, credential_id=cid, presented_payload_hash="b" * 64,
        hash_match=False, signature_valid=False, result="tampered"))
    assert _ids(_run(issuable, detect_a4)) == {"VER-0000001"}

    execute(issuable, "DROP TRIGGER trg_credentials_immutable")
    execute(issuable, "UPDATE credentials SET payload_json = replace(payload_json, '82.50', '99.00')")
    sweep = [f for f in _run(issuable, detect_a4) if f.record_type == "credential"]
    assert [(f.record_id, f.evidence["check"]) for f in sweep] == [(cid, "integrity_sweep")]


# ------------------------------------------------------------------ evaluation arithmetic ---------
def test_scores() -> None:
    assert scores(10, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert scores(8, 2, 1) == {"precision": 0.8, "recall": 0.8889, "f1": 0.8421}
    assert scores(0, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}  # nothing to find or flag
    assert scores(0, 3, 0)["precision"] == 0.0 and scores(0, 0, 4)["recall"] == 0.0


def test_evaluate_counts_tp_fp_fn_tn_per_type() -> None:
    from app.services.anomalies import Finding

    findings = [Finding("credential", "C1", A1), Finding("credential", "C2", A1),
                Finding("verification_request", "V1", A3), Finding("credential", "PLATFORM", A1)]
    labels = {("credential", "C1", A1), ("credential", "C3", A1), ("verification_request", "V1", A3)}
    populations = {"credential": {"C1", "C2", "C3", "C4"}, "issuance_attempt": set(),
                   "verification_request": {"V1", "V2"}}
    result = evaluate(findings, labels, populations)
    assert {k: result["by_type"][A1][k] for k in ("tp", "fp", "fn", "tn")} == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert result["by_type"][A1]["precision"] == 0.5 and result["by_type"][A1]["recall"] == 0.5
    assert {k: result["by_type"][A3][k] for k in ("tp", "fp", "fn", "tn")} == {"tp": 1, "fp": 0, "fn": 0, "tn": 1}
    assert {k: result["overall"][k] for k in ("tp", "fp", "fn")} == {"tp": 2, "fp": 1, "fn": 1}
    assert result["false_positives"] == [{"record_type": "credential", "record_id": "C2", "anomaly_type": A1}]
    assert result["false_negatives"] == [{"record_type": "credential", "record_id": "C3", "anomaly_type": A1}]


# ------------------------------------------------------------------ against ground truth ----------
def test_starter_detectors_match_ground_truth_exactly(starter_db: Engine, starter_files: Path) -> None:
    labels = load_labels("starter", starter_files / "ground_truth")
    for kind, detector in ((A1, detect_a1), (A2, detect_a2), (A3, detect_a3), (A4, detect_a4)):
        found = {f.key for f in _run(starter_db, detector)}
        assert found == {label for label in labels if label[2] == kind}, kind


def test_starter_evaluation_meets_the_target(starter_db: Engine, starter_files: Path) -> None:
    truth = starter_files / "ground_truth" / "starter" / "anomaly_labels.csv"
    before = hashlib.sha256(truth.read_bytes()).hexdigest()
    with read_sessions(starter_db)() as session:
        result = evaluate_profile(session, "starter", starter_files / "ground_truth")
    assert result["overall"]["recall"] == 1.0 and result["overall"]["precision"] >= 0.95
    assert all(t["recall"] == 1.0 and t["precision"] >= 0.95 for t in result["by_type"].values())
    assert result["false_positives"] == [] and result["false_negatives"] == []
    assert result["overall"]["tp"] == 9 and result["overall"]["tn"] > 0
    assert hashlib.sha256(truth.read_bytes()).hexdigest() == before  # ground truth is read-only


def test_full_evaluation_within_budget(migrated_engine: Engine, tmp_path: Path) -> None:
    _generated(tmp_path, "full")
    _load(migrated_engine, tmp_path, "full")
    with read_sessions(migrated_engine)() as session:
        started = time.perf_counter()
        result = evaluate_profile(session, "full", tmp_path / "ground_truth")
        elapsed = time.perf_counter() - started
    assert elapsed < 30, f"anomaly detection took {elapsed:.1f}s (NFR-03 budget 30s)"
    assert result["overall"]["recall"] == 1.0 and result["overall"]["precision"] >= 0.95
    assert result["overall"]["tp"] == 250


# ------------------------------------------------------------------ metrics -------------------------
def test_metrics_match_source_counts(starter_db: Engine) -> None:
    with read_sessions(starter_db)() as session:
        body = summary(session, lambda: NOW)
    tables = {"learners": "learners", "courses": "courses", "completions": "completion_records",
              "credentials": "credentials", "verifications": "verification_requests"}
    for key, table in tables.items():
        assert body["counts"][key] == scalar(starter_db, f"SELECT count(*) FROM {table}"), key
    assert body["counts"]["credentials_revoked"] == scalar(starter_db, "SELECT count(*) FROM revocations")
    assert body["counts"]["credentials_active"] + body["counts"]["credentials_revoked"] == body["counts"]["credentials"]
    assert body["counts"]["active_rules"] == scalar(starter_db, "SELECT count(*) FROM credential_rules WHERE status='active'")
    assert sum(body["verifications_by_result"].values()) == body["counts"]["verifications"]
    assert sum(m["count"] for m in body["credentials_issued_by_month"]) == body["counts"]["credentials"]
    rate = body["rates"]["revocation_rate"]
    assert rate["value"] == round(body["counts"]["credentials_revoked"] / body["counts"]["credentials"], 4)
    assert (rate["numerator"], rate["denominator"]) == ("revoked credentials", "all credentials")
    completed = scalar(starter_db, "SELECT count(*) FROM completion_records WHERE status='completed'")
    assert body["rates"]["issuance_conversion_rate"]["value"] == round(body["counts"]["credentials"] / completed, 4)


def test_metrics_on_an_empty_database(migrated_engine: Engine) -> None:
    with read_sessions(migrated_engine)() as session:
        body = summary(session, lambda: NOW)
    assert body["counts"]["credentials"] == 0 and body["rates"]["revocation_rate"]["value"] == 0.0
    assert body["credentials_issued_by_month"] == []


# ------------------------------------------------------------------ API -----------------------------
@pytest.fixture
def analytics_api(starter_db: Engine, starter_files: Path, monkeypatch) -> TestClient:  # noqa: ANN001
    from app.api.deps import SessionFactories

    factories = SessionFactories(read=read_sessions(starter_db), write=write_sessions(starter_db))
    monkeypatch.setattr(audit, "WriteSessionLocal", factories.write)
    app.dependency_overrides[get_session_factories] = lambda: factories
    app.dependency_overrides[get_clock] = lambda: (lambda: NOW)
    app.dependency_overrides[get_ground_truth_root] = lambda: starter_files / "ground_truth"
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


PA = {"X-Actor-Id": "PA-1", "X-Actor-Role": "platform_admin"}
AAH = {"X-Actor-Id": "AA-1", "X-Actor-Role": "academic_admin"}
SS = {"X-Actor-Id": "SUP-1", "X-Actor-Role": "support_staff"}


def test_metrics_route(analytics_api: TestClient) -> None:
    body = analytics_api.get("/api/v1/metrics/summary", headers=SS).json()
    assert set(body) == {"computed_at", "counts", "rates", "verifications_by_result", "credentials_issued_by_month"}
    assert body["computed_at"] == "2026-11-14T12:00:00Z" and body["counts"]["credentials"] == 100
    assert set(body["rates"]) == {"revocation_rate", "verification_failure_rate", "issuance_conversion_rate"}
    assert analytics_api.get("/api/v1/metrics/summary",
                             headers={"X-Actor-Id": "LRN-0000001", "X-Actor-Role": "learner"}).status_code == 403


def test_anomaly_run_route(analytics_api: TestClient, starter_db: Engine) -> None:
    assert analytics_api.post("/api/v1/anomalies/run", headers=AAH).status_code == 403
    body = analytics_api.post("/api/v1/anomalies/run", headers=PA).json()
    assert body["findings_by_type"] == {A1: 3, A2: 2, A3: 2, A4: 2} and len(body["findings"]) == 9
    assert set(body["findings"][0]) == {"record_type", "record_id", "anomaly_type", "evidence"}
    assert scalar(starter_db, "SELECT event_type FROM audit_events ORDER BY event_id DESC LIMIT 1") == "anomaly.run_completed"


def test_evaluation_route(analytics_api: TestClient, starter_files: Path) -> None:
    body = analytics_api.get("/api/v1/anomalies/evaluation", params={"profile": "starter"}, headers=AAH).json()
    assert set(body) == {"profile", "by_type", "overall", "false_positives", "false_negatives"}
    assert body["overall"]["recall"] == 1.0 and body["overall"]["precision"] == 1.0
    assert set(body["by_type"][A1]) == {"tp", "fp", "fn", "tn", "precision", "recall", "f1"}
    missing = analytics_api.get("/api/v1/anomalies/evaluation", params={"profile": "full"}, headers=PA)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "ground_truth_unavailable")
    assert analytics_api.get("/api/v1/anomalies/evaluation", params={"profile": "huge"}, headers=PA).status_code == 422
    assert analytics_api.get("/api/v1/anomalies/evaluation", params={"profile": "starter"}, headers=SS).status_code == 403
