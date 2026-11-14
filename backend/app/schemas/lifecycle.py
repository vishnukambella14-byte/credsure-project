"""Eligibility, rule activation and audit schemas (api_contract §4.1, §4.6, §4.9)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import ApiModel, UtcDateTime


class EligibilityOut(BaseModel):
    learner_id: str
    course_id: str
    eligible: bool
    rule_id: str | None
    rule_version: int | None
    completion_id: str | None
    reasons: list[str]
    checks: dict[str, bool | None]
    evaluated_at: UtcDateTime


class ActivateRuleRequest(ApiModel):
    effective_from: UtcDateTime | None = None


class ActivateRuleResponse(BaseModel):
    rule_id: str
    status: str
    effective_from: UtcDateTime
    activated_at: UtcDateTime
    retired_rule_id: str | None


class AuditEventOut(BaseModel):
    event_id: int
    event_uuid: str
    event_type: str
    entity_type: str
    entity_id: str
    actor_id: str
    actor_role: str
    outcome: str
    reason_code: str | None
    attempt_ref: str | None
    details: dict[str, Any]
    occurred_at: UtcDateTime
    previous_hash: str | None
    event_hash: str


class ChainVerificationOut(BaseModel):
    valid: bool
    events_checked: int
    first_broken_event_id: int | None
