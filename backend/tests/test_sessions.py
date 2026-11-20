"""대화 맥락 저장소 (F3010)."""

from __future__ import annotations

import datetime as dt

from astrowatch.schemas.domain import Candidate, Conditions, JudgedCandidate, Verdict, Window
from astrowatch.storage import sessions
from astrowatch.tools.contracts import EventKind, SkyCondition


def _judged() -> JudgedCandidate:
    return JudgedCandidate(
        candidate=Candidate(
            event_id="geminids-2026", title="쌍둥이자리 유성우 극대", kind=EventKind.METEOR,
            date=dt.date(2026, 12, 14), region="서울", lat=37.56, lon=126.97, event_minute=1500,
        ),
        verdict=Verdict.FIT,
        time_window=Window(start=1320, end=1560),
        clear_minutes=200,
        worst_sky=SkyCondition.CLEAR,
        reasons=["맑은 시간 200분"],
    )


def test_roundtrip():
    sid = sessions.new_session_id()
    assert len(sid) == 12 and sid != sessions.new_session_id()
    cond = Conditions(date_from=dt.date(2026, 12, 14), date_to=dt.date(2026, 12, 14), region="서울",
                      event_kind=EventKind.METEOR, start_minute=1320, fixed=["date_from"])
    hist = [{"role": "user", "content": "서울에서 유성우"}, {"role": "assistant", "content": "추천합니다"}]
    sessions.save_session(sid, conditions=cond, memory_summary="요약", last_recommended=["쌍둥이자리 유성우 극대"], history=hist)

    rec = sessions.load_session(sid)
    assert rec is not None
    assert rec.conditions == cond
    assert rec.conditions.date_from == dt.date(2026, 12, 14)
    assert rec.memory_summary == "요약"
    assert rec.last_recommended == ["쌍둥이자리 유성우 극대"]
    assert rec.history == hist
    assert isinstance(rec.created_at, dt.datetime)


def test_missing_session():
    assert sessions.load_session("nope") is None
    assert sessions.delete_session("nope") is False


def test_upsert_and_delete():
    sid = "abc"
    sessions.save_session(sid, conditions=Conditions(region="서울"), memory_summary=None,
                          last_recommended=[], history=[])
    first = sessions.load_session(sid)
    sessions.save_session(sid, conditions=Conditions(region="부산"), memory_summary="두 번째",
                          last_recommended=["쌍둥이자리 유성우 극대"], history=[{"role": "user", "content": "부산"}])
    rec = sessions.load_session(sid)
    assert rec.conditions.region == "부산"
    assert rec.memory_summary == "두 번째"
    assert len(rec.last_recommended) == 1
    assert rec.created_at == first.created_at
    assert sessions.delete_session(sid) is True
    assert sessions.load_session(sid) is None


def test_legacy_row_with_full_candidates_loads_titles():
    """옛 행(last_recommended_json 에 JudgedCandidate 전체)도 읽혀야 한다. 제목만 남긴다."""
    import json

    from astrowatch.storage.db import connect
    from astrowatch.storage.plans import ensure_db

    sessions.save_session("old-row", conditions=Conditions(), memory_summary=None, last_recommended=[], history=[])
    with connect(ensure_db(None)) as conn:
        conn.execute("UPDATE sessions SET last_recommended_json = ? WHERE session_id = ?",
                     (json.dumps([_judged().model_dump(mode="json")], ensure_ascii=False), "old-row"))
    rec = sessions.load_session("old-row")
    assert rec.last_recommended == ["쌍둥이자리 유성우 극대"]


# ── agent.memory: SQLite 가 세션 상태의 기준 ─────────────────

def test_memory_load_after_delete_has_no_previous_conditions():
    """회귀: 저장 → 삭제 뒤 load 가 메모리에 남은 옛 조건을 돌려주면 안 된다."""
    from astrowatch.agent import memory

    memory.reset_memory()
    s = memory.Session(session_id="del-001", conditions=Conditions(region="강원"), last_recommended=["유성우"],
                       history=[{"role": "user", "content": "강원"}])
    assert memory.save(s) is True
    assert memory.load("del-001").conditions.region == "강원"
    assert sessions.delete_session("del-001") is True
    again = memory.load("del-001")
    assert again.conditions == Conditions() and again.history == [] and again.last_recommended == []


def test_memory_save_failure_is_reported_not_hidden(monkeypatch, caplog):
    from astrowatch.agent import memory

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(sessions, "save_session", boom)
    with caplog.at_level("WARNING", logger="astrowatch.agent.memory"):
        assert memory.save(memory.Session(session_id="fail-01", conditions=Conditions(region="서울"))) is False
    assert "세션 저장 실패" in caplog.text
    assert memory.load("fail-01").conditions == Conditions()     # 실패한 저장을 메모리에서 대신 내주지 않는다


def test_memory_load_failure_returns_empty_session(monkeypatch, caplog):
    from astrowatch.agent import memory

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(sessions, "load_session", boom)
    with caplog.at_level("WARNING", logger="astrowatch.agent.memory"):
        assert memory.load("any-01").conditions == Conditions()
    assert "세션 읽기 실패" in caplog.text


def test_memory_forget_clears_only_that_sessions_tool_cache():
    from astrowatch.agent import memory
    from astrowatch.tools.base import ok

    memory.reset_memory()
    memory.remember_tool_results("a", {"search_sky_events|x": ok({"n": 1})})
    memory.remember_tool_results("b", {"search_sky_events|x": ok({"n": 2})})
    memory.forget("a")
    assert memory.cached_tool_results("a") == {}
    assert list(memory.cached_tool_results("b")) == ["search_sky_events|x"]
