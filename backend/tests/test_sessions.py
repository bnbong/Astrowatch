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
    sessions.save_session(sid, conditions=cond, memory_summary="요약", last_recommended=[_judged()], history=hist)

    rec = sessions.load_session(sid)
    assert rec is not None
    assert rec.conditions == cond
    assert rec.conditions.date_from == dt.date(2026, 12, 14)
    assert rec.memory_summary == "요약"
    assert rec.last_recommended == [_judged()]
    assert rec.last_recommended[0].candidate.date == dt.date(2026, 12, 14)
    assert rec.last_recommended[0].time_window.end == 1560
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
                          last_recommended=[_judged()], history=[{"role": "user", "content": "부산"}])
    rec = sessions.load_session(sid)
    assert rec.conditions.region == "부산"
    assert rec.memory_summary == "두 번째"
    assert len(rec.last_recommended) == 1
    assert rec.created_at == first.created_at
    assert sessions.delete_session(sid) is True
    assert sessions.load_session(sid) is None
