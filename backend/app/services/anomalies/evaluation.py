"""Evaluation of detector findings against ground truth (FR-22, exception_catalogue §4, BR-24).

Steps: run all detectors; load ``data/ground_truth/<profile>/anomaly_labels.csv``; compare the
``(record_type, record_id, anomaly_type)`` sets per anomaly type.

* Only synthetic records are scored (SR-36): findings on platform-created records cannot be in a
  profile's ground truth. Synthetic records are ``record_source = 'synthetic_import'`` rows, and
  attempts whose reference has the dataset format ``ATT-nnnnnn``.
* TN = records of the anomaly's record type in the scored population that are neither labelled
  nor flagged. Populations: credentials (A1), issuance attempts recorded in the audit log (A2),
  verification requests (A3, A4).
* ``precision = TP/(TP+FP)``, ``recall = TP/(TP+FN)``, ``F1 = 2PR/(P+R)``. A ratio whose
  denominator is 0 is reported as 1.0 when there is nothing to find or flag, otherwise 0.0.
* Ground truth is read only here, never by the detectors, and never loaded into the database.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models import AuditEvent, Credential, VerificationRequest
from app.models.enums import TERMINAL_ISSUANCE_EVENTS, RecordSource
from app.services.anomalies.detectors import A1, A2, A3, A4, ANOMALY_TYPES, Finding, run_detectors
from app.services.ids import SYNTHETIC_ATTEMPT_REF_RE

LIST_CAP = 50
SYNTHETIC = RecordSource.SYNTHETIC_IMPORT.value
PROFILES = ("starter", "full")
_RECORD_TYPE = {A1: "credential", A2: "issuance_attempt", A3: "verification_request", A4: "verification_request"}


def load_labels(profile: str, ground_truth_root: Path) -> set[tuple[str, str, str]]:
    if profile not in PROFILES:
        raise DomainError("validation_error", "profile must be 'starter' or 'full'.")
    path = ground_truth_root / profile / "anomaly_labels.csv"
    if not path.is_file():
        raise DomainError("ground_truth_unavailable", details={"profile": profile})
    with path.open(encoding="utf-8", newline="") as handle:
        return {(row["record_type"], row["record_id"], row["anomaly_type"]) for row in csv.DictReader(handle)}


def _populations(session: Session) -> dict[str, set[str]]:
    credentials = set(session.execute(select(Credential.credential_id)
                                      .where(Credential.record_source == SYNTHETIC)).scalars())
    verifications = set(session.execute(select(VerificationRequest.verification_id)
                                        .where(VerificationRequest.record_source == SYNTHETIC)).scalars())
    attempts = {ref for ref in session.execute(select(AuditEvent.attempt_ref)
                                               .where(AuditEvent.event_type.in_(TERMINAL_ISSUANCE_EVENTS))).scalars()
                if ref and SYNTHETIC_ATTEMPT_REF_RE.match(ref)}
    return {"credential": credentials, "issuance_attempt": attempts, "verification_request": verifications}


def _ratio(numerator: int, denominator: int, nothing_expected: bool) -> float:
    if denominator == 0:
        return 1.0 if nothing_expected else 0.0
    return round(numerator / denominator, 4)


def scores(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = _ratio(tp, tp + fp, nothing_expected=fn == 0)
    recall = _ratio(tp, tp + fn, nothing_expected=fp == 0)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def evaluate(findings: list[Finding], labels: set[tuple[str, str, str]],
             populations: dict[str, set[str]]) -> dict[str, Any]:
    scored = {f.key for f in findings if f.record_id in populations.get(f.record_type, set())}
    by_type: dict[str, dict[str, Any]] = {}
    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    false_positives: list[dict[str, str]] = []
    false_negatives: list[dict[str, str]] = []
    for kind in ANOMALY_TYPES:
        predicted = {key for key in scored if key[2] == kind}
        expected = {key for key in labels if key[2] == kind}
        tp, fp, fn = len(predicted & expected), len(predicted - expected), len(expected - predicted)
        population = {(_RECORD_TYPE[kind], rid, kind) for rid in populations[_RECORD_TYPE[kind]]}
        tn = len(population - predicted - expected)
        by_type[kind] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, **scores(tp, fp, fn)}
        for name, value in (("tp", tp), ("fp", fp), ("fn", fn), ("tn", tn)):
            totals[name] += value
        false_positives += [dict(zip(("record_type", "record_id", "anomaly_type"), k)) for k in sorted(predicted - expected)]
        false_negatives += [dict(zip(("record_type", "record_id", "anomaly_type"), k)) for k in sorted(expected - predicted)]
    return {
        "by_type": by_type,
        "overall": {**totals, **scores(totals["tp"], totals["fp"], totals["fn"])},
        "false_positives": false_positives[:LIST_CAP],
        "false_negatives": false_negatives[:LIST_CAP],
    }


def evaluate_profile(session: Session, profile: str, ground_truth_root: Path) -> dict[str, Any]:
    """``GET /api/v1/anomalies/evaluation?profile=``: detectors vs the profile's ground truth."""
    labels = load_labels(profile, ground_truth_root)
    result = evaluate(run_detectors(session), labels, _populations(session))
    return {"profile": profile, **result}
