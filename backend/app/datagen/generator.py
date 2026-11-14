"""Valid-first synthetic dataset generation, CSV/manifest writing and synthetic key storage.

One ``random.Random(seed)`` drives every choice, in a fixed order, and all timestamps derive from
``config.REFERENCE`` / ``DATASET_START`` (BR-25). The generator first builds a **fully valid**
dataset (``generate_valid``): every credential satisfies BR-04 at its ``issued_at`` and every
verification result is the true BR-13 outcome. ``injector.inject`` then mutates copies of
selected records (A1–A4). Payloads are built with the platform's ``issuance.build_payload`` and
canonicalised/signed with the platform's crypto, using deterministic synthetic Ed25519 keys
derived from the seed (forgeable by design, development only; ADR-024).
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.timeutil import format_utc
from app.datagen import config as cfg
from app.models import CompletionRecord, Course, Credential, CredentialRule, Issuer, Learner, Revocation, VerificationRequest
from app.services.crypto import canonical_json, private_key_to_pem, public_key_b64, sha256_hex, sign
from app.services.eligibility import EligibilityResult
from app.services.ids import CROCKFORD_ALPHABET
from app.services.issuance import build_payload
from app.services.verification import decide_result

GIVEN = ("Asha", "Ben", "Chen", "Dara", "Elif", "Femi", "Gita", "Hugo", "Ines", "Jonas", "Kofi", "Lena",
         "Mateo", "Nia", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Tara", "Uma", "Victor", "Wen", "Yara")
FAMILY = ("Rao", "Okafor", "Smith", "Nguyen", "Garcia", "Kowalski", "Haddad", "Ivanova", "Mensah",
          "Tanaka", "Silva", "Brown", "Ali", "Moreau", "Jensen", "Costa", "Patel", "Novak")
SUBJECTS = ("Data Foundations", "Applied Statistics", "Cloud Computing", "Project Management",
            "Cyber Security", "Machine Learning", "Digital Marketing", "Software Testing",
            "Financial Analysis", "UX Research", "Database Design", "Network Engineering")
LEVELS = ("Introduction to", "Intermediate", "Advanced", "Professional")
DEPTS = ("DATA", "STAT", "CLOUD", "PMGT", "SEC", "ML", "MKT", "QA", "FIN", "UX", "DB", "NET")
ORGS = ("Northwind Hiring", "Contoso Talent", "Fabrikam Admissions", "Example Licensing Board", None)
REASONS = ("issued_in_error", "academic_misconduct", "learner_request", "data_correction", "fraud", "other")


# ------------------------------------------------------------------ dataset container ------------
@dataclass
class Dataset:
    profile: str
    issuers: list[dict] = field(default_factory=list)
    learners: list[dict] = field(default_factory=list)
    courses: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    completions: list[dict] = field(default_factory=list)
    credentials: list[dict] = field(default_factory=list)
    revocations: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    # Not written: signing keys, payload objects and the records reserved for injection.
    keys: dict[str, Ed25519PrivateKey] = field(default_factory=dict, repr=False)
    payloads: dict[str, dict] = field(default_factory=dict, repr=False)
    reserved: dict[str, list[str]] = field(default_factory=dict)
    _index: dict = field(default_factory=dict, repr=False, compare=False)

    def lookup(self, table: str, key: str, value: str) -> dict:
        """Row of ``table`` whose ``key`` equals ``value`` (index rebuilt when the table grows)."""
        rows = getattr(self, table)
        cached = self._index.get((table, key))
        if cached is None or cached[0] != len(rows):
            cached = (len(rows), {row[key]: row for row in rows})
            self._index[(table, key)] = cached
        return cached[1][value]

    def rule_for(self, course_id: str, moment: datetime) -> dict | None:
        """D5 selection over this dataset's rules (same windows as eligibility.applicable_rule)."""
        cached = self._index.get(("rules_by_course", len(self.rules)))
        if cached is None:
            cached = {}
            for rule in self.rules:
                cached.setdefault(rule["course_id"], []).append(rule)
            self._index[("rules_by_course", len(self.rules))] = cached
        return rule_for(cached.get(course_id, []), course_id, moment)

    def tables(self) -> dict[str, list[dict]]:
        return {"issuers.csv": self.issuers, "learners.csv": self.learners, "courses.csv": self.courses,
                "credential_rules.csv": self.rules, "completion_records.csv": self.completions,
                "credentials.csv": self.credentials, "revocations.csv": self.revocations,
                "verification_requests.csv": self.verifications, cfg.ATTEMPTS_FILE: self.attempts}

    def copy(self) -> Dataset:
        clone = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("keys", "_index")})
        return Dataset(**clone, keys=self.keys)


