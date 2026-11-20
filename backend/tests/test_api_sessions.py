"""대화 세션 API — 실제 임시 DB(conftest) 위에서 라우터를 검사한다. run_logs 는 유지돼야 한다."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from astrowatch.events.bus import Event, EventType
from astrowatch.schemas.api import ChatResponse
from astrowatch.schemas.domain import Conditions, Intent
from astrowatch.storage import run_logs, sessions, turns


def _resp(sid: str, answer: str) -> ChatResponse:
    return ChatResponse(session_id=sid, intent=Intent.PLAN, answer=answer, conditions=Conditions(region="서울"))


@pytest.fixture
def client(monkeypatch):
    from astrowatch.app import main

    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    with TestClient(main.create_app(), raise_server_exceptions=False, headers={"X-Requested-With": "Astrowatch"}) as c:
        yield c


@pytest.fixture
def seeded():
    turns.append_turn("sess-001", "이번 주말 서울 유성우", _resp("sess-001", "하나"))
    turns.append_turn("sess-001", "강원도로 바꿔줘", _resp("sess-001", "둘"))
    sessions.save_session("sess-001", conditions=Conditions(), memory_summary=None, last_recommended=[], history=[])
    run_logs.append_event(Event(session_id="sess-001", type=EventType.SESSION_START, seq=1,
                                ts=datetime(2026, 11, 18, tzinfo=timezone.utc), payload={"message": "x"}))


def test_list_and_detail(client, seeded):
    res = client.get("/api/sessions")
    assert res.status_code == 200
    [s] = res.json()
    assert s["session_id"] == "sess-001" and s["turn_count"] == 2
    assert s["title"] == "이번 주말 서울 유성우" and s["last_intent"] == "plan"

    res = client.get("/api/sessions/sess-001")
    assert res.status_code == 200
    body = res.json()
    assert [t["seq"] for t in body["turns"]] == [1, 2]
    assert body["turns"][1]["user_message"] == "강원도로 바꿔줘"
    assert body["turns"][1]["response"]["answer"] == "둘"


def test_detail_404(client):
    assert client.get("/api/sessions/missing-01").status_code == 404
    assert client.delete("/api/sessions/missing-01").status_code == 404


def test_delete_keeps_run_logs(client, seeded):
    res = client.delete("/api/sessions/sess-001")
    assert res.status_code == 204
    assert client.get("/api/sessions/sess-001").status_code == 404
    assert client.get("/api/sessions").json() == []
    assert sessions.load_session("sess-001") is None
    assert len(run_logs.get_session_events("sess-001")) == 1  # 실행 로그는 남는다
    assert client.get("/api/logs/sess-001").status_code == 200


def test_delete_session_row_only(client):
    sessions.save_session("sess-002", conditions=Conditions(), memory_summary=None, last_recommended=[], history=[])
    assert client.delete("/api/sessions/sess-002").status_code == 204


def test_delete_clears_session_state_and_tool_cache(client, seeded):
    """회귀: 삭제 뒤 같은 id 로 이어 말해도 옛 조건과 Tool 캐시를 쓰지 않는다."""
    from astrowatch.agent import memory
    from astrowatch.tools.base import ok

    memory.reset_memory()
    sessions.save_session("sess-001", conditions=Conditions(region="강원"), memory_summary="요약",
                          last_recommended=["유성우"], history=[{"role": "user", "content": "강원"}])
    memory.remember_tool_results("sess-001", {"search_sky_events|k": ok({"n": 1})})
    assert memory.load("sess-001").conditions.region == "강원"
    assert client.delete("/api/sessions/sess-001").status_code == 204
    assert memory.load("sess-001").conditions == Conditions()
    assert memory.load("sess-001").last_recommended == []
    assert memory.cached_tool_results("sess-001") == {}
