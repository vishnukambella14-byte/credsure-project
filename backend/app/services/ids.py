"""Identifier generation (data_dictionary §1 "ID formats").

* Random IDs (credential IDs, platform attempt references) use ``secrets`` and the Crockford
  base32 alphabet (no I, L, O, U).
* Sequence IDs are ``MAX(id) + 1`` over the fixed-width IDs of a table. Call
  ``next_sequence_id`` inside the ``BEGIN IMMEDIATE`` write transaction that inserts the row
  (D3); this module only reads. It never inserts and never retries: the database primary key
  remains the final authority on uniqueness.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RANDOM_PART_LENGTH = 12

CREDENTIAL_ID_RE = re.compile(r"^CRED-[0-9A-HJKMNP-TV-Z]{12}$")
PLATFORM_ATTEMPT_REF_RE = re.compile(r"^ATT-[0-9A-HJKMNP-TV-Z]{12}$")
SYNTHETIC_ATTEMPT_REF_RE = re.compile(r"^ATT-[0-9]{6}$")


class IdSpaceExhausted(RuntimeError):
    """The fixed-width ID range of an entity is full (e.g. platform issuers above ISS-899)."""


def _random_part() -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(RANDOM_PART_LENGTH))


def new_credential_id() -> str:
    """``CRED-`` + 12 Crockford base32 characters from ``secrets`` (about 60 bits)."""
    return f"CRED-{_random_part()}"


def new_attempt_ref() -> str:
    """Platform issuance attempt reference: ``ATT-`` + 12 Crockford base32 characters (D7)."""
    return f"ATT-{_random_part()}"


def normalize_credential_id(raw: str) -> str:
    """Normalise a submitted credential ID (trim, upper case) before lookup or uniqueness checks."""
    return raw.strip().upper()


def is_credential_id(value: str) -> bool:
    return bool(CREDENTIAL_ID_RE.match(value))


def is_attempt_ref(value: str) -> bool:
    return bool(PLATFORM_ATTEMPT_REF_RE.match(value) or SYNTHETIC_ATTEMPT_REF_RE.match(value))


def rule_id_for(course_id: str, version: int) -> str:
    """``RUL-<course#>-V<version>``, the value enforced by ck_credential_rules_rule_id_derived."""
    if version < 1:
        raise ValueError("rule versions start at 1")
    return f"RUL-{course_id[4:]}-V{version}"


def signing_key_id_for(issuer_id: str) -> str:
    """Always ``-K1`` in the MVP: a new key creates a new issuer row (ADR-006)."""
    return f"{issuer_id}-K1"


def synthetic_attempt_ref(number: int) -> str:
    """Dataset attempt reference ``ATT-nnnnnn`` (generator / replay)."""
    if not 1 <= number <= 999_999:
        raise IdSpaceExhausted("synthetic attempt references are ATT-000001..ATT-999999")
    return f"ATT-{number:06d}"


@dataclass(frozen=True)
class SequenceSpec:
    table: str
    column: str
    prefix: str
    width: int
    first: int = 1
    last: int | None = None  # inclusive upper bound; default: all digits

    @property
    def maximum(self) -> int:
        return self.last if self.last is not None else 10**self.width - 1

    def format(self, number: int) -> str:
        if not self.first <= number <= self.maximum:
            raise IdSpaceExhausted(f"{self.prefix}{number} is outside {self.prefix} range")
        return f"{self.prefix}{number:0{self.width}d}"

    def glob(self) -> str:
        return self.prefix + "[0-9]" * self.width


SEQUENCES: dict[str, SequenceSpec] = {
    "issuer": SequenceSpec("issuers", "issuer_id", "ISS-", 3, first=1, last=899),
    "synthetic_issuer": SequenceSpec("issuers", "issuer_id", "ISS-", 3, first=901, last=999),
    "learner": SequenceSpec("learners", "learner_id", "LRN-", 7),
    "course": SequenceSpec("courses", "course_id", "CRS-", 5),
    "completion": SequenceSpec("completion_records", "completion_id", "CMP-", 7),
    "revocation": SequenceSpec("revocations", "revocation_id", "REV-", 6),
    "verification": SequenceSpec("verification_requests", "verification_id", "VER-", 7),
}


def format_sequence_id(kind: str, number: int) -> str:
    return SEQUENCES[kind].format(number)


def next_sequence_id(session: Session, kind: str) -> str:
    """Next ``MAX + 1`` ID of ``kind`` within its range. Reads only; never inserts.

    Must run inside the caller's ``BEGIN IMMEDIATE`` write transaction so that the read and the
    later insert are serialised (D3). Fixed-width zero-padded IDs make the lexical MAX numeric.
    """
    spec = SEQUENCES[kind]
    low, high = spec.format(spec.first), spec.format(spec.maximum)
    current = session.execute(
        text(
            f"SELECT MAX({spec.column}) FROM {spec.table} "  # noqa: S608 - identifiers from SEQUENCES
            f"WHERE {spec.column} GLOB :pattern AND {spec.column} BETWEEN :low AND :high"
        ),
        {"pattern": spec.glob(), "low": low, "high": high},
    ).scalar()
    number = spec.first if current is None else int(current[len(spec.prefix):]) + 1
    return spec.format(number)
