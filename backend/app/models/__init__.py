"""ORM models package.

Every model module is imported here so ``Base.metadata`` is complete for Alembic autogenerate
(``database/migrations/env.py`` imports ``Base`` from this package).
"""

from app.database.base import Base
from app.models.audit_event import AuditEvent
from app.models.completion_record import CompletionRecord
from app.models.course import Course
from app.models.credential import Credential
from app.models.credential_rule import CredentialRule
from app.models.issuer import Issuer
from app.models.learner import Learner
from app.models.revocation import Revocation
from app.models.verification_request import VerificationRequest

__all__ = [
    "AuditEvent",
    "Base",
    "CompletionRecord",
    "Course",
    "Credential",
    "CredentialRule",
    "Issuer",
    "Learner",
    "Revocation",
    "VerificationRequest",
]
