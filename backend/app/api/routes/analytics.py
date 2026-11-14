"""Metrics and anomaly routes (T07, api_contract §3, §4.7, §4.8)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionFactories, get_clock, get_session_factories
from app.core.security import Actor, Permission, require_permission
from app.datagen.config import GROUND_TRUTH_ROOT
from app.schemas.analytics import AnomalyRunOut, EvaluationOut, FindingOut, MetricsSummaryOut
from app.services import metrics
from app.services.anomalies import evaluate_profile, run_detection

router = APIRouter()


def get_ground_truth_root() -> Path:
    """Where ``<profile>/anomaly_labels.csv`` lives (tests override this)."""
    return GROUND_TRUTH_ROOT


@router.get("/metrics/summary", response_model=MetricsSummaryOut, tags=["metrics"])
def metrics_summary(
    actor: Actor = Depends(require_permission(Permission.METRICS_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> MetricsSummaryOut:
    with sessions.read() as session:
        return MetricsSummaryOut(**metrics.summary(session, clock))


@router.post("/anomalies/run", response_model=AnomalyRunOut, tags=["anomalies"])
def run_anomalies(
    actor: Actor = Depends(require_permission(Permission.ANOMALY_RUN)),
    sessions: SessionFactories = Depends(get_session_factories),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> AnomalyRunOut:
    result = run_detection(actor=actor, read_factory=sessions.read, write_factory=sessions.write, clock=clock)
    return AnomalyRunOut(run_at=result["run_at"], findings_by_type=result["findings_by_type"],
                         findings=[FindingOut(**asdict(f)) for f in result["findings"]])


@router.get("/anomalies/evaluation", response_model=EvaluationOut, tags=["anomalies"])
def anomaly_evaluation(
    profile: str = Query(..., pattern="^(starter|full)$"),
    actor: Actor = Depends(require_permission(Permission.ANOMALY_READ)),
    sessions: SessionFactories = Depends(get_session_factories),
    ground_truth_root: Path = Depends(get_ground_truth_root),
) -> EvaluationOut:
    with sessions.read() as session:
        return EvaluationOut(**evaluate_profile(session, profile, ground_truth_root))