# ------------------------------------------------------------------ helpers -----------------------
def _between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """Uniform whole-second instant in [start, end]."""
    span = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(span, 0)))


def _random_credential_id(rng: random.Random, taken: set[str]) -> str:
    while True:
        value = "CRED-" + "".join(rng.choice(CROCKFORD_ALPHABET) for _ in range(12))
        if value not in taken:
            taken.add(value)
            return value


def rule_for(rules: list[dict], course_id: str, moment: datetime) -> dict | None:
    """D5 selection (same windows as eligibility.applicable_rule)."""
    for rule in rules:
        if rule["course_id"] == course_id and rule["status"] in ("active", "retired") \
                and rule["effective_from"] <= moment and (rule["effective_to"] is None or moment < rule["effective_to"]):
            return rule
    return None


def sign_payload(ds: Dataset, credential: dict, completion: dict) -> None:
    """(Re)build the canonical payload from source rows and sign it with the synthetic issuer key."""
    issuer = ds.lookup("issuers", "issuer_id", credential["issuer_id"])
    rule = ds.lookup("rules", "rule_id", credential["rule_id"])
    eligibility = EligibilityResult(
        learner_id=credential["learner_id"], course_id=credential["course_id"], eligible=True, reasons=(),
        checks={}, evaluated_at=credential["issued_at"], rule=SimpleNamespace(**rule),
        completion=SimpleNamespace(**completion),
    )
    payload = build_payload(
        credential_id=credential["credential_id"],
        learner=SimpleNamespace(**ds.lookup("learners", "learner_id", credential["learner_id"])),
        course=SimpleNamespace(**ds.lookup("courses", "course_id", credential["course_id"])),
        issuer=SimpleNamespace(**issuer), eligibility=eligibility, issued_at=credential["issued_at"],
        expires_at=credential["expires_at"],
    )
    canonical = canonical_json(payload)
    data = canonical.encode("utf-8")
    credential.update(payload_json=canonical, payload_hash=sha256_hex(data),
                      signature=sign(ds.keys[issuer["signing_key_id"]], data))
    ds.payloads[credential["credential_id"]] = payload


def tamper(payload: dict, variant: str) -> dict:
    """A presented payload altered by the holder (A4 variants and correctly answered tampering)."""
    altered = dict(payload)
    if variant == "score_changed":
        altered["score"] = "99.00" if altered.get("score") != "99.00" else "98.00"
    elif variant == "name_changed":
        altered["learner_name"] = altered["learner_name"] + " Jr"
    elif variant == "course_changed":
        altered["course_title"] = "Advanced " + altered["course_title"]
    elif variant == "expiry_extended":
        altered["expires_at"] = "2099-12-31T00:00:00Z"
    else:  # pragma: no cover
        raise ValueError(variant)
    return altered


def presented_hash(payload: dict) -> str:
    return sha256_hex(canonical_json(payload).encode("utf-8"))


# ------------------------------------------------------------------ generation stages -------------
def _issuers(ds: Dataset, profile: cfg.Profile, rng: random.Random) -> None:
    for n in range(profile.active_issuers + 1):
        issuer_id = f"ISS-{901 + n}"
        key = Ed25519PrivateKey.from_private_bytes(rng.randbytes(32))  # deterministic, synthetic only
        signing_key_id = f"{issuer_id}-K1"
        ds.keys[signing_key_id] = key
        retired = n == 0
        ds.issuers.append({
            "issuer_id": issuer_id, "name": f"Synthetic College {n + 1}",
            "legal_name": f"Synthetic College {n + 1} (synthetic test data)",
            "country_code": rng.choice(("GB", "IN", "US", None)), "website_url": None,
            "signing_key_id": signing_key_id, "public_key": public_key_b64(key), "key_algorithm": "Ed25519",
            "status": "retired" if retired else "active",
            "supersedes_issuer_id": "ISS-901" if n == 1 else None,
            "valid_from": cfg.ISSUER_RETIREMENT if n == 1 else cfg.DATASET_START,  # successor starts at retirement
            "valid_to": cfg.ISSUER_RETIREMENT if retired else None,
            "created_by": cfg.SYSTEM_ACTOR,
        })


