"""Rule-based anomaly detection (A1–A4) and evaluation against ground truth (ADR-012, FR-21/22)."""

from app.services.anomalies.detectors import ANOMALY_TYPES, Finding, run_detection, run_detectors
from app.services.anomalies.evaluation import evaluate_profile, load_labels

__all__ = ["ANOMALY_TYPES", "Finding", "evaluate_profile", "load_labels", "run_detection", "run_detectors"]
