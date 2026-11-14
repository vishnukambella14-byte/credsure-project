"""Enumerations stored as TEXT with named CHECK constraints (data_dictionary §1).

Python 3.10 has no ``StrEnum``; ``class X(str, Enum)`` gives the same behaviour.
"""

from __future__ import annotations

from enum import Enum


class RecordSource(str, Enum):
    PLATFORM = "platform"
    SYNTHETIC_IMPORT = "synthetic_import"


class IssuerStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"


class LearnerStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class CourseStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CredentialType(str, Enum):
    CERTIFICATE = "certificate"
    BADGE = "badge"


class RuleStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class CompletionStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class CredentialStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class RevocationReason(str, Enum):
    ISSUED_IN_ERROR = "issued_in_error"
    ACADEMIC_MISCONDUCT = "academic_misconduct"
    LEARNER_REQUEST = "learner_request"
    DATA_CORRECTION = "data_correction"
    FRAUD = "fraud"
    OTHER = "other"


class VerificationPurpose(str, Enum):
    EMPLOYMENT = "employment"
    ADMISSION = "admission"
    LICENSING = "licensing"
    OTHER = "other"


class VerificationChannel(str, Enum):
    API = "api"
    PORTAL = "portal"


class VerificationResult(str, Enum):
    VALID = "valid"
    REVOKED = "revoked"
    EXPIRED = "expired"
    TAMPERED = "tampered"
    INVALID_SIGNATURE = "invalid_signature"
    NOT_FOUND = "not_found"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    REJECTED = "rejected"
    ERROR = "error"


# Terminal audit events of an issuance attempt (D7): attempt_ref required, at most one per attempt.
TERMINAL_ISSUANCE_EVENTS: tuple[str, ...] = ("credential.issued", "credential.issue_rejected")

# Crockford base32 character class used by credential IDs and platform attempt references.
CROCKFORD_CLASS = "[0-9A-HJKMNP-TV-Z]"


def sql_in(column: str, values: type[Enum] | tuple[str, ...]) -> str:
    """Render ``column IN ('a','b')`` for a CHECK constraint from an Enum or a tuple of strings."""
    items = [member.value for member in values] if isinstance(values, type) else list(values)
    return f"{column} IN ({','.join(repr(item) for item in items)})"
