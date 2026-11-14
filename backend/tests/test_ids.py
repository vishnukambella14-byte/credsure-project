"""ID formats (data_dictionary §1) and sequence allocation without persistence."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import Engine

from app.models import CredentialRule, Issuer, Learner
from app.services import ids
from tests.support import course_row, insert, issuer_row, learner_row, read_sessions, rule_row, scalar

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_credential_ids_match_documented_format() -> None:
    generated = {ids.new_credential_id() for _ in range(2000)}
    assert len(generated) == 2000
    for value in generated:
        assert re.fullmatch(r"CRED-[0-9A-HJKMNP-TV-Z]{12}", value)
        assert len(value) == 17 and set(value[5:]) <= CROCKFORD
        assert not set(value[5:]) & set("ILOU")


def test_attempt_refs_match_documented_format() -> None:
    value = ids.new_attempt_ref()
    assert re.fullmatch(r"ATT-[0-9A-HJKMNP-TV-Z]{12}", value) and ids.is_attempt_ref(value)
    assert ids.synthetic_attempt_ref(4) == "ATT-000004" and ids.is_attempt_ref("ATT-000004")
    with pytest.raises(ids.IdSpaceExhausted):
        ids.synthetic_attempt_ref(1_000_000)


def test_random_ids_use_the_secrets_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.secrets, "choice", lambda alphabet: alphabet[-1])
    assert ids.new_credential_id() == "CRED-ZZZZZZZZZZZZ"
    assert ids.new_attempt_ref() == "ATT-ZZZZZZZZZZZZ"


def test_normalisation_and_validation() -> None:
    assert ids.normalize_credential_id("  cred-7k2m9qxr4t1b ") == "CRED-7K2M9QXR4T1B"
    assert ids.is_credential_id("CRED-7K2M9QXR4T1B")
    assert not ids.is_credential_id("CRED-7K2M9QXR4T1I")  # I is not Crockford


@pytest.mark.parametrize(
    ("kind", "number", "expected"),
    [("issuer", 1, "ISS-001"), ("synthetic_issuer", 901, "ISS-901"), ("learner", 42, "LRN-0000042"),
     ("course", 17, "CRS-00017"), ("completion", 1234, "CMP-0001234"), ("revocation", 31, "REV-000031"),
     ("verification", 7, "VER-0000007")],
)
def test_sequence_formats(kind: str, number: int, expected: str) -> None:
    assert ids.format_sequence_id(kind, number) == expected


def test_derived_ids() -> None:
    assert ids.rule_id_for("CRS-00017", 2) == "RUL-00017-V2"
    assert ids.signing_key_id_for("ISS-001") == "ISS-001-K1"
    with pytest.raises(ValueError):
        ids.rule_id_for("CRS-00017", 0)
    with pytest.raises(ids.IdSpaceExhausted):
        ids.format_sequence_id("issuer", 900)  # 900+ is the synthetic range (D8)


def test_rule_id_for_satisfies_the_database_check(migrated_engine: Engine) -> None:
    insert(migrated_engine, Issuer, issuer_row())
    from app.models import Course

    insert(migrated_engine, Course, course_row())
    insert(migrated_engine, CredentialRule, rule_row(rule_id=ids.rule_id_for("CRS-00001", 1)))


def test_next_sequence_id_reads_but_never_persists(migrated_engine: Engine) -> None:
    session = read_sessions(migrated_engine)()
    try:
        assert ids.next_sequence_id(session, "learner") == "LRN-0000001"
        assert ids.next_sequence_id(session, "learner") == "LRN-0000001"  # no reservation
        assert scalar(migrated_engine, "SELECT count(*) FROM learners") == 0
    finally:
        session.close()
    insert(migrated_engine, Learner, learner_row(learner_id="LRN-0000041"))
    with read_sessions(migrated_engine)() as session:
        assert ids.next_sequence_id(session, "learner") == "LRN-0000042"


def test_issuer_sequences_respect_platform_and_synthetic_ranges(migrated_engine: Engine) -> None:
    insert(migrated_engine, Issuer, issuer_row(
        issuer_id="ISS-901", signing_key_id="ISS-901-K1", public_key="PK-901", record_source="synthetic_import"))
    with read_sessions(migrated_engine)() as session:
        assert ids.next_sequence_id(session, "issuer") == "ISS-001"  # synthetic IDs ignored
        assert ids.next_sequence_id(session, "synthetic_issuer") == "ISS-902"
    insert(migrated_engine, Issuer, issuer_row(issuer_id="ISS-899", signing_key_id="ISS-899-K1", public_key="PK-899"))
    with read_sessions(migrated_engine)() as session, pytest.raises(ids.IdSpaceExhausted):
        ids.next_sequence_id(session, "issuer")