def _learners(ds: Dataset, profile: cfg.Profile, rng: random.Random) -> None:
    for n in range(1, profile.learners + 1):
        given, family = rng.choice(GIVEN), rng.choice(FAMILY)  # small lists: natural name collisions
        ds.learners.append({
            "learner_id": f"LRN-{n:07d}", "external_ref": f"STU-{n:07d}", "given_name": given,
            "family_name": family, "email": f"{given}.{family}.{n}@example.org".lower(),
            "status": "inactive" if rng.random() < 0.03 else "active",
        })


def _courses_and_rules(ds: Dataset, profile: cfg.Profile, rng: random.Random) -> dict[str, datetime]:
    starts: dict[str, datetime] = {}
    active_issuers = [i["issuer_id"] for i in ds.issuers if i["status"] == "active"]
    v2_courses: list[int] = []
    for n in range(1, profile.courses + 1):
        course_id = f"CRS-{n:05d}"
        start = cfg.DATASET_START + timedelta(days=rng.randint(0, 180))
        starts[course_id] = start
        ds.courses.append({
            "course_id": course_id, "course_code": f"{DEPTS[(n - 1) % len(DEPTS)]}-{n:04d}",
            "title": f"{rng.choice(LEVELS)} {rng.choice(SUBJECTS)}", "description": None,
            "credit_hours": rng.choice((5, 10, 15, 20)), "issuer_id": active_issuers[(n - 1) % len(active_issuers)],
            "lead_instructor_id": f"INS-SYN-{rng.randint(1, 40):03d}" if rng.random() < 0.5 else None,
            "status": "active",
        })
        min_score = float(rng.choice((50, 60, 70, 75)))
        min_attendance = float(rng.choice((70, 75, 80))) if rng.random() < 0.5 else None
        validity = rng.choice((180, 365)) if (n == 1 or rng.random() < 0.15) else None
        credential_type = "badge" if rng.random() < 0.1 else "certificate"
        has_v2 = rng.random() < 0.10
        if has_v2:
            v2_courses.append(n)
        ds.rules.append({
            "rule_id": f"RUL-{n:05d}-V1", "course_id": course_id, "version": 1,
            "credential_type": credential_type, "min_score": min_score, "min_attendance_pct": min_attendance,
            "validity_days": validity, "status": "active", "effective_from": start, "effective_to": None,
            "description": f"Pass mark {min_score:g}", "created_by": cfg.SYSTEM_ACTOR, "activated_at": start,
        })
    if not v2_courses:  # every profile demonstrates rule versioning
        v2_courses.append(profile.courses)
    for n in v2_courses:
        v1 = ds.rules[n - 1]
        effective = cfg.DATASET_START + timedelta(days=rng.randint(520, 700))
        v1.update(status="retired", effective_to=effective)
        ds.rules.append({
            **v1, "rule_id": f"RUL-{n:05d}-V2", "version": 2, "min_score": min(v1["min_score"] + 5, 95.0),
            "status": "active", "effective_from": effective, "effective_to": None,
            "description": f"Pass mark {min(v1['min_score'] + 5, 95.0):g}", "activated_at": effective,
        })
    ds.reserved["v2_courses"] = [f"CRS-{n:05d}" for n in v2_courses]
    return starts


