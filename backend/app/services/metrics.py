"""Metrics computed on demand from source tables (FR-20, BR-23, ADR-011, api_contract §4.7).

No metrics table, cache or stored aggregate: every figure is a SQL aggregate over the source
records at request time, and every rate states its numerator and denominator.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.models import CompletionRecord, Course, Credential, CredentialRule, Learner, VerificationRequest
from app.models.enums import CompletionStatus, CredentialStatus, RuleStatus, VerificationResult


def _rate(numerator: int, denominator: int, numerator_text: str, denominator_text: str) -> dict[str, Any]:
    value = round(numerator / denominator, 4) if denominator else 0.0
    return {"value": value, "numerator": numerator_text, "denominator": denominator_text}


def summary(session: Session, clock: Callable[[], datetime] = utc_now) -> dict[str, Any]:
    """The ``GET /api/v1/metrics/summary`` body."""
    def count(statement) -> int:  # noqa: ANN001
        return int(session.execute(statement).scalar_one())

    credentials = count(select(func.count()).select_from(Credential))
    revoked = count(select(func.count()).where(Credential.status == CredentialStatus.REVOKED.value))
    verifications = count(select(func.count()).select_from(VerificationRequest))
    completed = count(select(func.count()).where(CompletionRecord.status == CompletionStatus.COMPLETED.value))

    by_result = {result.value: 0 for result in VerificationResult}
    for result, number in session.execute(
            select(VerificationRequest.result, func.count()).group_by(VerificationRequest.result)):
        by_result[result] = number
    month = func.substr(Credential.issued_at, 1, 7)  # stored 'YYYY-MM-DD HH:MM:SS' (D2)
    by_month = [{"month": m, "count": n} for m, n in session.execute(
        select(month, func.count()).group_by(month).order_by(month))]

    return {
        "computed_at": clock(),
        "counts": {
            "learners": count(select(func.count()).select_from(Learner)),
            "courses": count(select(func.count()).select_from(Course)),
            "active_rules": count(select(func.count()).where(CredentialRule.status == RuleStatus.ACTIVE.value)),
            "completions": count(select(func.count()).select_from(CompletionRecord)),
            "credentials": credentials,
            "credentials_active": count(select(func.count()).where(Credential.status == CredentialStatus.ACTIVE.value)),
            "credentials_revoked": revoked,
            "verifications": verifications,
        },
        "rates": {
            "revocation_rate": _rate(revoked, credentials, "revoked credentials", "all credentials"),
            "verification_failure_rate": _rate(
                verifications - by_result[VerificationResult.VALID.value], verifications,
                "verifications with result != valid", "all verifications"),
            "issuance_conversion_rate": _rate(credentials, completed, "credentials", "completed completion records"),
        },
        "verifications_by_result": by_result,
        "credentials_issued_by_month": by_month,
    }
