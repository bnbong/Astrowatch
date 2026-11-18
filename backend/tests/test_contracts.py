"""계약 파일 단위 테스트 — Tool 봉투 · 레지스트리 · EventBus · 지역 표 · 조건 병합 · DB 스키마."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from pydantic import BaseModel

from astrowatch.events.bus import EventBus, EventType
from astrowatch.schemas.domain import Conditions, Window
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.registry import ToolRegistry


class _In(BaseModel):
    x: int


class _Out(BaseModel):
    y: int


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="double", description="x 를 두 배로 할 때", kind=ToolKind.EXTERNAL, input_model=_In, output_model=_Out)
    def double(args: _In) -> ToolResult[_Out]:
        if args.x < 0:
            return fail(ErrorCode.NO_RESULT, "음수는 없다")
        if args.x == 99:
            raise RuntimeError("secret-key-123 boom")
        return ok(_Out(y=args.x * 2), note="추정" if args.x == 0 else None)

    return reg


def test_envelope_success_and_note():
    reg = _make_registry()
    r = reg.invoke("double", {"x": 0})
    assert r.ok and r.data.y == 0 and r.note == "추정"
    assert r.as_event_payload()["ok"] is True


def test_envelope_failure_code():
    r = _make_registry().invoke("double", {"x": -1})
    assert not r.ok and r.code is ErrorCode.NO_RESULT and r.code.retriable


def test_exception_becomes_envelope_not_raise():
    r = _make_registry().invoke("double", {"x": 99})
    assert not r.ok and r.code is ErrorCode.INTERNAL
    assert "boom" in r.error.message


def test_bad_input_and_unknown_tool():
    reg = _make_registry()
    assert reg.invoke("double", {"x": "abc"}).code is ErrorCode.BAD_INPUT
    assert reg.invoke("nope", {}).code is ErrorCode.BAD_INPUT


def test_openai_tool_schema_has_only_name_desc_params():
    spec = _make_registry().openai_tools()[0]
    assert spec["type"] == "function"
    assert set(spec["function"]) == {"name", "description", "parameters"}
    assert "x" in spec["function"]["parameters"]["properties"]


def test_duplicate_name_rejected():
    reg = _make_registry()
    with pytest.raises(ValueError):
        reg.register(reg.get("double"))


def test_bus_seq_per_session_and_subscriber_errors_swallowed():
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e))
    bus.subscribe(lambda e: 1 / 0)  # 죽는 구독자
    e1 = bus.publish("s1", EventType.STAGE_START, stage="analyze")
    e2 = bus.publish("s1", EventType.STAGE_END, stage="analyze", payload={"duration_ms": 3})
    e3 = bus.publish("s2", EventType.STAGE_START, stage="analyze")
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 1)
    assert len(seen) == 3 and seen[1].payload["duration_ms"] == 3


def test_bus_without_subscribers_still_returns_event():
    bus = EventBus()
    e = bus.publish("s", EventType.TOOL_CALL, tool="double", payload={"args": {"x": 1}})
    assert e.type is EventType.TOOL_CALL and bus.subscriber_count() == 0


def test_regions_lookup_and_grid():
    from astrowatch.data.regions import find_region, latlon_to_grid, nearby_regions

    seoul = find_region("서울에서")
    assert seoul and seoul.grid == (60, 127)           # 기상청 공식 서울 격자
    assert find_region("강원도").name == "춘천"
    assert find_region("대전 근처").name == "대전"
    assert find_region("화성") is None
    assert latlon_to_grid(36.3504, 127.3845) == (67, 100)  # 대전
    near = nearby_regions(seoul, 2)
    assert [r.name for r in near] == ["인천", "수원"]


def test_conditions_merge_keeps_unchanged_and_tracks_fixed():
    base = Conditions(date_from=date(2026, 12, 14), region="서울", start_minute=22 * 60, end_minute=26 * 60, fixed=["date_from"])
    update = Conditions(region="강원도", fixed=["region"])
    merged = base.merged(update)
    assert merged.region == "강원도" and merged.date_from == date(2026, 12, 14)
    assert merged.start_minute == 1320 and merged.fixed == ["date_from", "region"]


def test_conditions_missing_rules():
    assert Conditions(end_minute=23 * 60).missing_for_plan() == ["region", "date_from", "start_minute"]
    assert Conditions(region="서울", date_from=date(2026, 12, 14)).missing_for_plan() == []


def test_window_math_and_labels():
    dark = Window(start=18 * 60 + 48, end=24 * 60 + 6 * 60 + 5)
    mine = Window(start=22 * 60, end=26 * 60)
    w = dark.intersect(mine)
    assert (w.start, w.end, w.minutes) == (1320, 1560, 240)
    assert Window.label(1560) == "02:00+1" and Window.label(1128) == "18:48"
    assert dark.intersect(Window(start=100, end=200)).minutes == 0


def test_db_schema_and_run_logs_immutable(tmp_path):
    from astrowatch.storage.db import connect, init_db

    path = init_db(tmp_path / "x.db")
    with connect(path) as conn:
        conn.execute("INSERT INTO run_logs(session_id, seq, ts, type) VALUES ('s', 1, 't', 'stage_start')")
    with pytest.raises(sqlite3.IntegrityError):
        with connect(path) as conn:
            conn.execute("UPDATE run_logs SET type='x' WHERE session_id='s'")
    with pytest.raises(sqlite3.IntegrityError):
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO plans(event_title, start_dt, end_dt, location_label, created_at, updated_at) "
                "VALUES ('e', '2026-12-14T23:00', '2026-12-14T22:00', '서울', 't', 't')"
            )
