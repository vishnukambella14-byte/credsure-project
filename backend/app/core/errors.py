"""Global exception handlers that return a consistent JSON error envelope.

Shape::

    {"error": {"code": "<machine_code>", "message": "<human message>", "details": ...}}

* ``DomainError`` carries a code from the exception catalogue (docs/exception_catalogue.md §1);
  its HTTP status and default message come from ``ERROR_CATALOGUE``.
* ``IntegrityError`` is mapped explicitly by constraint or trigger name (catalogue §1.7, D3).
  Unmapped integrity errors are logged and returned as ``500 internal_server_error``.
* Responses never contain SQL text, parameters, database paths, key material or stack traces.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# code -> (HTTP status, default message). Source: docs/exception_catalogue.md §1.1–§1.6.
ERROR_CATALOGUE: dict[str, tuple[int, str]] = {
    # 1.1 Generic
    "validation_error": (422, "Request validation failed."),
    "actor_required": (401, "X-Actor-Id and X-Actor-Role headers with a known role are required."),
    "permission_denied": (403, "The actor's role is not allowed to perform this action."),
    "internal_server_error": (500, "An unexpected error occurred."),
    # 1.2 Master data
    "learner_not_found": (404, "Learner not found."),
    "course_not_found": (404, "Course not found."),
    "issuer_not_found": (404, "Issuer not found."),
    "rule_not_found": (404, "Credential rule not found."),
    "completion_not_found": (404, "Completion record not found."),
    "credential_not_found": (404, "Credential not found."),
    "learner_email_exists": (409, "A learner with this email already exists."),
    "learner_external_ref_exists": (409, "A learner with this student number already exists."),
    "course_code_exists": (409, "A course with this code already exists."),
    "issuer_inactive": (422, "The issuer is retired."),
    "issuer_immutable": (409, "Only an issuer's status and valid_to may change."),
    "rule_version_immutable": (409, "Active and retired rule versions cannot be changed."),
    "rule_not_draft": (409, "Only a draft rule can be activated."),
    "rule_has_no_criteria": (422, "A rule needs min_score or min_attendance_pct before activation."),
    "rule_effective_date_invalid": (422, "The rule's effective_from date is not valid."),
    "rule_effective_date_conflict": (
        409, "The activation would change the applicable rule of a recorded completion."),
    # 1.3 Completion records
    "completion_invalid_state": (422, "The completion record is not consistent."),
    "completion_in_future": (422, "completed_at is later than the current time."),
    "completion_attempt_exists": (409, "This completion attempt already exists."),
    "completion_locked": (409, "The completion record is referenced by an active credential."),
    "course_scope_denied": (403, "The instructor is not the lead instructor of this course."),
    # 1.4 Issuance
    "duplicate_attempt_ref": (409, "This attempt reference has already been used."),
    "duplicate_credential_id": (409, "A credential with this ID already exists."),
    "not_eligible": (422, "Learner is not eligible for this credential."),
    "credential_already_issued": (409, "The learner already holds an active credential for this course."),
    "signing_key_unavailable": (503, "The issuer's signing key is not available."),
    # 1.5 Revocation
    "credential_already_revoked": (409, "The credential has already been revoked."),
    "revocation_reason_required": (422, "A valid revocation reason is required."),
    # 1.6 Audit and data operations
    "audit_immutable": (409, "Audit events cannot be modified or deleted."),
    "record_immutable": (409, "This record cannot be modified."),
    "database_busy": (503, "The database is busy. Please retry."),
    "ground_truth_unavailable": (404, "Ground truth for this profile is not available."),
}


class DomainError(Exception):
    """An expected, documented failure. ``code`` must exist in ``ERROR_CATALOGUE``."""

    def __init__(self, code: str, message: str | None = None, details: Any = None) -> None:
        if code not in ERROR_CATALOGUE:
            raise ValueError(f"undocumented error code: {code!r}")
        status_code, default_message = ERROR_CATALOGUE[code]
        super().__init__(message or default_message)
        self.code = code
        self.status_code = status_code
        self.message = message or default_message
        self.details = details

    def __repr__(self) -> str:
        return f"DomainError({self.code!r}, status={self.status_code})"


# --------------------------------------------------------------- IntegrityError mapping (§1.7) ----
# SQLite reports UNIQUE failures as "UNIQUE constraint failed: <table.col>[, <table.col>...]" (or
# "index '<name>'" for expression indexes), CHECK failures as "CHECK constraint failed: <name>",
# and trigger aborts with the RAISE() message. Matching is exact for UNIQUE column lists.
_UNIQUE_MAP: dict[str, str] = {
    "credentials.credential_id": "duplicate_credential_id",
    "credentials.learner_id, credentials.course_id": "credential_already_issued",
    "audit_events.attempt_ref": "duplicate_attempt_ref",
    "revocations.credential_id": "credential_already_revoked",
    "credential_rules.course_id": "rule_version_immutable",  # uq_credential_rules_active_course
    "completion_records.learner_id, completion_records.course_id, completion_records.attempt_number":
        "completion_attempt_exists",
    "learners.email": "learner_email_exists",
    "learners.external_ref": "learner_external_ref_exists",
    "courses.course_code": "course_code_exists",
    # Chain integrity: serialised writes make these defects, never expected paths.
    "audit_events.previous_hash": "database_busy",
    "index 'uq_audit_events_single_genesis'": "database_busy",
}
_CHECK_MAP: dict[str, str] = {
    "ck_credentials_credential_id_format": "validation_error",
    "ck_credential_rules_window": "rule_effective_date_invalid",
    "ck_issuers_synthetic_range": "validation_error",
}
# Trigger RAISE() messages (prefix match), data_dictionary §6.
_TRIGGER_MAP: tuple[tuple[str, str], ...] = (
    ("rule_effective_date_conflict", "rule_effective_date_conflict"),
    ("completion_locked", "completion_locked"),
    ("credential_rules: criteria are immutable", "rule_version_immutable"),
    ("audit_events is append-only", "audit_immutable"),
    ("audit_events: previous_hash must equal the current chain head", "database_busy"),
    ("credentials: only", "record_immutable"),
    ("credentials cannot be deleted", "record_immutable"),
    ("revocations are immutable", "record_immutable"),
    ("issuers: only", "record_immutable"),
    ("issuers cannot be deleted", "record_immutable"),
    ("verification_requests are immutable", "record_immutable"),
)
_UNIQUE_PREFIX = "UNIQUE constraint failed: "
_CHECK_PREFIX = "CHECK constraint failed: "


def _driver_message(exc: IntegrityError) -> str:
    return str(exc.orig) if exc.orig is not None else ""


def map_integrity_error(exc: IntegrityError) -> DomainError:
    """Translate a database integrity failure into a documented DomainError (catalogue §1.7)."""
    message = _driver_message(exc).strip()
    code: str | None = None
    if message.startswith(_UNIQUE_PREFIX):
        code = _UNIQUE_MAP.get(message[len(_UNIQUE_PREFIX):].strip())
    elif message.startswith(_CHECK_PREFIX):
        code = _CHECK_MAP.get(message[len(_CHECK_PREFIX):].strip())
    else:
        code = next((c for prefix, c in _TRIGGER_MAP if message.startswith(prefix)), None)
    # FOREIGN KEY failures and anything unmapped: a service defect, not a client error.
    return DomainError(code or "internal_server_error")


# --------------------------------------------------------------- handlers ----------------------------
def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status_code, content=body)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request validation failed.",
        details=exc.errors(),
    )


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("%s on %s %s: %s", exc.code, request.method, request.url.path, exc.message)
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    error = map_integrity_error(exc)
    if error.status_code >= 500:
        # Server log only; the response never contains SQL, parameters or driver text.
        logger.error(
            "Unmapped or defect integrity error (%s) on %s %s: %s",
            error.code, request.method, request.url.path, _driver_message(exc),
        )
    return error_response(error.status_code, error.code, error.message)


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    if "database is locked" in str(exc.orig):
        error = DomainError("database_busy")
        logger.warning("Write lock timeout on %s %s", request.method, request.url.path)
    else:
        error = DomainError("internal_server_error")
        logger.exception("Database error on %s %s", request.method, request.url.path)
    return error_response(error.status_code, error.code, error.message)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_server_error",
        "An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(OperationalError, operational_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