def _completions(ds: Dataset, profile: cfg.Profile, rng: random.Random, starts: dict[str, datetime]) -> dict[str, list[dict]]:
    active_learners = [l["learner_id"] for l in ds.learners if l["status"] == "active"]
    course_ids = [c["course_id"] for c in ds.courses]
    used: set[tuple[str, str]] = set()
    rows: list[dict] = []

    def new_pair() -> tuple[str, str]:
        while True:
            pair = (rng.choice(active_learners), rng.choice(course_ids))
            if pair not in used:
                used.add(pair)
                return pair

    def completed(learner_id: str, course_id: str) -> dict:
        completed_at = _between(rng, starts[course_id] + timedelta(days=30), cfg.REFERENCE - timedelta(days=60))
        rule = ds.rule_for(course_id, completed_at)
        attendance_floor = rule["min_attendance_pct"] or 60.0
        lead = ds.lookup("courses", "course_id", course_id)["lead_instructor_id"]
        return {
            "learner_id": learner_id, "course_id": course_id, "attempt_number": 1, "status": "completed",
            "score": round(rng.uniform(rule["min_score"], 100.0), 2),
            "attendance_pct": round(rng.uniform(attendance_floor, 100.0), 2), "completed_at": completed_at,
            "assessed_by": lead or "INS-SYN-000",
            "recorded_at": min(completed_at + timedelta(seconds=rng.randint(0, 3 * 86400)), cfg.REFERENCE),
        }

    evidence = [completed(*new_pair()) for _ in range(profile.baseline_credentials)]
    pending = [completed(*new_pair()) for _ in range(profile.pending_completions)]
    others_left = profile.other_completions
    for row in evidence + pending:  # ≈5 % second attempts: a failed first attempt precedes them
        if others_left and rng.random() < 0.05:
            rule = ds.rule_for(row["course_id"], row["completed_at"])
            rows.append({**row, "attempt_number": 1, "status": "failed", "completed_at": None,
                         "score": round(rng.uniform(0, rule["min_score"] - 0.01), 2),
                         "recorded_at": row["completed_at"] - timedelta(days=rng.randint(20, 60))})
            row["attempt_number"] = 2
            others_left -= 1
    for _ in range(others_left):
        learner_id, course_id = new_pair()
        status = rng.choice(("failed", "in_progress", "withdrawn"))
        recorded = _between(rng, starts[course_id] + timedelta(days=10), cfg.REFERENCE - timedelta(days=1))
        rows.append({
            "learner_id": learner_id, "course_id": course_id, "attempt_number": 1, "status": status,
            "score": round(rng.uniform(0, 49.99), 2) if status == "failed" else None,
            "attendance_pct": round(rng.uniform(10, 60), 2) if status != "in_progress" else None,
            "completed_at": None, "assessed_by": None, "recorded_at": recorded,
        })
    rows.extend(evidence + pending)
    rows.sort(key=lambda r: (r["recorded_at"], r["learner_id"], r["course_id"], r["attempt_number"]))
    for n, row in enumerate(rows, start=1):
        row["completion_id"] = f"CMP-{n:07d}"
    ds.completions = rows
    return {"evidence": evidence, "pending": pending}


