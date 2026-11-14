"""SQLite connection and transaction configuration (D3) on the real application engine."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database.session import (
    SessionLocal,
    WriteSessionLocal,
    build_engine,
    engine,
    write_engine,
)


def test_pragmas_on_every_connection() -> None:
    engine.dispose()
    connections = [engine.connect() for _ in range(3)]  # three distinct DBAPI connections
    try:
        dbapi_ids = set()
        for conn in connections:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA recursive_triggers").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            raw = conn.connection.dbapi_connection
            assert raw.isolation_level is None  # driver's implicit BEGIN disabled
            dbapi_ids.add(id(raw))
        assert len(dbapi_ids) == 3
    finally:
        for conn in connections:
            conn.close()


def test_write_session_holds_write_lock_from_first_statement() -> None:
    session = WriteSessionLocal()
    try:
        session.execute(text("SELECT 1"))  # a read, yet BEGIN IMMEDIATE already took the lock
        raw = session.connection().connection.dbapi_connection
        assert raw.in_transaction
        other = write_engine(build_engine(str(engine.url), busy_timeout_ms=200))
        try:
            with pytest.raises(OperationalError, match="database is locked"):
                with other.connect() as conn:
                    conn.execute(text("SELECT 1"))
        finally:
            other.dispose()
    finally:
        session.rollback()
        session.close()


def test_read_session_does_not_take_the_write_lock() -> None:
    reader = SessionLocal()
    try:
        reader.execute(text("SELECT 1"))  # deferred BEGIN: shared snapshot only
        other = write_engine(build_engine(str(engine.url), busy_timeout_ms=200))
        try:
            with other.connect() as conn:
                conn.execute(text("SELECT 1"))  # acquires BEGIN IMMEDIATE without waiting
        finally:
            other.dispose()
    finally:
        reader.close()


def test_readers_are_not_blocked_by_a_writer() -> None:
    writer = WriteSessionLocal()
    try:
        writer.execute(text("SELECT 1"))
        with SessionLocal() as reader:
            assert reader.execute(text("SELECT 1")).scalar() == 1
    finally:
        writer.rollback()
        writer.close()


def test_second_writer_waits_until_first_commits() -> None:
    first = WriteSessionLocal()
    first.execute(text("SELECT 1"))
    waiting_engine = write_engine(build_engine(str(engine.url), busy_timeout_ms=5000))
    outcome: dict[str, float | str] = {}

    def second_writer() -> None:
        started = time.monotonic()
        try:
            with waiting_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            outcome["waited"] = time.monotonic() - started
        except Exception as exc:  # pragma: no cover - reported via assertion
            outcome["error"] = repr(exc)

    thread = threading.Thread(target=second_writer)
    try:
        thread.start()
        time.sleep(0.4)
        assert thread.is_alive(), "second writer acquired the lock while the first held it"
        first.rollback()
        first.close()
        thread.join(timeout=10)
    finally:
        waiting_engine.dispose()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["waited"] >= 0.35
