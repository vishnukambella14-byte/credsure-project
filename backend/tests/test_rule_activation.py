"""Rule activation service (FR-04, BR-06, BR-07 / D5, ADR-021)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.core.security import Actor, Role
from app.models import CredentialRule
from app.services import rules
from app.services.eligibility import evaluate
from tests.support import execute, insert, read_sessions, rule_row, scalar, ts

AA = Actor("AA-1", Role.ACADEMIC_ADMIN)
NOW = ts("2026-11-14 12:00:00")


def draft_v2(engine: Engine, **overrides) -> None:  # noqa: ANN003
    insert(engine, CredentialRule, rule_row(rule_id="RUL-00001-V2", version=2, status="draft",
                                            effective_from=None, activated_at=None, min_score=90.0, **overrides))


def activate(factories, rule_id="RUL-00001-V2", effective_from=None, actor=AA):  # noqa: ANN001, ANN201
    return rules.activate_rule(actor=actor, rule_id=rule_id, effective_from=effective_from,
                               write_factory=factories.write, clock=lambda: NOW)


def test_valid_activation_retires_predecessor(issuable: Engine, factories) -> None:  # noqa: ANN001
    draft_v2(issuable)
    outcome = activate(factories, effective_from=ts("2026-04-01 00:00:00"))
    assert (outcome.rule.status, outcome.retired_rule_id) == ("active", "RUL-00001-V1")
    assert outcome.rule.effective_from == ts("2026-04-01 00:00:00") and outcome.rule.activated_at == NOW
    assert scalar(issuable, "SELECT status || '|' || effective_to FROM credential_rules WHERE rule_id='RUL-00001-V1'") == (
        "retired|2026-04-01 00:00:00")
    events = scalar(issuable, "SELECT group_concat(event_type, ',') FROM "
                              "(SELECT event_type FROM audit_events ORDER BY event_id)")
    assert events == "rule.retired,rule.activated"


def test_default_effective_from_is_now(issuable: Engine, factories) -> None:  # noqa: ANN001
    draft_v2(issuable)
    assert activate(factories).rule.effective_from == NOW


def test_backdated_activation_over_recorded_completion_is_rejected(issuable: Engine, factories) -> None:  # noqa: ANN001
    draft_v2(issuable)
    with pytest.raises(DomainError) as caught:
        activate(factories, effective_from=ts("2026-02-01 00:00:00"))  # completion on 2026-03-01
    assert (caught.value.code, caught.value.status_code) == ("rule_effective_date_conflict", 409)
    assert caught.value.details == {"conflicting_completion_ids": ["CMP-0000001"]}
    assert scalar(issuable, "SELECT status FROM credential_rules WHERE rule_id='RUL-00001-V1'") == "active"
    assert scalar(issuable, "SELECT count(*) FROM audit_events") == 0


@pytest.mark.parametrize("when", ["2026-11-15 00:00:00", "2025-12-31 00:00:00"])
def test_invalid_effective_dates(issuable: Engine, factories, when: str) -> None:  # noqa: ANN001
    draft_v2(issuable)
    with pytest.raises(DomainError) as caught:  # future, or not after v1's 2026-01-01
        activate(factories, effective_from=ts(when))
    assert (caught.value.code, caught.value.status_code) == ("rule_effective_date_invalid", 422)


def test_only_existing_draft_rules_with_criteria(issuable: Engine, factories) -> None:  # noqa: ANN001
    for rule_id, code, status in [("RUL-00001-V9", "rule_not_found", 404), ("RUL-00001-V1", "rule_not_draft", 409)]:
        with pytest.raises(DomainError) as caught:
            activate(factories, rule_id=rule_id)
        assert (caught.value.code, caught.value.status_code) == (code, status)
    draft_v2(issuable, min_attendance_pct=None)
    execute(issuable, "UPDATE credential_rules SET min_score = NULL WHERE rule_id='RUL-00001-V2'")
    with pytest.raises(DomainError) as caught:
        activate(factories)
    assert caught.value.code == "rule_has_no_criteria"


def test_permission_is_checked(issuable: Engine, factories) -> None:  # noqa: ANN001
    draft_v2(issuable)
    with pytest.raises(DomainError) as caught:
        activate(factories, actor=Actor("PA-1", Role.PLATFORM_ADMIN))
    assert caught.value.code == "permission_denied"


def test_existing_completions_keep_their_rule(issuable: Engine, factories, key_store) -> None:  # noqa: ANN001
    draft_v2(issuable)
    activate(factories, effective_from=ts("2026-04-01 00:00:00"))  # v2 requires 90; March score 82.5
    with read_sessions(issuable)() as session:
        result = evaluate(session, "LRN-0000001", "CRS-00001", at=NOW, key_store=key_store)
    assert (result.rule_id, result.eligible) == ("RUL-00001-V1", True)


def test_active_rule_criteria_stay_frozen(issuable: Engine, factories) -> None:  # noqa: ANN001
    draft_v2(issuable)
    activate(factories)
    with pytest.raises(IntegrityError, match="credential_rules: criteria"):
        execute(issuable, "UPDATE credential_rules SET min_score = 10 WHERE rule_id='RUL-00001-V2'")
