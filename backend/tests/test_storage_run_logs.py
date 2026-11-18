"""실행 로그 저장소 (F3013~F3014)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from astrowatch.events.bus import Event, EventType
from astrowatch.storage import run_logs
from astrowatch.storage.db import connect

T0 = datetime(2026, 12, 14, 12, 0, tzinfo=timezone.utc)


def _ev(session: str, seq: int, type: EventType, **kw) -> Event:
    return Event(session_id=session, type=type, seq=seq, ts=T0 + timedelta(seconds=seq), **kw)


def _seed():
    run_logs.append_event(_ev("a", 1, EventType.SESSION_START, payload={"message": "이번 주말 서울 유성우"}))
    run_logs.append_event(_ev("a", 3, EventType.REPLAN, payload={"round": 1, "reason": "흐림"}))
    run_logs.append_event(_ev("a", 2, EventType.TOOL_CALL, stage="execute", tool="search_sky_events",
                              payload={"args": {"year": 2026}}))


def test_session_summary():
    _seed()
    [s] = run_logs.list_sessions()
    assert s.session_id == "a"
    assert s.event_count == 3
    assert s.tool_calls == 1
    assert s.replans == 1
    assert s.first_message == "이번 주말 서울 유성우"
    assert s.started_at == T0 + timedelta(seconds=1)
    assert s.last_at == T0 + timedelta(seconds=3)


def test_events_in_seq_order_and_isolated():
    _seed()
    run_logs.append_event(_ev("b", 1, EventType.TOOL_CALL, tool="x"))
    events = run_logs.get_session_events("a")
    assert [e.seq for e in events] == [1, 2, 3]
    assert all(e.session_id == "a" for e in events)
    assert events[1].tool == "search_sky_events" and events[1].payload == {"args": {"year": 2026}}
    assert events[0].payload["message"] == "이번 주말 서울 유성우"

    sessions = run_logs.list_sessions()
    assert {s.session_id for s in sessions} == {"a", "b"}
    assert sessions[0].session_id == "a"  # last_at 내림차순 (a 의 마지막이 더 늦다)
    b = next(s for s in sessions if s.session_id == "b")
    assert b.event_count == 1 and b.first_message is None


def test_delete_session():
    _seed()
    run_logs.append_event(_ev("b", 1, EventType.TOOL_CALL))
    assert run_logs.delete_session("a") is True
    assert run_logs.get_session_events("a") == []
    assert [s.session_id for s in run_logs.list_sessions()] == ["b"]
    assert run_logs.delete_session("a") is False


def test_update_blocked_by_trigger():
    _seed()
    with pytest.raises(sqlite3.IntegrityError):
        with connect() as conn:
            conn.execute("UPDATE run_logs SET type = 'error' WHERE session_id = 'a'")
    assert [e.type for e in run_logs.get_session_events("a")] == ["session_start", "tool_call", "replan"]


def test_no_update_function():
    assert not [n for n in dir(run_logs) if n.startswith(("update", "edit", "modify"))]


def test_duplicate_seq_ignored():
    run_logs.append_event(_ev("a", 1, EventType.TOOL_CALL, tool="first"))
    run_logs.append_event(_ev("a", 1, EventType.TOOL_CALL, tool="second"))
    events = run_logs.get_session_events("a")
    assert len(events) == 1 and events[0].tool == "first"


def test_payload_non_serializable_and_korean():
    run_logs.append_event(_ev("a", 1, EventType.TOOL_RESULT, payload={"when": datetime(2026, 1, 1), "msg": "맑음"}))
    [e] = run_logs.get_session_events("a")
    assert e.payload["msg"] == "맑음"
    assert e.payload["when"].startswith("2026-01-01")
