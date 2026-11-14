"""Profiles, fixed constants and file layout (data_dictionary §5, PRD §7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import BACKEND_DIR

SEED = 20261022
GENERATOR_VERSION = "1.0"
# Every timestamp is derived from fixed instants, never the wall clock (BR-25).
REFERENCE = datetime(2026, 10, 22, tzinfo=timezone.utc)
DATASET_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
ISSUER_RETIREMENT = datetime(2025, 1, 1, tzinfo=timezone.utc)  # ISS-901 retired, superseded by ISS-902

DATA_DIR = BACKEND_DIR.parent / "data"
GENERATED_ROOT = DATA_DIR / "generated"
GROUND_TRUTH_ROOT = DATA_DIR / "ground_truth"

SYSTEM_ACTOR = "system"
ADMIN_ACTOR = "AA-SYN-001"

# Load order (FK and D5 order, data_dictionary §5.1). Attempts are replayed, not bulk-loaded.
TABLE_FILES: tuple[tuple[str, str], ...] = (
    ("issuers.csv", "issuers"),
    ("learners.csv", "learners"),
    ("courses.csv", "courses"),
    ("credential_rules.csv", "credential_rules"),
    ("completion_records.csv", "completion_records"),
    ("credentials.csv", "credentials"),
    ("revocations.csv", "revocations"),
    ("verification_requests.csv", "verification_requests"),
)
ATTEMPTS_FILE = "issuance_attempts.csv"
ATTEMPT_COLUMNS = ("attempt_id", "credential_id", "learner_id", "course_id", "attempted_at", "actor_id")
LABELS_FILE = "anomaly_labels.csv"
LABEL_COLUMNS = ("label_id", "record_type", "record_id", "anomaly_type", "variant", "description")
MANIFEST_FILE = "manifest.json"
# CSV columns = table columns minus these (data_dictionary §5.1).
EXCLUDED_COLUMNS = frozenset({"record_source", "created_at", "updated_at"})

A1, A2, A3, A4 = (
    "issued_before_completion", "duplicate_credential_id",
    "revoked_verified_as_valid", "tampered_payload_not_detected",
)
VARIANTS = {
    A1: ("issued_before_completed_at", "completion_not_completed"),
    A2: ("exact_duplicate", "case_variant", "same_learner_retry"),
    A3: ("status_not_updated", "cache_stale"),
    A4: ("score_changed", "name_changed", "course_changed", "expiry_extended"),
}


@dataclass(frozen=True)
class Profile:
    name: str
    learners: int
    courses: int
    active_issuers: int  # plus one retired issuer (ISS-901) superseded by ISS-902
    credentials_total: int  # after replay of the valid issuance attempts
    valid_attempts: int
    verifications: int
    pending_completions: int  # completed, eligible, not yet issued
    other_completions: int  # failed / in progress / withdrawn (incl. failed first attempts)
    a1: int
    a2: int
    a3: int
    a4: int
    hard_negatives: int

    @property
    def baseline_credentials(self) -> int:
        return self.credentials_total - self.valid_attempts


PROFILES: dict[str, Profile] = {
    "starter": Profile("starter", 200, 20, 3, 100, 2, 50, 30, 20, 3, 2, 2, 2, 2),
    "full": Profile("full", 20_000, 1_000, 5, 10_000, 50, 5_000, 3_000, 2_000, 100, 50, 50, 50, 50),
}


def profile_dirs(profile: str, generated_root: Path = GENERATED_ROOT,
                 ground_truth_root: Path = GROUND_TRUTH_ROOT) -> tuple[Path, Path]:
    return generated_root / profile, ground_truth_root / profile
