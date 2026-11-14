"""Shared time helpers and the UTCDateTime column type (D2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import StatementError

from app.core.timeutil import format_utc, parse_utc, to_utc, utc_now
from app.models import Issuer, Learner
from tests.support import insert, issuer_row, learner_row, scalar

UTC = timezone.utc


def test_format_is_whole_second_utc_with_z() -> None:
    value = datetime(2026, 3, 14, 15, 0, 0, 999999, tzinfo=timezone(timedelta(hours=5)))
    assert format_utc(value) == "2026-03-14T10:00:00Z"


@pytest.mark.parametrize(
    ("text_value", "expected"),
    [
        ("2026-03-14T10:00:00Z", datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)),
        ("2026-03-14T10:00:00.999Z", datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)),  # truncated
        ("2026-03-14T15:30:00+05:30", datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)),
        ("2026-03-14 10:00:00-01:00", datetime(2026, 3, 14, 11, 0, 0, tzinfo=UTC)),
    ],
)
def test_parse_accepts_explicit_offsets(text_value: str, expected: datetime) -> None:
    assert parse_utc(text_value) == expected


@pytest.mark.parametrize("bad", ["2026-03-14T10:00:00", "2026-03-14", "14/03/2026 10:00", ""])
def test_parse_rejects_naive_or_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_utc(bad)


def test_to_utc_rejects_naive_and_truncates() -> None:
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 1, 1))
    assert to_utc(datetime(2026, 1, 1, 0, 0, 0, 500000, tzinfo=UTC)).microsecond == 0
    assert utc_now().microsecond == 0 and utc_now().tzinfo == UTC


def test_round_trip_through_format_and_parse() -> None:
    now = utc_now()
    assert parse_utc(format_utc(now)) == now


def test_utc_datetime_stores_whole_second_text_and_returns_aware_utc(migrated_engine: Engine) -> None:
    created = datetime(2026, 3, 14, 15, 30, 0, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    insert(migrated_engine, Learner, learner_row(created_at=created, updated_at=created))
    assert scalar(migrated_engine, "SELECT created_at FROM learners") == "2026-03-14 10:00:00"
    with migrated_engine.connect() as conn:
        loaded = conn.execute(select(Learner.__table__.c.created_at)).scalar_one()
    assert loaded == datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)
    assert loaded.tzinfo == UTC


def test_utc_datetime_rejects_naive_values(migrated_engine: Engine) -> None:
    with pytest.raises(StatementError, match="naive datetimes are not accepted"):
        insert(migrated_engine, Issuer, issuer_row(valid_from=datetime(2026, 1, 1)))
