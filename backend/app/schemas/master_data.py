"""Master-data schemas: issuers, learners, courses, draft rules, completion records (T05)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel, UtcDateTime


# ------------------------------------------------------------------ issuers ----------------------
class IssuerCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    website_url: str | None = Field(default=None, max_length=500)
    supersedes_issuer_id: str | None = Field(default=None, max_length=20)  # ADR-006 issuer versions


class IssuerOut(BaseModel):
    issuer_id: str
    name: str
    legal_name: str
    country_code: str | None
    website_url: str | None
    signing_key_id: str
    public_key: str
    key_algorithm: str
    status: str
    supersedes_issuer_id: str | None
    valid_from: UtcDateTime
    valid_to: UtcDateTime | None
    record_source: str
    created_at: UtcDateTime


class PublicKeyOut(BaseModel):
    """api_contract §3: ``{issuer_id, signing_key_id, algorithm, public_key}``."""

    issuer_id: str
    signing_key_id: str
    algorithm: str
    public_key: str


# ------------------------------------------------------------------ learners ---------------------
class LearnerCreate(ApiModel):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    external_ref: str | None = Field(default=None, min_length=1, max_length=50)


class LearnerOut(BaseModel):
    learner_id: str
    external_ref: str | None
    given_name: str
    family_name: str
    email: str
    status: str
    record_source: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ------------------------------------------------------------------ rules -------------------------
class RuleCreate(ApiModel):
    credential_type: Literal["certificate", "badge"]
    min_score: float | None = Field(default=None, ge=0, le=100)
    min_attendance_pct: float | None = Field(default=None, ge=0, le=100)
    validity_days: int | None = Field(default=None, gt=0)
    description: str | None = None


class RuleOut(BaseModel):
    rule_id: str
    course_id: str
    version: int
    credential_type: str
    min_score: float | None
    min_attendance_pct: float | None
    validity_days: int | None
    status: str
    effective_from: UtcDateTime | None
    effective_to: UtcDateTime | None
    description: str | None
    created_by: str
    created_at: UtcDateTime
    activated_at: UtcDateTime | None


# ------------------------------------------------------------------ courses ----------------------
class CourseCreate(ApiModel):
    course_code: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    credit_hours: int | None = Field(default=None, ge=0)
    issuer_id: str = Field(min_length=1, max_length=20)
    lead_instructor_id: str | None = Field(default=None, max_length=64)


class CourseOut(BaseModel):
    course_id: str
    course_code: str
    title: str
    description: str | None
    credit_hours: int | None
    issuer_id: str
    lead_instructor_id: str | None
    status: str
    record_source: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class CourseDetailOut(CourseOut):
    active_rule: RuleOut | None  # "Detail including current active rule"


# ------------------------------------------------------------------ completions -------------------
class CompletionCreate(ApiModel):
    learner_id: str = Field(min_length=1, max_length=20)
    course_id: str = Field(min_length=1, max_length=20)
    attempt_number: int = Field(ge=1)
    status: Literal["in_progress", "completed", "failed", "withdrawn"]
    # Range checks happen in the service so they return the documented completion_invalid_state.
    score: float | None = None
    attendance_pct: float | None = None
    completed_at: UtcDateTime | None = None


class CompletionOut(BaseModel):
    completion_id: str
    learner_id: str
    course_id: str
    attempt_number: int
    status: str
    score: float | None
    attendance_pct: float | None
    completed_at: UtcDateTime | None
    assessed_by: str | None
    recorded_at: UtcDateTime
    updated_at: UtcDateTime
    record_source: str
    applicable_rule_id: str | None  # derived (D5), never stored
    warnings: list[str]
