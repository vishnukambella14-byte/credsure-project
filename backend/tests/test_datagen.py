"""T06 acceptance tests: generator, A1–A4 injection, ground truth, manifest, key isolation, loader."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.datagen import config as cfg
from app.datagen.__main__ import generate
from app.datagen.generator import generate_valid, presented_hash, write_synthetic_keys
from app.datagen.injector import inject
from app.datagen.loader import LoadError, load_dataset
from app.services.audit import verify_chain
from app.services.crypto import KeyStore, load_private_key_pem, public_key_b64, sha256_hex, verify_signature
from tests.support import read_sessions, scalar, write_sessions

STARTER = cfg.PROFILES["starter"]
FULL = cfg.PROFILES["full"]


def _generate(root: Path, profile: str):  # noqa: ANN202
    platform = root / "keys" / "platform"
    platform.mkdir(parents=True, exist_ok=True)
    (platform / "ISS-001-K1.pem").write_bytes(b"platform key: must never change")
    return generate(profile, generated_root=root / "generated", ground_truth_root=root / "ground_truth",
                    synthetic_key_dir=root / "keys" / "synthetic", platform_key_dir=platform)


def _files(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def starter(tmp_path_factory):  # noqa: ANN001, ANN201
    root = tmp_path_factory.mktemp("starter")
    return root, _generate(root, "starter")


@pytest.fixture(scope="module")
def full_runs(tmp_path_factory):  # noqa: ANN001, ANN201
    first, second = tmp_path_factory.mktemp("full1"), tmp_path_factory.mktemp("full2")
    return (first, _generate(first, "full")), (second, _generate(second, "full"))


def _expected_counts(profile: cfg.Profile, manifest: dict) -> None:
    counts = manifest["row_counts"]
    assert counts["issuers.csv"] == profile.active_issuers + 1
    assert counts["learners.csv"] == profile.learners and counts["courses.csv"] == profile.courses
    assert counts["credentials.csv"] == profile.baseline_credentials
    assert manifest["credentials_after_replay"] == profile.credentials_total
    assert counts["verification_requests.csv"] == profile.verifications
    assert counts["issuance_attempts.csv"] == profile.valid_attempts + profile.a2
    assert counts["completion_records.csv"] == (profile.baseline_credentials + profile.pending_completions
                                                + profile.other_completions)
    assert manifest["anomaly_counts"] == {cfg.A1: profile.a1, cfg.A2: profile.a2, cfg.A3: profile.a3, cfg.A4: profile.a4}


# ------------------------------------------------------------------ generation and reproducibility --
def test_starter_generation_succeeds(starter) -> None:  # noqa: ANN001
    root, result = starter
    _expected_counts(STARTER, result.manifest)
    assert result.manifest["seed"] == cfg.SEED and result.manifest["profile"] == "starter"
    assert sorted(_files(result.generated_dir)) == sorted([f for f, _ in cfg.TABLE_FILES] + [cfg.ATTEMPTS_FILE, cfg.MANIFEST_FILE])


def test_full_generation_succeeds(full_runs) -> None:  # noqa: ANN001
    (_, result), _ = full_runs
    _expected_counts(FULL, result.manifest)
    rules = result.manifest["row_counts"]["credential_rules.csv"]
    assert FULL.courses < rules <= FULL.courses * 1.2  # ≈10 % of courses have a second version


def test_same_seed_and_profile_give_identical_files(starter, full_runs, tmp_path: Path) -> None:  # noqa: ANN001
    root, first = starter
    second = _generate(tmp_path, "starter")
    assert _files(first.generated_dir) == _files(second.generated_dir)
    assert _files(first.ground_truth_dir) == _files(second.ground_truth_dir)
    (_, full1), (_, full2) = full_runs
    assert _files(full1.generated_dir) == _files(full2.generated_dir)
    assert _files(full1.ground_truth_dir) == _files(full2.ground_truth_dir)


def test_manifest_checksums_match_the_files(starter) -> None:  # noqa: ANN001
    _, result = starter
    for name, digest in result.manifest["files"].items():
        assert hashlib.sha256((result.generated_dir / name).read_bytes()).hexdigest() == digest
    labels = result.ground_truth_dir / cfg.LABELS_FILE
    assert hashlib.sha256(labels.read_bytes()).hexdigest() == result.manifest["ground_truth"][cfg.LABELS_FILE]
    raw = (result.generated_dir / cfg.MANIFEST_FILE).read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")


# ------------------------------------------------------------------ valid first, then injection -----
def _a1_condition(ds, credential) -> bool:  # noqa: ANN001
    completion = ds.lookup("completions", "completion_id", credential["completion_id"])
    return (completion["status"] != "completed" or completion["completed_at"] is None
            or credential["issued_at"] < completion["completed_at"])


def _a3_condition(ds, row) -> bool:  # noqa: ANN001
    if row["credential_id"] is None:
        return False
    revoked_at = ds.lookup("credentials", "credential_id", row["credential_id"])["revoked_at"]
    return revoked_at is not None and revoked_at <= row["requested_at"] and row["result"] == "valid"


def _a4_condition(ds, row) -> bool:  # noqa: ANN001
    if row["credential_id"] is None or row["presented_payload_hash"] is None:
        return False
    stored = ds.lookup("credentials", "credential_id", row["credential_id"])["payload_hash"]
    return row["presented_payload_hash"] != stored and row["result"] == "valid"


def test_valid_records_are_generated_before_injection() -> None:
    valid = generate_valid(STARTER, random.Random(cfg.SEED))
    public = {i["signing_key_id"]: i["public_key"] for i in valid.issuers}
    for credential in valid.credentials:
        assert not _a1_condition(valid, credential)
        data = credential["payload_json"].encode("utf-8")
        assert credential["payload_hash"] == sha256_hex(data)
        assert verify_signature(public[credential["signing_key_id"]], data, credential["signature"])
    assert not any(_a3_condition(valid, v) or _a4_condition(valid, v) for v in valid.verifications)
    existing = {c["credential_id"] for c in valid.credentials}
    assert not any(a["credential_id"].upper() in existing for a in valid.attempts)


def test_injection_changes_only_labelled_records() -> None:
    rng = random.Random(cfg.SEED)
    valid = generate_valid(STARTER, rng)
    snapshot = json.dumps(valid.tables(), default=str, sort_keys=True)
    injected, labels = inject(valid, STARTER, rng)
    assert json.dumps(valid.tables(), default=str, sort_keys=True) == snapshot  # baseline untouched
    by_type = {kind: {l["record_id"] for l in labels if l["anomaly_type"] == kind} for kind in (cfg.A1, cfg.A2, cfg.A3, cfg.A4)}

    changed_credentials = {c["credential_id"] for c, o in zip(injected.credentials, valid.credentials) if c != o}
    assert changed_credentials == by_type[cfg.A1]
    changed_verifications = {v["verification_id"] for v, o in zip(injected.verifications, valid.verifications) if v != o}
    assert changed_verifications == by_type[cfg.A3] | by_type[cfg.A4]
    added_attempts = {a["attempt_id"] for a in injected.attempts[len(valid.attempts):]}
    assert added_attempts == by_type[cfg.A2]


def test_a1_to_a4_are_injected_correctly() -> None:
    rng = random.Random(cfg.SEED)
    ds, labels = inject(generate_valid(STARTER, rng), STARTER, rng)
    by_type = {kind: {l["record_id"] for l in labels if l["anomaly_type"] == kind} for kind in (cfg.A1, cfg.A2, cfg.A3, cfg.A4)}
    assert {c["credential_id"] for c in ds.credentials if _a1_condition(ds, c)} == by_type[cfg.A1]
    existing = {c["credential_id"] for c in ds.credentials}
    assert {a["attempt_id"] for a in ds.attempts if a["credential_id"].upper() in existing} == by_type[cfg.A2]
    assert {v["verification_id"] for v in ds.verifications if _a3_condition(ds, v)} == by_type[cfg.A3]
    assert {v["verification_id"] for v in ds.verifications if _a4_condition(ds, v)} == by_type[cfg.A4]
    # A1 credentials are re-signed, so they are not also an integrity (A4-like) problem.
    public = {i["signing_key_id"]: i["public_key"] for i in ds.issuers}
    for credential_id in by_type[cfg.A1]:
        c = ds.lookup("credentials", "credential_id", credential_id)
        assert verify_signature(public[c["signing_key_id"]], c["payload_json"].encode(), c["signature"])
    # Every documented variant appears; A3/A4 rows keep flags consistent with the precedence CHECK.
    assert {l["variant"] for l in labels if l["anomaly_type"] == cfg.A1} == set(cfg.VARIANTS[cfg.A1])
    assert {l["variant"] for l in labels if l["anomaly_type"] == cfg.A2} == set(cfg.VARIANTS[cfg.A2][:STARTER.a2])
    for v in ds.verifications:
        if v["verification_id"] in by_type[cfg.A3] | by_type[cfg.A4]:
            assert (v["hash_match"], v["signature_valid"], v["is_revoked"], v["is_expired"]) == (True, True, False, False)


def test_hard_negatives_are_present() -> None:
    rng = random.Random(cfg.SEED)
    ds, _ = inject(generate_valid(STARTER, rng), STARTER, rng)
    hard = STARTER.hard_negatives
    assert len(ds.reserved["quick_issue_hard_negatives"]) >= hard  # A1: issued minutes/hours after completion
    assert len(ds.reserved["valid_attempts"]) >= hard  # A2: valid new attempts
    assert len(ds.reserved["before_revocation"]) >= hard and len(ds.reserved["revoked"]) >= hard  # A3
    assert len(ds.reserved["tampered"]) >= hard and len(ds.reserved["presented_intact"]) >= hard  # A4
    tampered = [ds.lookup("verifications", "verification_id", v) for v in ds.reserved["tampered"]]
    assert all(v["result"] == "tampered" for v in tampered)


# ------------------------------------------------------------------ ground truth --------------------
def test_ground_truth_is_separate_and_identifies_anomalies(starter) -> None:  # noqa: ANN001
    _, result = starter
    labels = _rows(result.ground_truth_dir / cfg.LABELS_FILE)
    assert list(labels[0]) == list(cfg.LABEL_COLUMNS) and len(labels) == 9
    assert [l["label_id"] for l in labels] == [f"GT-{n:06d}" for n in range(1, 10)]
    record_files = {"credential": ("credentials.csv", "credential_id"),
                    "issuance_attempt": (cfg.ATTEMPTS_FILE, "attempt_id"),
                    "verification_request": ("verification_requests.csv", "verification_id")}
    for label in labels:
        file_name, key = record_files[label["record_type"]]
        assert label["record_id"] in {r[key] for r in _rows(result.generated_dir / file_name)}
    for path in result.generated_dir.glob("*.csv"):  # no labels or hints in input features (BR-24)
        header = path.read_text(encoding="utf-8").splitlines()[0].lower()
        assert not any(word in header for word in ("anomaly", "label", "injected", "ground"))
    assert not list(result.generated_dir.glob(cfg.LABELS_FILE))


# ------------------------------------------------------------------ synthetic keys (D8) -------------
def test_synthetic_keys_use_the_profile_directory(starter) -> None:  # noqa: ANN001
    root, result = starter
    synthetic = root / "keys" / "synthetic" / "starter"
    assert sorted(p.name for p in synthetic.iterdir()) == ["ISS-901-K1.pem", "ISS-902-K1.pem",
                                                            "ISS-903-K1.pem", "ISS-904-K1.pem"]
    assert all(p.parent == synthetic for p in result.key_files)
    issuers = {r["signing_key_id"]: r for r in _rows(result.generated_dir / "issuers.csv")}
    for path in result.key_files:
        assert public_key_b64(load_private_key_pem(path.read_bytes())) == issuers[path.stem]["public_key"]
    assert all(i["issuer_id"].startswith("ISS-9") for i in issuers.values())


def test_platform_key_directory_remains_unchanged(starter, tmp_path: Path) -> None:  # noqa: ANN001
    root, _ = starter
    platform = root / "keys" / "platform"
    assert _files(platform) == {"ISS-001-K1.pem": b"platform key: must never change"}
    valid = generate_valid(STARTER, random.Random(cfg.SEED))
    for synthetic, platform_dir in ((tmp_path / "same", tmp_path / "same"),
                                    (tmp_path / "keys", tmp_path / "keys" / "platform"),
                                    (tmp_path / "keys" / "platform" / "synthetic", tmp_path / "keys" / "platform")):
        with pytest.raises(ValueError, match="separate"):
            write_synthetic_keys(valid, "starter", synthetic, platform_dir)


# ------------------------------------------------------------------ loader -----------------------
def _load(engine: Engine, root: Path, profile: str = "starter"):  # noqa: ANN202
    store = KeyStore(root / "keys" / "platform", root / "keys" / "synthetic", synthetic_profile=profile)
    return load_dataset(profile, write_factory=write_sessions(engine), read_factory=read_sessions(engine),
                        key_store=store, generated_root=root / "generated")


def test_loader_imports_the_starter_dataset(migrated_engine: Engine, starter) -> None:  # noqa: ANN001
    root, result = starter
    report = _load(migrated_engine, root)
    counts = result.manifest["row_counts"]
    for file_name, table in cfg.TABLE_FILES:
        expected = counts[file_name] + (STARTER.valid_attempts if table == "credentials" else 0)
        assert scalar(migrated_engine, f"SELECT count(*) FROM {table}") == expected, table
    assert scalar(migrated_engine, "SELECT count(*) FROM credentials WHERE record_source <> 'synthetic_import'") == 0
    assert report.summary()["replay_rejected"] == {"duplicate_credential_id": STARTER.a2}
    assert scalar(migrated_engine, "SELECT count(*) FROM audit_events WHERE event_type='dataset.imported'") == 8
    with read_sessions(migrated_engine)() as session:
        assert verify_chain(session).valid


def test_replay_preserves_attempt_refs_and_injected_timestamps(migrated_engine: Engine, starter) -> None:  # noqa: ANN001
    root, result = starter
    report = _load(migrated_engine, root)
    attempts = {a["attempt_id"]: a for a in _rows(result.generated_dir / cfg.ATTEMPTS_FILE)}
    labels = {l["record_id"] for l in _rows(result.ground_truth_dir / cfg.LABELS_FILE)}
    assert set(report.replay_issued) == set(attempts) - labels  # every valid attempt issued
    for attempt_id in report.replay_issued:
        attempt = attempts[attempt_id]
        row = scalar(migrated_engine, "SELECT issued_at || '|' || credential_id FROM credentials "
                                      "WHERE credential_id = :c", {"c": attempt["credential_id"]})
        assert row == attempt["attempted_at"].replace("T", " ").rstrip("Z") + "|" + attempt["credential_id"]
        issued_ref = scalar(migrated_engine, "SELECT attempt_ref FROM audit_events WHERE event_type='credential.issued' "
                                             "AND entity_id = :c", {"c": attempt["credential_id"]})
        assert issued_ref == attempt_id
    for attempt_id, code in report.replay_rejected.items():
        assert attempt_id in labels and code == "duplicate_credential_id"
        event = scalar(migrated_engine, "SELECT occurred_at || '|' || reason_code FROM audit_events "
                                        "WHERE event_type='credential.issue_rejected' AND attempt_ref = :a", {"a": attempt_id})
        assert event == attempts[attempt_id]["attempted_at"].replace("T", " ").rstrip("Z") + "|duplicate_credential_id"


def test_loader_refuses_conflicts_and_wrong_key_store(migrated_engine: Engine, starter, tmp_path: Path) -> None:  # noqa: ANN001
    root, _ = starter
    _load(migrated_engine, root)
    with pytest.raises(LoadError, match="issuers.csv"):  # second load: PK conflict, use --reset
        _load(migrated_engine, root)
    with pytest.raises(LoadError, match="synthetic keys"):
        load_dataset("starter", write_factory=write_sessions(migrated_engine),
                     read_factory=read_sessions(migrated_engine), generated_root=root / "generated",
                     key_store=KeyStore(tmp_path / "p", tmp_path / "s"))


def test_loader_accepts_only_synthetic_issuers(fresh_engine: Engine, starter, tmp_path: Path) -> None:  # noqa: ANN001
    from tests.support import run_alembic

    root, result = starter
    copied = tmp_path / "generated" / "starter"
    copied.mkdir(parents=True)
    for path in result.generated_dir.iterdir():
        (copied / path.name).write_bytes(path.read_bytes())
    issuers = (copied / "issuers.csv").read_text(encoding="utf-8").replace("ISS-904", "ISS-004")
    (copied / "issuers.csv").write_text(issuers, encoding="utf-8", newline="\n")
    run_alembic(fresh_engine, "upgrade", "head")
    store = KeyStore(root / "keys" / "platform", root / "keys" / "synthetic", synthetic_profile="starter")
    with pytest.raises(LoadError, match="non-synthetic"):
        load_dataset("starter", write_factory=write_sessions(fresh_engine), read_factory=read_sessions(fresh_engine),
                     key_store=store, generated_root=tmp_path / "generated")
    assert scalar(fresh_engine, "SELECT count(*) FROM issuers") == 0


def test_presented_hash_helper_matches_platform_canonicalisation() -> None:
    payload = {"b": "1", "a": None}
    assert presented_hash(payload) == sha256_hex(b'{"a":null,"b":"1"}')
