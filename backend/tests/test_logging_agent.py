"""로깅 서브 Agent (F3013) — EventBus 구독 · 실패 격리 · LLM 미사용."""

from __future__ import annotations

from pathlib import Path

import pytest

from astrowatch.events.bus import EventType
from astrowatch.logging_agent import agent as agent_mod
from astrowatch.logging_agent.agent import LoggingAgent, start_logging_agent, stop_logging_agent
from astrowatch.storage import run_logs


@pytest.fixture(autouse=True)
def _reset_singleton():
    stop_logging_agent()
    yield
    stop_logging_agent()


def test_publish_saves_rows(fresh_bus):
    agent = LoggingAgent()
    agent.attach(fresh_bus)
    assert agent.attached
    fresh_bus.publish("s1", EventType.SESSION_START, payload={"message": "안녕"})
    fresh_bus.publish("s1", EventType.TOOL_CALL, stage="execute", tool="search_sky_events")
    fresh_bus.publish("s1", EventType.STAGE_END, stage="execute", payload={"duration_ms": 12})
    events = run_logs.get_session_events("s1")
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.type for e in events] == ["session_start", "tool_call", "stage_end"]


def test_bad_db_path_never_raises(fresh_bus, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = blocker / "sub" / "db.sqlite"  # 파일 아래 경로 — 만들 수 없다
    agent = LoggingAgent(db_path=bad)
    agent.attach(fresh_bus)  # 초기화 실패도 삼킨다
    first = fresh_bus.publish("s1", EventType.TOOL_CALL)  # 구독자 경유 저장 실패
    agent.handle(first)  # 직접 호출해도 예외 없음
    ev = fresh_bus.publish("s1", EventType.TOOL_RESULT, payload={"ok": True})
    assert ev.seq == first.seq + 1
    assert not bad.exists()


def test_detach_stops_saving(fresh_bus):
    agent = LoggingAgent()
    agent.attach(fresh_bus)
    fresh_bus.publish("s1", EventType.TOOL_CALL)
    agent.detach()
    assert not agent.attached and fresh_bus.subscriber_count() == 0
    fresh_bus.publish("s1", EventType.TOOL_CALL)
    assert len(run_logs.get_session_events("s1")) == 1


def test_singleton(fresh_bus):
    a = start_logging_agent(fresh_bus)
    b = start_logging_agent(fresh_bus)
    assert a is b and fresh_bus.subscriber_count() == 1
    fresh_bus.publish("s2", EventType.REPLAN, payload={"round": 1})
    assert run_logs.list_sessions()[0].replans == 1
    stop_logging_agent()
    assert fresh_bus.subscriber_count() == 0


def test_no_llm_dependency():
    src = Path(agent_mod.__file__).read_text(encoding="utf-8")
    for name in ("langchain", "openai", "ChatOpenAI"):
        assert name not in src
    import astrowatch.storage.run_logs as rl

    assert "langchain" not in Path(rl.__file__).read_text(encoding="utf-8")


def test_seq_continues_after_restart(tmp_path):
    """재시작(새 버스·새 Agent) 뒤에도 같은 세션의 seq 가 이어져 INSERT OR IGNORE 에 먹히지 않는다."""
    from astrowatch.events.bus import EventBus, EventType
    from astrowatch.logging_agent.agent import LoggingAgent
    from astrowatch.storage.run_logs import get_session_events

    db = tmp_path / "restart.db"
    bus1, agent1 = EventBus(), LoggingAgent(db)
    agent1.attach(bus1)
    bus1.publish("s", EventType.STAGE_START)
    bus1.publish("s", EventType.STAGE_END)

    bus2, agent2 = EventBus(), LoggingAgent(db)
    agent2.attach(bus2)
    event = bus2.publish("s", EventType.STAGE_START)
    assert event.seq == 3
    assert [e.seq for e in get_session_events("s", db_path=db)] == [1, 2, 3]
    agent2.detach()
    assert bus2.seq_seed is None