def _credentials(ds: Dataset, profile: cfg.Profile, rng: random.Random, evidence: list[dict]) -> set[str]:
    taken: set[str] = set()
    completion_by_pair = {(c["learner_id"], c["course_id"], c["attempt_number"]): c for c in ds.completions}
    quick = max(profile.hard_negatives, profile.baseline_credentials // 100)  # A1 hard negatives
    for n, row in enumerate(evidence):
        completion = completion_by_pair[(row["learner_id"], row["course_id"], row["attempt_number"])]
        gap = timedelta(seconds=rng.randint(300, 12 * 3600)) if n < quick else \
            timedelta(days=rng.randint(1, 14), seconds=rng.randint(0, 86399))
        issued_at = completion["completed_at"] + gap
        course = ds.lookup("courses", "course_id", row["course_id"])
        issuer_id = course["issuer_id"]
        if issuer_id == "ISS-902" and issued_at < cfg.ISSUER_RETIREMENT:
            issuer_id = "ISS-901"  # historical issuer: signed before its retirement (BR-17)
        rule = ds.rule_for(row["course_id"], completion["completed_at"])
        credential = {
            "credential_id": _random_credential_id(rng, taken), "learner_id": row["learner_id"],
            "course_id": row["course_id"], "completion_id": completion["completion_id"],
            "rule_id": rule["rule_id"], "issuer_id": issuer_id, "credential_type": rule["credential_type"],
            "status": "active", "issued_at": issued_at,
            "expires_at": issued_at + timedelta(days=rule["validity_days"]) if rule["validity_days"] else None,
            "revoked_at": None, "payload_json": "", "payload_hash": "", "signature": "",
            "signing_key_id": f"{issuer_id}-K1", "schema_version": "1.0", "issued_by": cfg.ADMIN_ACTOR,
        }
        sign_payload(ds, credential, completion)
        ds.credentials.append(credential)
    ds.reserved["quick_issue_hard_negatives"] = [c["credential_id"] for c in ds.credentials[:quick]]
    return taken


def _revocations(ds: Dataset, profile: cfg.Profile, rng: random.Random) -> None:
    candidates = [c for c in ds.credentials if c["expires_at"] is None
                  and c["issued_at"] < cfg.REFERENCE - timedelta(days=90)]
    count = max(profile.a3 + profile.hard_negatives, round(0.03 * profile.baseline_credentials))
    chosen = rng.sample(candidates, count)
    for credential in chosen:
        room = max(1, min(200, (cfg.REFERENCE - timedelta(days=30) - credential["issued_at"]).days))
        revoked_at = credential["issued_at"] + timedelta(days=rng.randint(1, room), seconds=rng.randint(0, 86399))
        credential.update(status="revoked", revoked_at=revoked_at)
        reason = rng.choice(REASONS)
        ds.revocations.append({
            "credential_id": credential["credential_id"], "reason_code": reason,
            "reason_text": "Synthetic panel decision" if reason == "other" else None,
            "requested_by": "SUP-SYN-001" if rng.random() < 0.5 else None, "approved_by": cfg.ADMIN_ACTOR,
            "revoked_at": revoked_at,
        })
    ds.revocations.sort(key=lambda r: (r["revoked_at"], r["credential_id"]))
    ds.revocations = [{"revocation_id": f"REV-{n:06d}", **r} for n, r in enumerate(ds.revocations, start=1)]


def _verification(ds: Dataset, rng: random.Random, credential: dict | None, requested_at: datetime,
                  presented: dict | None = None, submitted: str | None = None) -> dict:
    """A correctly answered verification (true flags, BR-13 result)."""
    if credential is None:
        flags = dict(hash_match=None, signature_valid=None, is_revoked=None, is_expired=None)
        presented_payload_hash = None
    else:
        presented_payload_hash = presented_hash(presented) if presented is not None else None
        intact = presented_payload_hash in (None, credential["payload_hash"])
        flags = dict(
            hash_match=intact, signature_valid=intact,  # a modified payload cannot match the signature
            is_revoked=credential["revoked_at"] is not None and credential["revoked_at"] <= requested_at,
            is_expired=credential["expires_at"] is not None and requested_at > credential["expires_at"],
        )
    result = decide_result(found=credential is not None, **flags)
    return {
        "submitted_credential_id": submitted or credential["credential_id"],
        "credential_id": credential["credential_id"] if credential else None,
        "verifier_name": f"{rng.choice(GIVEN)} {rng.choice(FAMILY)}", "verifier_organization": rng.choice(ORGS),
        "verifier_email": None, "purpose": rng.choice(("employment", "admission", "licensing", "other", None)),
        "channel": "api", "presented_payload_hash": presented_payload_hash, **flags, "result": result,
        "requested_at": requested_at, "responded_at": requested_at,
    }


def _verifications(ds: Dataset, profile: cfg.Profile, rng: random.Random, taken_ids: set[str]) -> None:
    hard = profile.hard_negatives
    v = profile.verifications
    # A1 targets are reserved now so no verification ever references a credential that the
    # injector will re-sign (that would create an unlabelled A4).
    a1_pool = [c for c in ds.credentials if c["status"] == "active"
               and c["course_id"] not in ds.reserved["v2_courses"]
               and c["issued_at"] < cfg.REFERENCE - timedelta(days=40)
               and c["credential_id"] not in ds.reserved["quick_issue_hard_negatives"]]
    a1 = rng.sample(a1_pool, profile.a1)
    ds.reserved["a1"] = [c["credential_id"] for c in a1]
    excluded = set(ds.reserved["a1"])
    revoked = [c for c in ds.credentials if c["status"] == "revoked"]
    active = [c for c in ds.credentials if c["status"] == "active" and c["credential_id"] not in excluded]
    expiring = [c for c in active if c["expires_at"] is not None and c["expires_at"] < cfg.REFERENCE - timedelta(days=2)]

    def valid_window(c: dict) -> tuple[datetime, datetime]:
        end = min(x for x in (cfg.REFERENCE, c["expires_at"]) if x is not None)
        return c["issued_at"] + timedelta(seconds=1), end

    plan = {
        "not_found": max(1, round(0.02 * v)), "tampered": max(hard, round(0.02 * v)),
        "expired": max(1, round(0.01 * v)), "revoked": max(hard, round(0.05 * v)),
        "before_revocation": hard, "a3_pool": profile.a3, "a4_pool": profile.a4,
        "presented_intact": max(hard, round(0.10 * v)),
    }
    if not expiring:  # pragma: no cover - guaranteed by course 1 having a validity period
        raise RuntimeError("no expired credentials available for the expired verification class")
    rows: list[tuple[str, dict]] = []
    for _ in range(plan["not_found"]):
        rows.append(("not_found", _verification(ds, rng, None, _between(rng, cfg.DATASET_START, cfg.REFERENCE),
                                                submitted=_random_credential_id(rng, set(taken_ids)))))
    for kind in ("revoked", "a3_pool"):
        for _ in range(plan[kind]):
            c = rng.choice(revoked)
            rows.append((kind, _verification(ds, rng, c, _between(rng, c["revoked_at"] + timedelta(seconds=1), cfg.REFERENCE))))
    for _ in range(plan["before_revocation"]):
        c = rng.choice(revoked)
        rows.append(("before_revocation", _verification(ds, rng, c, _between(
            rng, c["issued_at"] + timedelta(seconds=1), c["revoked_at"] - timedelta(seconds=1)))))
    for _ in range(plan["expired"]):
        c = rng.choice(expiring)
        rows.append(("expired", _verification(ds, rng, c, _between(rng, c["expires_at"] + timedelta(seconds=1), cfg.REFERENCE))))
    for _ in range(plan["tampered"]):
        c = rng.choice(active)
        variant = rng.choice(cfg.VARIANTS[cfg.A4])
        rows.append(("tampered", _verification(ds, rng, c, _between(rng, *valid_window(c)),
                                               presented=tamper(ds.payloads[c["credential_id"]], variant))))
    for _ in range(plan["presented_intact"]):
        c = rng.choice(active)
        rows.append(("presented_intact", _verification(ds, rng, c, _between(rng, *valid_window(c)),
                                                       presented=ds.payloads[c["credential_id"]])))
    remaining = v - len(rows)
    for kind, count in (("a4_pool", plan["a4_pool"]), ("valid", remaining - plan["a4_pool"])):
        for _ in range(count):
            c = rng.choice(active)
            rows.append((kind, _verification(ds, rng, c, _between(rng, *valid_window(c)))))
    rows.sort(key=lambda kr: (kr[1]["requested_at"], kr[1]["submitted_credential_id"]))
    for n, (kind, row) in enumerate(rows, start=1):
        row_id = f"VER-{n:07d}"
        ds.verifications.append({"verification_id": row_id, **row})
        ds.reserved.setdefault(kind, []).append(row_id)


def _valid_attempts(ds: Dataset, profile: cfg.Profile, rng: random.Random, pending: list[dict],
                    taken_ids: set[str]) -> None:
    """Late-arriving valid issuance requests (A2 hard negatives), replayed by the loader."""
    targets = rng.sample(pending, profile.valid_attempts)
    for n, row in enumerate(targets, start=1):
        attempted = _between(rng, cfg.REFERENCE - timedelta(days=20), cfg.REFERENCE - timedelta(days=1))
        ds.attempts.append({
            "attempt_id": f"ATT-{n:06d}", "credential_id": _random_credential_id(rng, taken_ids),
            "learner_id": row["learner_id"], "course_id": row["course_id"], "attempted_at": attempted,
            "actor_id": cfg.ADMIN_ACTOR,
        })
    ds.reserved["valid_attempts"] = [a["attempt_id"] for a in ds.attempts]


def generate_valid(profile: cfg.Profile, rng: random.Random) -> Dataset:
    """A fully valid dataset: no anomalies, every outcome correct."""
    ds = Dataset(profile=profile.name)
    _issuers(ds, profile, rng)
    _learners(ds, profile, rng)
    starts = _courses_and_rules(ds, profile, rng)
    groups = _completions(ds, profile, rng, starts)
    taken = _credentials(ds, profile, rng, groups["evidence"])
    _revocations(ds, profile, rng)
    _verifications(ds, profile, rng, taken)
    _valid_attempts(ds, profile, rng, groups["pending"], taken)
    return ds


# ------------------------------------------------------------------ output ----------------------------
def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return format_utc(value)
    return str(value)


TABLE_MODELS = {
    "issuers.csv": Issuer, "learners.csv": Learner, "courses.csv": Course,
    "credential_rules.csv": CredentialRule, "completion_records.csv": CompletionRecord,
    "credentials.csv": Credential, "revocations.csv": Revocation,
    "verification_requests.csv": VerificationRequest,
}


def columns_of(file_name: str) -> tuple[str, ...]:
    if file_name == cfg.ATTEMPTS_FILE:
        return cfg.ATTEMPT_COLUMNS
    return tuple(c.name for c in TABLE_MODELS[file_name].__table__.columns if c.name not in cfg.EXCLUDED_COLUMNS)


def csv_bytes(columns: tuple[str, ...], rows: list[dict]) -> bytes:
    """UTF-8 without BOM, '\\n' line endings: byte-identical across runs and platforms."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return buffer.getvalue().encode("utf-8")


def write_outputs(ds: Dataset, labels: list[dict], profile: cfg.Profile, seed: int,
                  generated_dir: Path, ground_truth_dir: Path) -> dict:
    """Write the input CSVs, ground truth and manifest. Returns the manifest."""
    generated_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_dir.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    counts: dict[str, int] = {}
    for file_name, rows in ds.tables().items():
        data = csv_bytes(columns_of(file_name), rows)
        (generated_dir / file_name).write_bytes(data)
        checksums[file_name] = hashlib.sha256(data).hexdigest()
        counts[file_name] = len(rows)
    label_data = csv_bytes(cfg.LABEL_COLUMNS, labels)
    (ground_truth_dir / cfg.LABELS_FILE).write_bytes(label_data)
    anomaly_counts = {kind: sum(1 for l in labels if l["anomaly_type"] == kind) for kind in (cfg.A1, cfg.A2, cfg.A3, cfg.A4)}
    manifest = {
        "generator_version": cfg.GENERATOR_VERSION, "profile": profile.name, "seed": seed,
        "reference_instant": format_utc(cfg.REFERENCE), "row_counts": counts,
        "credentials_after_replay": counts["credentials.csv"] + len(ds.reserved.get("valid_attempts", [])),
        "anomaly_counts": anomaly_counts, "files": checksums,
        "ground_truth": {cfg.LABELS_FILE: hashlib.sha256(label_data).hexdigest(), "labels": len(labels)},
    }
    (generated_dir / cfg.MANIFEST_FILE).write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def write_synthetic_keys(ds: Dataset, profile: str, synthetic_key_dir: Path, platform_key_dir: Path) -> list[Path]:
    """Write keys only under ``<synthetic_key_dir>/<profile>/`` (D8). Never touches the platform dir."""
    root = synthetic_key_dir.resolve()
    platform = platform_key_dir.resolve()
    if root == platform or platform in root.parents or root in platform.parents:
        raise ValueError("synthetic and platform key directories must be separate (D8)")
    target = (root / profile).resolve()
    if root not in target.parents:
        raise ValueError(f"profile directory escapes the synthetic key directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for signing_key_id, key in sorted(ds.keys.items()):
        if not signing_key_id.startswith("ISS-9"):
            raise ValueError(f"not a synthetic key: {signing_key_id}")
        path = (target / f"{signing_key_id}.pem").resolve()
        if path.parent != target:
            raise ValueError(f"key path escapes {target}")
        path.write_bytes(private_key_to_pem(key))  # deterministic bytes; same seed rewrites the same key
        written.append(path)
    return written
