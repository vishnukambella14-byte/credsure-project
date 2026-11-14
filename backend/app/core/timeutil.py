"""The only time helpers used by CredSure (decision D2, ADR-018).

Every persisted and signed timestamp is UTC with whole-second precision. The API, services,
generator, loader and verification logic must use these functions instead of formatting or
parsing datetimes themselves.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

WIRE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# ISO-8601 with an explicit offset; fractional seconds optional (truncated, never rounded).
_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})(?:\.\d+)?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})$"
)


def to_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime truncated to whole seconds.

    Naive datetimes are rejected because their meaning is ambiguous.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("naive datetimes are not accepted; supply an explicit UTC offset")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def utc_now() -> datetime:
    """Current time as aware UTC, whole seconds. Services receive this as an injectable clock."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_utc(value: datetime) -> str:
    """Format as ``YYYY-MM-DDTHH:MM:SSZ``: the single wire, payload, audit-hash and CSV format."""
    return to_utc(value).strftime(WIRE_FORMAT)


def parse_utc(text: str) -> datetime:
    """Parse ISO-8601 with an explicit offset (``Z`` or ``±HH:MM``) into aware UTC, whole seconds.

    Fractional seconds are truncated. Strings without an offset are rejected.
    """
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")
    match = _ISO_RE.match(text.strip())
    if match is None:
        raise ValueError(f"not an ISO-8601 timestamp with an explicit UTC offset: {text!r}")
    offset = "+00:00" if match["offset"] == "Z" else match["offset"]
    parsed = datetime.fromisoformat(f"{match['date']}T{match['time']}{offset}")
    return to_utc(parsed)
