"""Canonical JSON, SHA-256 and Ed25519 for signed credential payloads and the audit hash chain.

ADR-004 (amended by ADR-018 and ADR-022), BR-08, BR-09, BR-10:

* ``canonical_json`` = ``json.dumps(value, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)``, encoded as UTF-8 by ``canonical_bytes``.
  Only ``str``, ``int``, ``bool``, ``None``, ``dict`` (string keys) and ``list`` are accepted;
  **any float is rejected** (D6). Timestamps must already be ``format_utc`` strings and scores
  ``format_score`` strings.
* ``payload_hash = sha256(canonical_bytes).hexdigest()``; the signature is Ed25519 over the
  **same** canonical bytes, base64 (RFC 4648, padded). Public keys are base64 of the raw 32 bytes.
* Private keys live only in PKCS8 PEM files under the git-ignored key directories (``KeyStore``);
  they are never stored in the database, logged or returned.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.enums import RecordSource

SIGNATURE_B64_LENGTH = 88  # 64-byte Ed25519 signature
PUBLIC_KEY_B64_LENGTH = 44  # 32-byte raw public key
_TWO_PLACES = Decimal("0.01")


class CanonicalizationError(ValueError):
    """The value cannot be represented in a signed payload (e.g. it contains a float)."""


# --------------------------------------------------------------- canonical JSON -----------------------
def _check_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise CanonicalizationError(
            f"float at {path or '<root>'}: signed payloads use fixed-point strings (D6)"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string key at {path or '<root>'}: {key!r}")
            _check_value(item, f"{path}.{key}" if path else key)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_value(item, f"{path}[{index}]")
        return
    raise CanonicalizationError(
        f"unsupported type {type(value).__name__} at {path or '<root>'}; "
        "format timestamps with format_utc and scores with format_score first"
    )


def canonical_json(value: Any) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, UTF-8 characters kept, no floats."""
    _check_value(value, "")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_bytes(value: Any) -> bytes:
    """The exact bytes that are hashed and signed."""
    return canonical_json(value).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    """Lowercase hex SHA-256 (64 characters). ``str`` input is UTF-8 encoded."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def format_score(value: float | int | Decimal | str | None) -> str | None:
    """Fixed-point score string with exactly two decimals, e.g. ``82.5 -> "82.50"`` (D6)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise CanonicalizationError("a boolean is not a score")
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalizationError("score must be finite")
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise CanonicalizationError(f"not a number: {value!r}") from exc
    if not decimal.is_finite():
        raise CanonicalizationError("score must be finite")
    return str(decimal.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def to_decimal(value: float | int | Decimal | str) -> Decimal:
    """Decimal used for score/threshold comparisons (ADR-022), rounded to two places."""
    return Decimal(format_score(value))  # type: ignore[arg-type]


# --------------------------------------------------------------- Ed25519 --------------------------------
def generate_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def private_key_to_pem(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_key_pem(data: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def sign(private_key: Ed25519PrivateKey, data: bytes) -> str:
    """Base64 Ed25519 signature over ``data`` (the canonical bytes)."""
    return base64.b64encode(private_key.sign(data)).decode("ascii")


def _strict_b64(value: str, expected_bytes: int) -> bytes | None:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None
    return raw if len(raw) == expected_bytes else None


def verify_signature(public_key: str, data: bytes, signature: str) -> bool:
    """True only if ``signature`` is a valid Ed25519 signature of ``data`` under ``public_key``.

    Malformed base64, wrong lengths and invalid signatures all return False (never raise),
    so a caller can record ``signature_valid = False`` / ``invalid_signature``.
    """
    if not isinstance(signature, str) or not isinstance(public_key, str):
        return False
    raw_signature = _strict_b64(signature, 64)
    raw_public_key = _strict_b64(public_key, 32)
    if raw_signature is None or raw_public_key is None:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(raw_signature, data)
    except (InvalidSignature, ValueError):
        return False
    return True


# --------------------------------------------------------------- payload integrity check -------------
@dataclass(frozen=True)
class PayloadCheck:
    """Integrity result for a payload: recomputed hash, hash comparison and signature check."""

    computed_hash: str | None
    hash_match: bool
    signature_valid: bool


def check_payload(
    payload: dict[str, Any] | str, *, expected_hash: str, signature: str, public_key: str
) -> PayloadCheck:
    """Recompute the hash of ``payload`` and verify ``signature`` over its canonical bytes.

    ``payload`` is either a presented payload object (canonicalised here) or the stored
    ``payload_json`` text (hashed byte-for-byte as stored). A payload that cannot be
    canonicalised (e.g. a float score) cannot match: both checks fail, nothing raises.
    """
    try:
        data = payload.encode("utf-8") if isinstance(payload, str) else canonical_bytes(payload)
    except CanonicalizationError:
        return PayloadCheck(computed_hash=None, hash_match=False, signature_valid=False)
    computed = sha256_hex(data)
    return PayloadCheck(
        computed_hash=computed,
        hash_match=computed == expected_hash,
        signature_valid=verify_signature(public_key, data, signature),
    )


# --------------------------------------------------------------- key storage (ADR-005, ADR-024) ------
_SIGNING_KEY_ID = re.compile(r"^ISS-[0-9]{3}-K[0-9]+$")
_PROFILE = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class KeyStore:
    """Resolves private-key files. Platform and synthetic keys never share a directory (D8)."""

    platform_dir: Path
    synthetic_dir: Path
    synthetic_profile: str | None = None

    def path_for(self, signing_key_id: str, record_source: str) -> Path:
        if not _SIGNING_KEY_ID.match(signing_key_id or ""):
            raise ValueError(f"invalid signing key id: {signing_key_id!r}")
        if record_source == RecordSource.PLATFORM.value:
            return self.platform_dir / f"{signing_key_id}.pem"
        if record_source == RecordSource.SYNTHETIC_IMPORT.value:
            if not self.synthetic_profile or not _PROFILE.match(self.synthetic_profile):
                raise ValueError("synthetic keys require a dataset profile (e.g. 'starter')")
            return self.synthetic_dir / self.synthetic_profile / f"{signing_key_id}.pem"
        raise ValueError(f"unknown record_source: {record_source!r}")

    def is_available(self, signing_key_id: str, record_source: str) -> bool:
        try:
            return self.path_for(signing_key_id, record_source).is_file()
        except ValueError:
            return False

    def load(self, signing_key_id: str, record_source: str) -> Ed25519PrivateKey:
        """Load a private key, or raise ``signing_key_unavailable`` (never sign unsigned, BR-10)."""
        try:
            return load_private_key_pem(self.path_for(signing_key_id, record_source).read_bytes())
        except (OSError, ValueError, TypeError) as exc:
            raise DomainError(
                "signing_key_unavailable", details={"signing_key_id": signing_key_id}
            ) from exc

    def store_new_platform_key(self, signing_key_id: str, private_key: Ed25519PrivateKey) -> Path:
        """Write a new platform key with exclusive create: an existing file is never overwritten."""
        path = self.path_for(signing_key_id, RecordSource.PLATFORM.value)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:  # FileExistsError rather than overwrite (ADR-024)
            handle.write(private_key_to_pem(private_key))
        return path


def default_key_store(synthetic_profile: str | None = None) -> KeyStore:
    settings = get_settings()
    return KeyStore(
        platform_dir=Path(settings.signing_key_dir),
        synthetic_dir=Path(settings.synthetic_key_dir),
        synthetic_profile=synthetic_profile,
    )
