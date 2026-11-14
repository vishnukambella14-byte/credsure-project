"""Anomaly injection A1–A4 (exception_catalogue §3). Only the four documented anomalies.

``inject`` works on a deep copy of the valid dataset and mutates the records reserved for it by the
generator. Every mutation is labelled in the ground truth, which is returned separately and never
written into an input file (BR-24).

* A1 issued before completion: the evidence completion is moved after ``issued_at`` (and
  ``recorded_at`` after it, for ck_completion_records_recorded_after), or set to an unfinished
  status; the payload is rebuilt and re-signed so A1 is not also an A4.
* A2 duplicate credential ID attempt: issuance attempts that reuse an existing credential ID
  (exact, lower-case variant, or same learner retry). The loader replays them; the platform must
  reject each with ``duplicate_credential_id``.
* A3 revoked credential verified as valid: verifications made after ``revoked_at`` whose stored
  result is ``valid`` with ``is_revoked = 0``.
* A4 altered payload not detected: verifications whose ``presented_payload_hash`` is the hash of a
  modified payload but whose stored result is ``valid`` with ``hash_match = 1``.

A3/A4 flags stay consistent with ck_verification_requests_result_precedence: the faulty system
recorded wrong *flags*, which is exactly what T07 detectors recompute.
"""

from __future__ import annotations

import random
from datetime import timedelta

from app.datagen import config as cfg
from app.datagen.generator import Dataset, presented_hash, sign_payload, tamper


def _label(labels: list[dict], record_type: str, record_id: str, anomaly: str, variant: str, text: str) -> None:
    labels.append({"label_id": f"GT-{len(labels) + 1:06d}", "record_type": record_type, "record_id": record_id,
                   "anomaly_type": anomaly, "variant": variant, "description": text})


def _a1(ds: Dataset, rng: random.Random, labels: list[dict]) -> None:
    variants = cfg.VARIANTS[cfg.A1]
    for n, credential_id in enumerate(ds.reserved["a1"]):
        variant = variants[n % len(variants)]
        credential = ds.lookup("credentials", "credential_id", credential_id)
        completion = ds.lookup("completions", "completion_id", credential["completion_id"])
        if variant == "issued_before_completed_at":
            completed_at = credential["issued_at"] + timedelta(days=rng.randint(1, 30), seconds=rng.randint(0, 86399))
            completion.update(completed_at=completed_at,
                              recorded_at=completed_at + timedelta(seconds=rng.randint(0, 2 * 86400)))
            text = "Credential issued before the evidence completion's completed_at."
        else:
            status = rng.choice(("in_progress", "failed"))
            completion.update(status=status, completed_at=None, score=completion["score"] if status == "failed" else None)
            text = f"Evidence completion has status {status}; the learner had not completed the course."
        sign_payload(ds, credential, completion)  # re-signed: integrity stays intact, only timing is wrong
        _label(labels, "credential", credential_id, cfg.A1, variant, text)


def _a2(ds: Dataset, profile: cfg.Profile, rng: random.Random, labels: list[dict]) -> None:
    variants = cfg.VARIANTS[cfg.A2]
    originals = rng.sample(ds.credentials, profile.a2)
    next_number = len(ds.attempts) + 1
    for n, original in enumerate(originals):
        variant = variants[n % len(variants)]
        if variant == "same_learner_retry":
            learner_id, course_id, credential_id = original["learner_id"], original["course_id"], original["credential_id"]
        else:
            other = rng.choice([c for c in ds.credentials if c["learner_id"] != original["learner_id"]])
            learner_id, course_id = other["learner_id"], other["course_id"]
            credential_id = original["credential_id"].lower() if variant == "case_variant" else original["credential_id"]
        attempt_id = f"ATT-{next_number + n:06d}"
        ds.attempts.append({
            "attempt_id": attempt_id, "credential_id": credential_id, "learner_id": learner_id,
            "course_id": course_id,
            "attempted_at": cfg.REFERENCE - timedelta(days=rng.randint(1, 20), seconds=rng.randint(0, 86399)),
            "actor_id": cfg.ADMIN_ACTOR,
        })
        _label(labels, "issuance_attempt", attempt_id, cfg.A2, variant,
               f"Reuses existing credential ID {original['credential_id']}.")


def _a3(ds: Dataset, labels: list[dict]) -> None:
    variants = cfg.VARIANTS[cfg.A3]
    for n, verification_id in enumerate(ds.reserved["a3_pool"]):
        row = ds.lookup("verifications", "verification_id", verification_id)
        row.update(is_revoked=False, result="valid")
        _label(labels, "verification_request", verification_id, cfg.A3, variants[n % len(variants)],
               "Verified after revoked_at but recorded as valid.")


def _a4(ds: Dataset, labels: list[dict]) -> None:
    variants = cfg.VARIANTS[cfg.A4]
    for n, verification_id in enumerate(ds.reserved["a4_pool"]):
        variant = variants[n % len(variants)]
        row = ds.lookup("verifications", "verification_id", verification_id)
        altered = tamper(ds.payloads[row["credential_id"]], variant)
        row.update(presented_payload_hash=presented_hash(altered), hash_match=True, signature_valid=True,
                   result="valid")
        _label(labels, "verification_request", verification_id, cfg.A4, variant,
               "Presented payload differs from the signed payload but was recorded as valid.")


def inject(valid: Dataset, profile: cfg.Profile, rng: random.Random) -> tuple[Dataset, list[dict]]:
    """Return a mutated copy of ``valid`` plus its ground-truth labels (A1, A2, A3, A4 order)."""
    ds = valid.copy()
    labels: list[dict] = []
    _a1(ds, rng, labels)
    _a2(ds, profile, rng, labels)
    _a3(ds, labels)
    _a4(ds, labels)
    return ds, labels
