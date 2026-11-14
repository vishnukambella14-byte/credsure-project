"""Canonical JSON, SHA-256, Ed25519 and key storage (ADR-004, ADR-022, ADR-024, BR-08–BR-10)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.services.crypto import (
    CanonicalizationError,
    KeyStore,
    canonical_bytes,
    canonical_json,
    check_payload,
    format_score,
    generate_private_key,
    load_private_key_pem,
    public_key_b64,
    sha256_hex,
    sign,
    verify_signature,
)

# data_dictionary §4 example, with the fields as the issuance service will build them.
DOC_PAYLOAD = {
    "schema_version": "1.0", "credential_id": "CRED-7K2M9QXR4T1B", "credential_type": "certificate",
    "learner_id": "LRN-0000042", "learner_name": "Asha Rao", "course_id": "CRS-00017",
    "course_code": "DATA-101", "course_title": "Data Foundations", "rule_id": "RUL-00017-V1",
    "rule_version": 1, "completion_id": "CMP-0001234", "completed_at": "2026-03-14T10:00:00Z",
    "score": "82.50", "issuer_id": "ISS-001", "issuer_name": "CredSure Institute of Technology",
    "signing_key_id": "ISS-001-K1", "issued_at": "2026-03-20T09:30:00Z", "expires_at": None,
}
DOC_CANONICAL = (
    '{"completed_at":"2026-03-14T10:00:00Z","completion_id":"CMP-0001234","course_code":"DATA-101",'
    '"course_id":"CRS-00017","course_title":"Data Foundations","credential_id":"CRED-7K2M9QXR4T1B",'
    '"credential_type":"certificate","expires_at":null,"issued_at":"2026-03-20T09:30:00Z",'
    '"issuer_id":"ISS-001","issuer_name":"CredSure Institute of Technology","learner_id":"LRN-0000042",'
    '"learner_name":"Asha Rao","rule_id":"RUL-00017-V1","rule_version":1,"schema_version":"1.0",'
    '"score":"82.50","signing_key_id":"ISS-001-K1"}'
)


# ------------------------------------------------------------------ canonical JSON -----------------
def test_matches_documented_canonical_example() -> None:
    assert canonical_json(DOC_PAYLOAD) == DOC_CANONICAL


def test_deterministic_and_key_order_independent() -> None:
    reordered = dict(reversed(list(DOC_PAYLOAD.items())))
    assert canonical_json(reordered) == canonical_json(DOC_PAYLOAD)
    assert canonical_json({"b": {"y": 1, "x": [2, {"d": 1, "c": 0}]}, "a": True}) == (
        '{"a":true,"b":{"x":[2,{"c":0,"d":1}],"y":1}}'
    )
    assert canonical_bytes(DOC_PAYLOAD) == DOC_CANONICAL.encode("utf-8")


def test_exact_serialization_settings() -> None:
    expected = json.dumps(
        DOC_PAYLOAD, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert canonical_json(DOC_PAYLOAD) == expected
    assert canonical_json({"name": "Zoë Ñúñez"}) == '{"name":"Zoë Ñúñez"}'  # not \u-escaped
    assert canonical_bytes({"name": "é"}) == '{"name":"é"}'.encode("utf-8")


@pytest.mark.parametrize(
    "value",
    [
        {"score": 80.0},
        {"nested": {"items": [1, 2.5]}},
        {"score": float("nan")},
        {"at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"score": Decimal("82.50")},
        {1: "non-string key"},
    ],
)
def test_floats_and_unformatted_values_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(82.5, "82.50"), (80.0, "80.00"), (80, "80.00"), (100, "100.00"), (69.995, "70.00"),
     (0, "0.00"), (Decimal("70.004"), "70.00"), (None, None)],
)
def test_fixed_point_score_strings(raw: object, expected: str | None) -> None:
    assert format_score(raw) == expected


def test_fixed_point_score_is_accepted_and_survives_a_json_round_trip() -> None:
    payload = {"score": format_score(80.0)}
    assert canonical_json(payload) == '{"score":"80.00"}'
    assert canonical_json(json.loads(json.dumps(payload))) == canonical_json(payload)


@pytest.mark.parametrize("bad", [True, float("inf"), "abc"])
def test_format_score_rejects_non_scores(bad: object) -> None:
    with pytest.raises(CanonicalizationError):
        format_score(bad)


# ------------------------------------------------------------------ SHA-256 -------------------------
def test_sha256_known_vector_and_payload_hash() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex("abc") == sha256_hex(b"abc")
    digest = sha256_hex(canonical_bytes(DOC_PAYLOAD))
    assert digest == hashlib.sha256(DOC_CANONICAL.encode("utf-8")).hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


# ------------------------------------------------------------------ Ed25519 -------------------------
@pytest.fixture(scope="module")
def keypair():  # noqa: ANN201
    private_key = generate_private_key()
    return private_key, public_key_b64(private_key)


def test_sign_and_verify(keypair) -> None:  # noqa: ANN001
    private_key, public_key = keypair
    data = canonical_bytes(DOC_PAYLOAD)
    signature = sign(private_key, data)
    assert len(signature) == 88 and len(public_key) == 44
    assert verify_signature(public_key, data, signature)
    assert sign(private_key, data) == signature  # Ed25519 is deterministic


def test_incorrect_signatures_fail(keypair) -> None:  # noqa: ANN001
    private_key, public_key = keypair
    data = canonical_bytes(DOC_PAYLOAD)
    other_signature = sign(private_key, b"other bytes")
    other_key = public_key_b64(generate_private_key())
    signature = sign(private_key, data)
    assert not verify_signature(public_key, data, other_signature)
    assert not verify_signature(other_key, data, signature)
    assert not verify_signature(public_key, data, "not base64!!")
    assert not verify_signature(public_key, data, signature[:-4])
    assert not verify_signature("short", data, signature)


def test_modified_payload_fails_verification(keypair) -> None:  # noqa: ANN001
    private_key, public_key = keypair
    signature = sign(private_key, canonical_bytes(DOC_PAYLOAD))
    expected_hash = sha256_hex(canonical_bytes(DOC_PAYLOAD))

    intact = check_payload(dict(DOC_PAYLOAD), expected_hash=expected_hash, signature=signature,
                           public_key=public_key)
    assert intact.hash_match and intact.signature_valid and intact.computed_hash == expected_hash

    stored = check_payload(DOC_CANONICAL, expected_hash=expected_hash, signature=signature,
                           public_key=public_key)
    assert stored.hash_match and stored.signature_valid

    altered = check_payload({**DOC_PAYLOAD, "score": "92.50"}, expected_hash=expected_hash,
                            signature=signature, public_key=public_key)
    assert not altered.hash_match and not altered.signature_valid

    extra_key = check_payload({**DOC_PAYLOAD, "honours": True}, expected_hash=expected_hash,
                              signature=signature, public_key=public_key)
    assert not extra_key.hash_match


def test_malformed_payload_is_never_silent_success(keypair) -> None:  # noqa: ANN001
    private_key, public_key = keypair
    signature = sign(private_key, canonical_bytes(DOC_PAYLOAD))
    float_score = check_payload({**DOC_PAYLOAD, "score": 82.5}, expected_hash="0" * 64,
                                signature=signature, public_key=public_key)
    assert float_score == float_score.__class__(None, False, False)


# ------------------------------------------------------------------ key store (ADR-005, ADR-024) ---
def test_key_store_separates_platform_and_synthetic(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "platform", tmp_path / "synthetic", synthetic_profile="starter")
    assert store.path_for("ISS-001-K1", "platform") == tmp_path / "platform" / "ISS-001-K1.pem"
    assert store.path_for("ISS-901-K1", "synthetic_import") == (
        tmp_path / "synthetic" / "starter" / "ISS-901-K1.pem"
    )
    with pytest.raises(ValueError):
        KeyStore(tmp_path / "p", tmp_path / "s").path_for("ISS-901-K1", "synthetic_import")
    for bad in ("../ISS-001-K1", "ISS-001-K1/../../x", "iss-001-k1"):
        with pytest.raises(ValueError):
            store.path_for(bad, "platform")


def test_platform_key_is_written_once_and_never_overwritten(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "platform", tmp_path / "synthetic")
    first = generate_private_key()
    path = store.store_new_platform_key("ISS-001-K1", first)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.store_new_platform_key("ISS-001-K1", generate_private_key())
    assert path.read_bytes() == original
    loaded = store.load("ISS-001-K1", "platform")
    assert public_key_b64(loaded) == public_key_b64(first)
    assert load_private_key_pem(original) is not None
    assert store.is_available("ISS-001-K1", "platform")


def test_missing_key_raises_signing_key_unavailable(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "platform", tmp_path / "synthetic")
    assert not store.is_available("ISS-002-K1", "platform")
    with pytest.raises(DomainError) as caught:
        store.load("ISS-002-K1", "platform")
    assert caught.value.code == "signing_key_unavailable" and caught.value.status_code == 503
