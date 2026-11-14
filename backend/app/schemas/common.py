"""Shared API schema types (api_contract §1)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

from app.core.timeutil import format_utc, parse_utc, to_utc


def _parse_timestamp(value: Any) -> Any:
    """Requests: ISO-8601 with an explicit offset; naive values are rejected (D2)."""
    if isinstance(value, str):
        return parse_utc(value)
    if isinstance(value, datetime):
        return to_utc(value)
    return value


# Whole-second UTC, serialised as 'YYYY-MM-DDTHH:MM:SSZ' (D2, ADR-018).
UtcDateTime = Annotated[
    datetime,
    BeforeValidator(_parse_timestamp),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel, Generic[T]):
    """``{"items": [...], "total": n, "limit": l, "offset": o}`` (api_contract §1)."""

    items: list[T]
    total: int
    limit: int
    offset: int
