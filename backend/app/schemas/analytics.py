"""Metrics and anomaly schemas (api_contract §4.7, §4.8)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import UtcDateTime


class Rate(BaseModel):
    value: float
    numerator: str
    denominator: str


class MonthCount(BaseModel):
    month: str
    count: int


class MetricsSummaryOut(BaseModel):
    computed_at: UtcDateTime
    counts: dict[str, int]
    rates: dict[str, Rate]
    verifications_by_result: dict[str, int]
    credentials_issued_by_month: list[MonthCount]


class FindingOut(BaseModel):
    record_type: str
    record_id: str
    anomaly_type: str
    evidence: dict[str, Any]


class AnomalyRunOut(BaseModel):
    run_at: UtcDateTime
    findings_by_type: dict[str, int]
    findings: list[FindingOut]


class TypeScores(BaseModel):
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float


class RecordRef(BaseModel):
    record_type: str
    record_id: str
    anomaly_type: str


class EvaluationOut(BaseModel):
    profile: str
    by_type: dict[str, TypeScores]
    overall: TypeScores
    false_positives: list[RecordRef]
    false_negatives: list[RecordRef]
