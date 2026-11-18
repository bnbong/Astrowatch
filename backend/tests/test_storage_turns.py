"""대화 턴 저장소 — 화면 복원용 (GET/DELETE /api/sessions)."""

from __future__ import annotations

from datetime import datetime

from astrowatch.schemas.api import ChatResponse
from astrowatch.schemas.domain import Conditions, Intent
from astrowatch.storage import turns
from astrowatch.storage.db import connect


def _resp(sid: str, intent: Intent = Intent.PLAN, answer: str = "응답") -> ChatResponse:
    return ChatResponse(session_id=sid, intent=intent, answer=answer, conditions=Conditions(region="서울"))


def _set_ts(sid: str, seq: int, ts: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE turns SET ts = ? WHERE session_id = ? AND seq = ?", (ts, sid, seq))


def test_append_increments_seq_per_session():
    assert turns.append_turn("a", "첫 질문", _resp("a")) == 1
    assert turns.append_turn("a", "둘째", _resp("a")) == 2
    assert turns.append_turn("b", "다른 세션", _resp("b")) == 1
    assert turns.append_turn("a", "셋째", _resp("a")) == 3


def test_list_sessions_aggregates_and_sorts():
    long_msg = "가" * 80
    turns.append_turn("a", long_msg, _resp("a", Intent.PLAN))
    turns.append_turn("a", "같은 날 강원도로", _resp("a", Intent.CHAT))
    turns.append_turn("b", "안녕", _resp("b", Intent.CHAT))
    _set_ts("a", 1, "2026-11-18T20:00:00")
    _set_ts("a", 2, "2026-11-18T20:05:00")
    _set_ts("b", 1, "2026-11-18T21:00:00")

    sessions = turns.list_sessions()
    assert [s.session_id for s in sessions] == ["b", "a"]  # last_at 내림차순
    a = sessions[1]
    assert a.title == "가" * 60
    assert a.turn_count == 2
    assert a.started_at == datetime(2026, 11, 18, 20, 0)
    assert a.last_at == datetime(2026, 11, 18, 20, 5)
    assert a.last_intent == "chat"


def test_detail_in_seq_order_with_response():
    turns.append_turn("a", "첫 질문", _resp("a", answer="하나"))
    turns.append_turn("a", "둘째 질문", _resp("a", answer="둘"))
    turns.append_turn("b", "남의 것", _resp("b"))

    detail = turns.get_session_detail("a")
    assert detail is not None
    assert detail.title == "첫 질문"
    assert [t.seq for t in detail.turns] == [1, 2]
    assert [t.response.answer for t in detail.turns] == ["하나", "둘"]
    assert detail.turns[0].response.conditions.region == "서울"
    assert detail.started_at <= detail.last_at
    assert turns.get_session_detail("없음") is None


def test_delete_session_turns():
    turns.append_turn("a", "q1", _resp("a"))
    turns.append_turn("a", "q2", _resp("a"))
    turns.append_turn("b", "q", _resp("b"))
    assert turns.delete_session_turns("a") == 2
    assert turns.get_session_detail("a") is None
    assert [s.session_id for s in turns.list_sessions()] == ["b"]
    assert turns.delete_session_turns("a") == 0


def test_broken_json_turn_skipped(caplog):
    turns.append_turn("a", "정상", _resp("a", answer="ok"))
    turns.append_turn("a", "옛 형식", _resp("a"))
    with connect() as conn:
        conn.execute("UPDATE turns SET response_json = '{\"old\": 1}' WHERE session_id = 'a' AND seq = 2")

    with caplog.at_level("WARNING", logger="astrowatch.storage.turns"):
        detail = turns.get_session_detail("a")
        [summary] = turns.list_sessions()
    assert detail is not None and [t.seq for t in detail.turns] == [1]
    assert summary.turn_count == 2 and summary.last_intent is None
    assert any("역직렬화" in r.getMessage() for r in caplog.records)
