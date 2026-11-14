"""Route dependencies: session factories, key store, clock, pagination and scope enforcement.

Tests override ``get_session_factories``, ``get_key_store``, ``get_clock`` (and the security
module's ``get_denial_recorder``) to run routes against a temporary migrated database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Query, Request
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.security import Actor, DenialRecorder, Permission, ensure_permitted
from app.core.timeutil import utc_now
from app.database.session import SessionLocal, WriteSessionLocal
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from app.services.crypto import KeyStore, default_key_store


@dataclass(frozen=True)
class SessionFactories:
    read: Callable[[], Session]
    write: Callable[[], Session]


def get_session_factories() -> SessionFactories:
    return SessionFactories(read=SessionLocal, write=WriteSessionLocal)


def get_key_store() -> KeyStore:
    return default_key_store()


def get_clock() -> Callable[[], datetime]:
    return utc_now


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def get_pagination(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def enforce_scope(
    request: Request,
    actor: Actor,
    permission: Permission,
    record_denial: DenialRecorder,
    *,
    learner_id: str | None = None,
    lead_instructor_id: str | None = None,
) -> None:
    """Scope check once the resource is known; a denial is audited as ``access.denied``."""
    try:
        ensure_permitted(actor, permission, learner_id=learner_id, lead_instructor_id=lead_instructor_id)
    except DomainError:
        try:
            record_denial(actor, permission, request.url.path)
        except Exception:  # pragma: no cover - the 403 must still be returned
            pass
        raise
