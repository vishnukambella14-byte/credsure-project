"""Custom column types (decision D2, ADR-018)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.core.timeutil import to_utc

# Stored text: 'YYYY-MM-DD HH:MM:SS' (UTC, no microseconds) so lexical order is chronological.
SQLITE_STORAGE_FORMAT = "%(year)04d-%(month)02d-%(day)02d %(hour)02d:%(minute)02d:%(second)02d"
SQLITE_STORAGE_REGEXP = r"(\d+)-(\d+)-(\d+) (\d+):(\d+):(\d+)"


class UTCDateTime(TypeDecorator[datetime]):
    """Aware-UTC datetimes in Python; naive whole-second UTC text in SQLite.

    * Binding rejects naive datetimes, converts to UTC and truncates to whole seconds.
    * Results come back as aware UTC datetimes.

    Migrations render this type as ``sa.DateTime()`` (see ``database/migrations/env.py``):
    the DDL is identical and migrations must not import application types.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(
                sqlite.DATETIME(storage_format=SQLITE_STORAGE_FORMAT, regexp=SQLITE_STORAGE_REGEXP)
            )
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return to_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)
