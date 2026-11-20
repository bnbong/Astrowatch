"""메인 Agent 흐름 (F3005~F3011) — fake Tool 4개 + fake LLM. 네트워크·키 없음."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pytest

from astrowatch.agent import memory
from astrowatch.agent.llm import set_chat_model_override
from astrowatch.agent.service import run_turn
from astrowatch.agent.state import cache_key
from astrowatch.events.bus import EventType
from astrowatch.schemas.domain import Intent, Verdict
from astrowatch.tools import sky_object  # 모듈 import 시 전역 레지스트리에 등록 — fresh_registry 전에 끝내 둔다
from astrowatch.tools.base import ErrorCode, ToolKind, fail, ok
from astrowatch.tools.contracts import (
    EventKind,
    HourlySky,
    NightWindowInput,
    NightWindowOutput,
    ObservatoryInput,
    ObservatoryOutput,
    Place,
    SkyCondition,
    SkyEvent,
    SkyEventsInput,
    SkyEventsOutput,
    SkyForecastInput,
    SkyForecastOutput,
)

PEAK = dt.date(2026, 12, 14)


# ── fake Tool ────────────────────────────────────────────────

@dataclass
class World:
    forecast: str = "out_of_range"          # out_of_range | cloudy | clear | cloudy_peak
    place_auth_fail: bool = False
    place_fail_outside_seoul: bool = False
    calls: Counter = field(default_factory=Counter)

    def count(self, tool: str) -> int:
        return sum(n for k, n in self.calls.items() if k.startswith(tool + "|"))


@pytest.fixture
def world(fresh_registry, fresh_bus):
    w = World()
    memory.reset_memory()

    def record(name: str, args: Any) -> None:
        w.calls[cache_key(name, args.model_dump(mode="json"))] += 1

    @fresh_registry.tool(name="search_sky_events", description="천문현상", kind=ToolKind.EXTERNAL,
                         input_model=SkyEventsInput, output_model=SkyEventsOutput)
    def _events(a: SkyEventsInput):
        record("search_sky_events", a)
        evs = []
        if (a.year, a.month) == (2026, 12):
            evs = [
                SkyEvent(event_id="20261214-1", date=PEAK, time="23:00", title="쌍둥이자리 유성우", kind=EventKind.METEOR),
                SkyEvent(event_id="20261221-1", date=dt.date(2026, 12, 21), time="18:03", title="동지", kind=EventKind.SEASON),
            ]
        evs = [e for e in evs if a.kind is None or e.kind == a.kind]
        return ok(SkyEventsOutput(year=a.year, month=a.month, requested_defaulted=False, events=evs))

    @fresh_registry.tool(name="get_night_window", description="박명", kind=ToolKind.EXTERNAL,
                         input_model=NightWindowInput, output_model=NightWindowOutput)
    def _night(a: NightWindowInput):
        record("get_night_window", a)
        return ok(NightWindowOutput(date=a.date, sunset="17:15", astro_dusk_end="18:48", astro_dawn_start="06:05",
                                    sunrise_next="07:40", moonrise="11:20", moonset="21:52", lun_age=5.5,
                                    dark_minutes=677))

    @fresh_registry.tool(name="get_sky_forecast", description="예보", kind=ToolKind.EXTERNAL,
                         input_model=SkyForecastInput, output_model=SkyForecastOutput)
    def _forecast(a: SkyForecastInput):
        record("get_sky_forecast", a)
        if w.forecast == "out_of_range":
            return fail(ErrorCode.OUT_OF_RANGE, "예보 범위 밖")
        cloudy = w.forecast == "cloudy" or (w.forecast == "cloudy_peak" and a.date == PEAK)
        sky = SkyCondition.CLOUDY if cloudy else SkyCondition.CLEAR
        return ok(SkyForecastOutput(date=a.date, source="mock", coverage="full",
                                    hourly=[HourlySky(hour=h, sky=sky, pop=10) for h in a.hours]))

    @fresh_registry.tool(name="search_observatory", description="장소", kind=ToolKind.EXTERNAL,
                         input_model=ObservatoryInput, output_model=ObservatoryOutput)
    def _place(a: ObservatoryInput):
        record("search_observatory", a)
        if w.place_auth_fail:
            return fail(ErrorCode.AUTH, "카카오 키 없음")
        if w.place_fail_outside_seoul and abs(a.lat - 37.5665) > 1e-6:
            return fail(ErrorCode.UPSTREAM, "카카오 오류 응답")
        return ok(ObservatoryOutput(query=a.query, radius_km=a.radius_km, places=[
            Place(place_id="1", name="테스트 천문대", address="서울", url="https://place.example/1", distance_m=5000)]))

    yield w


# ── fake LLM ─────────────────────────────────────────────────

def _analysis(intent: str, **kw) -> dict[str, Any]:
    base = dict(intent=intent, reason="fake 판단", reset_conditions=False, date_flexible=False, date_from=None, date_to=None,
                start_time=None, end_time=None, region=None, event_kind=None, event_query=None, place_query=None,
                max_distance_km=None, allow_partly_cloudy=None, avoid_moon=None, fixed=[], missing=[],
                fact_kind=None, date_invalid=None, single_night=False)
    base.update(kw)
    return base


R1_FIELDS = dict(date_from="2026-12-14", date_to="2026-12-14", start_time="22:00", end_time="02:00",
                 region="서울", event_kind="meteor", event_query="쌍둥이자리 유성우")


def fake_rules(text: str) -> dict[str, Any]:
    for key, fields in EXTRA_RULES:          # 리뷰 반례(R1~R5·S09) — 아래 일반 규칙보다 먼저
        if key in text:
            return _analysis(**fields)
    if "안녕" in text:
        return _analysis("chat")
    if "대전에서 밤 11시" in text:
        return _analysis("plan", region="대전", end_time="23:00")
    if "11시 전" in text:
        return _analysis("plan", end_time="23:00")
    if "글쎄" in text:
        return _analysis("clarify", missing=["start_minute"])
    if "화성" in text:
        return _analysis("fact", region="대전", event_query="화성", event_kind="other",
                         fact_kind="object_visibility")
    if "언제 떠" in text:
        return _analysis("fact", region="대전", event_query="안드로메다 은하", event_kind="deep_sky")
    if "안드로메다" in text:
        return _analysis("plan", region="대전", event_query="안드로메다 은하", event_kind="deep_sky",
                         date_flexible=True)
    if "강원도" in text:
        return _analysis("plan", region="강원도")
    if "이번 달 천문현상" in text:
        return _analysis("fact", date_from="2026-12-01", date_to="2026-12-01", fact_kind="events")
    extra: dict[str, Any] = {"place_query": "천문대", "max_distance_km": 20} if "천문대" in text else {}
    if "다른 날도" in text:
        extra["date_flexible"] = True
    return _analysis("plan", **R1_FIELDS, **extra)


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    model_name = "fake-llm"

    def __init__(self) -> None:
        self.structured_calls = 0
        self.text_calls = 0

    def with_structured_output(self, schema, **_kw):
        outer = self

        class _Runnable:
            def invoke(self, messages):
                outer.structured_calls += 1
                human = [m[1] for m in messages if m[0] == "human"][-1]
                return schema(**fake_rules(human))

        return _Runnable()

    def invoke(self, messages):
        self.text_calls += 1
        return _Msg("fake 응답입니다.")


@pytest.fixture
def llm():
    fake = FakeLLM()
    set_chat_model_override(fake)
    yield fake
    set_chat_model_override(None)


@pytest.fixture
def events(fresh_bus):
    got = []
    fresh_bus.subscribe(got.append)
    return got


R1_MSG = "12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시~새벽 2시에 볼 수 있어?"
FLEX_MSG = R1_MSG + " 구름 많으면 다른 날도 괜찮아"


def _stage_starts(evts, sid):
    return [e.stage for e in evts if e.session_id == sid and e.type == EventType.STAGE_START]


# ── 테스트 ───────────────────────────────────────────────────

def test_r1_flow(world, llm, events):
    res = run_turn(None, R1_MSG)
    assert res.intent == Intent.PLAN
    assert _stage_starts(events, res.session_id) == ["analyze", "plan", "execute", "judge", "respond"]
    assert len(res.recommended) == 1
    j = res.recommended[0]
    assert j.verdict == Verdict.CONDITIONAL
    assert (j.time_window.start, j.time_window.end) == (1320, 1560)
    assert res.conditions.start_minute == 1320 and res.conditions.end_minute == 1560
    assert res.replan_count == 0 and not res.replan_limit_reached
    types = [s.type for s in res.steps]
    assert "llm_decision" in types and "tool_call" in types and "replan" not in types
    assert res.answer == "fake 응답입니다."
    # 계획 순서: 현상 → 박명 → 예보
    tools = [s.tool for s in res.steps if s.type == "tool_call"]
    assert tools == ["search_sky_events", "get_night_window", "get_sky_forecast"]


def test_replan_reuses_cache(world, llm, events):
    world.forecast = "cloudy_peak"
    res = run_turn(None, FLEX_MSG)
    assert res.replan_count == 1
    assert [e.payload["strategy"] for e in events if e.type == EventType.REPLAN] == ["same_event_other_dates"]
    assert res.recommended and all(j.verdict == Verdict.FIT for j in res.recommended)
    assert any(j.candidate.date == PEAK and j.verdict == Verdict.UNFIT for j in res.excluded)
    # 같은 인자의 Tool 은 한 번만 호출됐다 (재계획에서 캐시 재사용)
    assert all(n == 1 for n in world.calls.values()), world.calls
    assert world.count("search_sky_events") == 1
    # 재계획 이후 단계에는 replan_round 가 붙는다
    assert any(s.replan_round == 1 and s.type == "tool_call" for s in res.steps)


def test_replan_limit(world, llm, events):
    world.forecast = "cloudy"
    res = run_turn(None, FLEX_MSG)
    replans = [e for e in events if e.type == EventType.REPLAN]
    assert len(replans) == 3
    assert [e.payload["round"] for e in replans] == [1, 2, 3]
    assert res.replan_limit_reached is True
    assert res.replan_count == 3 and res.replan_limit == 3 and res.stop_reason == "limit_reached"
    assert [e.payload["limit_reached"] for e in replans] == [False, False, True]
    assert not res.recommended
    assert all(n == 1 for n in world.calls.values())
    # 세션에는 재계획 전 사용자 조건이 남는다
    assert res.conditions.region == "서울"


def test_greeting_no_tools(world, llm, events):
    res = run_turn(None, "안녕, 이거 어떻게 써?")
    assert res.intent == Intent.CHAT
    assert sum(world.calls.values()) == 0
    plan_end = [e for e in events if e.type == EventType.STAGE_END and e.stage == "plan"]
    assert plan_end == []  # analyze → respond 로 바로
    assert _stage_starts(events, res.session_id) == ["analyze", "respond"]


def test_clarify_when_only_end_time(world, llm):
    res = run_turn(None, "밤 11시 전엔 들어와야 해")
    assert res.intent == Intent.CLARIFY
    assert set(res.needs_clarification) == {"start_minute", "region"}  # 날짜는 되묻지 않는다
    assert sum(world.calls.values()) == 0
    assert "몇 시부터" in res.answer and "어느 지역" in res.answer


def test_tool_auth_failure_continues(world, llm):
    world.place_auth_fail = True
    res = run_turn(None, "토요일 밤 서울 근처 천문대, 20km 이내")
    assert len(res.recommended) == 1
    assert res.places == []
    assert any("카카오" in w and "키" in w for w in res.warnings)
    assert all("Traceback" not in w and "http" not in w for w in res.warnings)
    assert world.count("search_observatory") == 1
    assert any(s.tool == "search_observatory" and s.ok is False for s in res.steps)


def test_runs_without_subscribers(world, llm, fresh_bus):
    assert fresh_bus.subscriber_count() == 0
    res = run_turn(None, R1_MSG)
    assert res.recommended and res.steps
    assert fresh_bus.subscriber_count() == 0  # 임시 구독자는 해제됨

    def broken(_e):
        raise RuntimeError("구독자 장애")

    fresh_bus.subscribe(broken)
    assert run_turn(None, R1_MSG).recommended


def test_follow_up_keeps_context(world, llm):
    first = run_turn(None, R1_MSG)
    second = run_turn(first.session_id, "같은 날 장소만 강원도로")
    c = second.conditions
    assert c.region == "강원도"
    assert c.date_from == PEAK and c.start_minute == 1320 and c.end_minute == 1560
    assert c.event_query == "쌍둥이자리 유성우"
    assert second.recommended and second.recommended[0].candidate.region == "춘천"
    # 현상 조회는 재사용, 박명은 지역이 바뀌어 다시 호출
    assert world.count("search_sky_events") == 1
    assert world.count("get_night_window") == 2


def test_no_llm_key_gives_usage(world, events):
    set_chat_model_override(None)
    res = run_turn(None, R1_MSG)
    assert res.intent == Intent.CHAT
    assert any("LLM API 키" in w for w in res.warnings)
    assert sum(world.calls.values()) == 0


def test_exception_still_returns_response(world, llm, monkeypatch):
    from astrowatch.agent import service

    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(service, "_invoke", boom)
    res = run_turn("s-err", R1_MSG)
    assert res.session_id == "s-err"
    assert "secret" not in res.answer and all("secret" not in w for w in res.warnings)


def test_single_fixed_date_skips_other_dates(world, llm, events):
    world.forecast = "cloudy"
    res = run_turn(None, R1_MSG)          # 날짜 하나 + 유연하다는 말 없음 → date_from 고정
    assert "date_from" in res.conditions.fixed
    strategies = [e.payload["strategy"] for e in events if e.type == EventType.REPLAN]
    assert "same_event_other_dates" not in strategies
    assert strategies == ["other_events_same_period", "nearby_region"]
    # 2회 재계획 후 전략 소진 — 상한(3회) 도달이 아니다 (R3)
    assert res.replan_count == 2 and res.replan_limit_reached is False
    assert res.stop_reason == "no_alternative" and res.replan_limit == 3


class _TextFails(FakeLLM):
    def invoke(self, messages):
        raise RuntimeError("텍스트 생성 실패")


def test_places_not_carried_after_region_change(world, events):
    """재계획으로 지역이 바뀐 뒤 그 지역 장소 검색이 실패하면 서울 장소를 붙이지 않는다."""
    set_chat_model_override(_TextFails())
    try:
        world.forecast = "cloudy"
        world.place_fail_outside_seoul = True
        res = run_turn(None, FLEX_MSG + " 천문대")
    finally:
        set_chat_model_override(None)
    assert any(e.payload["strategy"] == "nearby_region" for e in events if e.type == EventType.REPLAN)
    assert res.places == []
    assert "장소는 확인하지 못했습니다" in res.answer
    # 원래 요청 조건을 먼저, 재계획 내용을 그다음에 (8a)
    first = res.answer.splitlines()[0]
    assert first.startswith("요청 조건:") and "서울" in first and "2026-12-14" in first
    assert "재계획 1회차" in res.answer and "최종 탐색 조건" in res.answer
    assert "(검증용 모의 예보)" in res.answer


def test_clarify_time_label(world, llm):
    res = run_turn(None, "밤 11시 전엔 들어와야 해")
    assert "23:00까지" in res.answer and "-~" not in res.answer


def test_fact_uses_structured_summary(world):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "이번 달 천문현상 뭐 있어?")
    finally:
        set_chat_model_override(None)
    assert res.intent == Intent.FACT
    assert "천문현상 2건" in res.answer and "쌍둥이자리 유성우" in res.answer
    assert "truncated" not in res.answer


@pytest.fixture
def fixed_today(monkeypatch):
    from astrowatch.agent import nodes

    monkeypatch.setattr(nodes, "today", lambda: dt.date(2026, 12, 10))


ANDROMEDA = "대전에서 안드로메다 은하 보기 좋은 날 추천해줘"


def test_deep_sky_without_date_uses_default_range(world, events, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, ANDROMEDA)
    finally:
        set_chat_model_override(None)
    assert res.intent == Intent.PLAN and not res.needs_clarification
    assert res.conditions.date_from is None          # 사용자 조건에는 기본값을 남기지 않음
    assert world.count("get_night_window") == 10       # 10일 범위
    assert len(res.recommended) == 10
    assert all(j.candidate.kind == EventKind.DEEP_SKY and j.candidate.title == "안드로메다 은하"
               for j in res.recommended)
    assert "날짜를 지정하지 않아 2026-12-10~2026-12-19 범위에서 찾았습니다" in res.answer
    decision = next(e for e in events if e.type == EventType.LLM_DECISION)
    assert "날짜 미지정" in decision.payload["reason"]


def test_deep_sky_skips_other_events_replan(world, events, fixed_today):
    world.forecast = "cloudy"
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, ANDROMEDA)
    finally:
        set_chat_model_override(None)
    strategies = [e.payload["strategy"] for e in events if e.type == EventType.REPLAN]
    assert strategies == ["nearby_region"]       # ① 기본 범위라 생략, ② 천체 지정이라 생략
    assert res.replan_limit_reached is False and res.stop_reason == "no_alternative"   # 1회 후 소진 (R3)
    assert "합삭" in res.answer


def test_start_time_not_asked_twice(world, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        first = run_turn(None, "대전에서 밤 11시 전엔 들어와야 해")
        assert first.intent == Intent.CLARIFY and first.needs_clarification == ["start_minute"]
        second = run_turn(first.session_id, "글쎄")
    finally:
        set_chat_model_override(None)
    assert second.intent == Intent.PLAN
    assert "시작 시각을 알려 주지 않아" in second.answer
    assert second.conditions.region == "대전" and second.conditions.end_minute == 23 * 60
    assert second.recommended or second.excluded


# ── 천체 고도 (lookup_sky_object) ─────────────────────────────

@pytest.fixture
def with_object_tool(world, fresh_registry):
    """fake 세계에 lookup_sky_object 를 더한다 (실제 핸들러 — 로컬 계산이라 네트워크 없음)."""
    from astrowatch.tools.contracts import SkyObjectInput, SkyObjectOutput

    @fresh_registry.tool(name="lookup_sky_object", description="천체 고도", kind=ToolKind.LOCAL,
                         input_model=SkyObjectInput, output_model=SkyObjectOutput)
    def _obj(a: SkyObjectInput):
        world.calls[cache_key("lookup_sky_object", a.model_dump(mode="json"))] += 1
        return sky_object.lookup_sky_object.handler(a)

    return world


def test_deep_sky_uses_object_altitude(with_object_tool, events, fixed_today):
    world = with_object_tool
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, ANDROMEDA)
    finally:
        set_chat_model_override(None)
    assert world.count("lookup_sky_object") == 10                       # 날짜마다 1회
    tools = [s.tool for s in res.steps if s.type == "tool_call"]
    assert tools.index("lookup_sky_object") < tools.index("get_night_window")
    assert len(res.recommended) == 10
    for j in res.recommended:
        assert j.candidate.kind == EventKind.DEEP_SKY and j.candidate.title == "안드로메다 은하"
        # 어두운 창은 06:05 까지지만 M31 이 20° 아래로 내려가는 새벽 1~4시(12월 중순)에 창이 끝난다
        assert 1440 + 60 <= j.time_window.end <= 1440 + 240
        assert any("안드로메다 은하 고도 20° 이상" in r and "남중" in r for r in j.reasons)
        assert j.target_alt is not None and j.target_alt > 80


def test_object_not_found_is_unjudged(with_object_tool, events, fixed_today, monkeypatch):
    """R1: 지목한 천체가 현상 목록·카탈로그 모두에 없으면 어두운 창 전체로 추천하지 않는다 (이전: 충족 추천)."""
    from astrowatch.astro import catalog

    monkeypatch.setattr(catalog, "catalog", lambda: [])
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, ANDROMEDA)
    finally:
        set_chat_model_override(None)
    assert with_object_tool.count("lookup_sky_object") == 10
    assert with_object_tool.count("get_night_window") == 0
    assert res.recommended == []
    assert len(res.excluded) == 10 and all(j.verdict == Verdict.UNJUDGED for j in res.excluded)
    assert all("천문현상 목록과 천체 카탈로그에서 찾지 못해 판단하지 않음" in j.reasons[-1] for j in res.excluded)
    assert res.replan_count == 0 and res.stop_reason is None
    assert not [e for e in events if e.type == EventType.REPLAN]


def test_fact_object_rise_question(with_object_tool, llm, fixed_today, events):
    # fake LLM 이 fact_kind=null 을 줘도 event_query 가 카탈로그에 있으면 object_visibility 로 폴백
    res = run_turn(None, "안드로메다 은하 언제 떠?")
    assert res.intent == Intent.FACT
    tools = [s.tool for s in res.steps if s.type == "tool_call"]
    assert tools == ["lookup_sky_object", "get_night_window"]
    decision = next(e for e in events if e.type == EventType.LLM_DECISION)
    assert "fact_kind=object_visibility(규칙 폴백)" in decision.payload["reason"]


def test_fact_planet_best_time(with_object_tool, fixed_today, events):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "오늘 화성이 제일 잘 보이는 시간대")
    finally:
        set_chat_model_override(None)
    assert res.intent == Intent.FACT
    tools = [s.tool for s in res.steps if s.type == "tool_call"]
    assert tools == ["lookup_sky_object", "get_night_window"]
    assert with_object_tool.count("search_sky_events") == 0
    calls = [k for k in with_object_tool.calls if k.startswith("lookup_sky_object|")]
    assert '"date": "2026-12-10"' in calls[0] and '"name": "화성"' in calls[0]
    assert "남중" in res.answer and "어두운 시간 18:48~06:05+1" in res.answer
    assert "제일 잘 보이는 시간대" in res.answer or "낮에만" in res.answer or "오르지 않음" in res.answer
    assert "날씨" in res.answer
    decision = next(e for e in events if e.type == EventType.LLM_DECISION)
    assert "fact_kind=object_visibility(LLM)" in decision.payload["reason"]


def test_fact_events_single_call(world):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "이번 달 천문현상 뭐 있어?")
    finally:
        set_chat_model_override(None)
    assert [s.tool for s in res.steps if s.type == "tool_call"] == ["search_sky_events"]
    assert world.count("search_sky_events") == 1


# ── E2E 리뷰 반례 회귀 (R1~R5 · S09) ─────────────────────────

_SEOUL_21_23 = dict(date_from="2026-12-14", date_to="2026-12-14", start_time="21:00", end_time="23:00", region="서울")
EXTRA_RULES: list[tuple[str, dict[str, Any]]] = [
    ("XYZ-FAKE-999", dict(intent="plan", **_SEOUL_21_23, event_kind="other", event_query="XYZ-FAKE-999",
                          fixed=["event_query"])),
    ("안드로매다", dict(intent="plan", **_SEOUL_21_23, event_kind="deep_sky", event_query="안드로매다 은하")),
    ("일시오류천체", dict(intent="plan", **_SEOUL_21_23, event_kind="deep_sky", event_query="안드로메다 은하")),
    ("남십자자리", dict(intent="plan", **_SEOUL_21_23, event_kind="other", event_query="남십자자리",
                       fixed=["event_query"])),
    ("다 고정", dict(intent="plan", **R1_FIELDS, fixed=["event_query", "region"])),
    ("그냥 별", dict(intent="plan", **{**_SEOUL_21_23, "date_from": "2026-12-18", "date_to": "2026-12-18"})),
    ("2월 30일", dict(intent="plan", date_invalid="2026년 2월 30일", region="서울", start_time="21:00",
                     end_time="23:00")),
    ("2027-02-29", dict(intent="plan", date_from="2027-02-29", date_to="2027-02-29", region="서울",
                        start_time="21:00", end_time="23:00")),
    ("범위 역전", dict(intent="plan", date_from="2026-12-20", date_to="2026-12-14", region="서울",
                      start_time="21:00", end_time="23:00")),
    ("다음 날 새벽", dict(intent="plan", date_from="2026-12-14", date_to="2026-12-15", start_time="22:00", single_night=True,
                        end_time="02:00", region="서울", event_kind="meteor", fixed=["date_from", "date_to"])),
    ("20km 안 찾아줘", dict(intent="plan", date_from="2026-12-10", date_to="2026-12-10", region="대전",
                          place_query="천문대", max_distance_km=20)),
    ("천문대에서 별 보고 싶어", dict(intent="plan", region="대전", place_query="천문대")),
    ("캘린더", dict(intent="calendar")),
    ("블랙홀이 뭐야", dict(intent="knowledge")),
]


def _tool_calls(res) -> list[str]:
    return [s.tool for s in res.steps if s.type == "tool_call"]


# R1 — 미등록 천체

def test_r1_unknown_object_not_recommended(with_object_tool, events, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "12월 14일 서울 밤 9시~11시 XYZ-FAKE-999 관측 계획. 다른 천체는 원하지 않아")
    finally:
        set_chat_model_override(None)
    assert res.intent == Intent.PLAN
    assert res.recommended == []
    assert [j.verdict for j in res.excluded] == [Verdict.UNJUDGED]
    assert "'XYZ-FAKE-999'를 천문현상 목록과 천체 카탈로그에서 찾지 못해 판단하지 않음" in res.excluded[0].reasons[-1]
    assert res.replan_count == 0 and res.stop_reason is None and res.replan_limit_reached is False
    assert not [e for e in events if e.type == EventType.REPLAN]
    assert "충족" not in res.answer
    last = res.answer.splitlines()[-1]
    assert last.startswith("혹시") and last.endswith("?")
    # 대상을 모르면 박명·예보는 부르지 않는다 (판정에 쓸 수 없는 외부 호출)
    assert _tool_calls(res) == ["search_sky_events", "lookup_sky_object"]


def test_r1_similar_names_in_reason_and_question(with_object_tool, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "12월 14일 서울 밤 9시~11시 안드로매다 은하")
    finally:
        set_chat_model_override(None)
    assert res.recommended == []
    assert "비슷한 이름: 안드로메다 은하" in res.excluded[0].reasons[-1]
    assert "혹시 안드로메다 은하" in res.answer and res.answer.rstrip().endswith("말씀하신가요?")


def test_r1_temporary_error_distinguished(world, fresh_registry, fixed_today):
    from astrowatch.tools.contracts import SkyObjectInput, SkyObjectOutput

    @fresh_registry.tool(name="lookup_sky_object", description="천체 고도", kind=ToolKind.LOCAL,
                         input_model=SkyObjectInput, output_model=SkyObjectOutput)
    def _obj(a: SkyObjectInput):
        return fail(ErrorCode.TIMEOUT, "시간 초과")

    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "12월 14일 서울 밤 9시~11시 일시오류천체")
    finally:
        set_chat_model_override(None)
    assert res.recommended == []
    reason = res.excluded[0].reasons[-1]
    assert "일시 오류로 확인 못 함" in reason and "카탈로그에서 찾지 못해" not in reason
    assert "일시 오류" in res.answer and res.stop_reason is None


def test_r1_general_request_keeps_general_candidate(with_object_tool, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "12월 18일 서울 밤 9시~11시 그냥 별 보고 싶어")
    finally:
        set_chat_model_override(None)
    assert [j.candidate.title for j in res.recommended] == ["일반 관측(별·은하수)"]
    assert with_object_tool.count("lookup_sky_object") == 0


# R2 — 잘못된 날짜

@pytest.mark.parametrize("msg, expect", [
    ("2026년 2월 30일 서울 밤 9시~11시", "'2026년 2월 30일' 은 존재하지 않는 날짜입니다. 날짜를 다시 알려 주세요."),
    ("2027-02-29 서울 밤 9시~11시", "'2027-02-29' 는 존재하지 않는 날짜입니다."),
    ("범위 역전 서울", "날짜 범위가 뒤집혔습니다"),
])
def test_r2_invalid_date_asks_back(world, llm, events, msg, expect):
    res = run_turn(None, msg)
    assert res.intent == Intent.CLARIFY
    assert res.needs_clarification[0] == "date_from"
    assert expect in res.answer
    assert sum(world.calls.values()) == 0                       # 외부 Tool 0회
    assert not [e for e in events if e.type == EventType.TOOL_CALL]
    assert res.conditions.date_from is None
    decision = next(e for e in events if e.type == EventType.LLM_DECISION)
    assert "날짜 오류" in decision.payload["reason"]


def test_r2_invalid_date_does_not_silently_reuse_previous(world, llm):
    first = run_turn(None, R1_MSG)
    before = sum(world.calls.values())
    second = run_turn(first.session_id, "아니 2월 30일로 바꿔줘")
    assert second.intent == Intent.CLARIFY and second.needs_clarification == ["date_from"]
    assert "2026-12-14" not in second.answer                    # 이전 날짜로 조용히 진행하지 않음
    assert "존재하지 않는 날짜" in second.answer
    assert sum(world.calls.values()) == before


# R3 — 종료 사유

def test_r3_never_rises_is_target_unobservable(with_object_tool, events, fixed_today):
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, "12월 14일 서울 밤 9시~11시 남십자자리만")
    finally:
        set_chat_model_override(None)
    assert res.recommended == []
    assert res.replan_count == 0 and res.replan_limit_reached is False
    assert res.stop_reason == "target_unobservable" and res.replan_limit == 3
    assert not [e for e in events if e.type == EventType.REPLAN]
    assert "상한" not in res.answer and "넓혀 볼까요" not in res.answer


def test_r3_fixed_constraints(world, events):
    world.forecast = "cloudy"
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, R1_MSG + " 다 고정")
    finally:
        set_chat_model_override(None)
    assert res.replan_count == 0 and res.replan_limit_reached is False
    assert res.stop_reason == "fixed_constraints"
    assert not [e for e in events if e.type == EventType.REPLAN]


def test_r3_stop_reason_none_when_recommended(world, llm):
    res = run_turn(None, R1_MSG)
    assert res.recommended and res.stop_reason is None and res.replan_limit_reached is False


# R4 — 자정 넘김 한 밤

def test_r4_overnight_single_night_folded(world, llm):
    res = run_turn(None, "12월 14일 서울 밤 10시부터 다음 날 새벽 2시까지 유성우")
    assert res.conditions.date_from == res.conditions.date_to == PEAK
    assert world.count("get_night_window") == 1
    dates = {j.candidate.date for j in [*res.recommended, *res.excluded]}
    assert dates == {PEAK}


def test_r4_to_conditions_update_unit():
    from astrowatch.agent.nodes import AnalyzeOutput, date_problem, to_conditions_update

    base = _analysis("plan", date_from="2026-12-14", date_to="2026-12-15", start_time="22:00", end_time="02:00",
                     single_night=True)
    c = to_conditions_update(AnalyzeOutput(**base))
    assert c.date_from == c.date_to == PEAK and c.end_minute == 1560
    # 시간 없이도 single_night 이면 접는다
    c2 = to_conditions_update(AnalyzeOutput(**{**base, "start_time": None, "end_time": None}))
    assert c2.date_to == PEAK
    # 여러 밤(같은 시각대, 자정 안 넘김)은 그대로
    c3 = to_conditions_update(AnalyzeOutput(**{**base, "start_time": "20:00", "end_time": "23:00", "single_night": False}))
    assert c3.date_to == dt.date(2026, 12, 15)
    # 회귀: "주말 밤 9시~12시"(이틀 밤, 종료 24:00) 는 single_night 가 아니면 접지 않는다
    c4 = to_conditions_update(AnalyzeOutput(**{**base, "start_time": "21:00", "end_time": "00:00", "single_night": False}))
    assert c4.date_to == dt.date(2026, 12, 15)
    # LLM 이 한 밤이라고 하지 않았으면 자정을 넘겨도 접지 않는다 (시각만으로 추정하지 않음)
    c5 = to_conditions_update(AnalyzeOutput(**{**base, "single_night": False}))
    assert c5.date_to == dt.date(2026, 12, 15)
    # 잘못된 날짜는 날짜를 갱신하지 않고 문제 문장을 만든다
    bad = AnalyzeOutput(**_analysis("plan", date_from="2026-02-30"))
    assert to_conditions_update(bad).date_from is None and "존재하지 않는 날짜" in date_problem(bad)


# R5 — 캘린더·배경 지식 안내

def test_r5_calendar_unavailable_is_honest(world, llm):
    res = run_turn(None, "이 추천을 구글 캘린더에 등록해줘")
    assert res.intent == Intent.CALENDAR
    assert "캘린더 연동은 아직 제공하지 않습니다" in res.answer
    assert "저장한 계획 화면에서 할 수 있어요" not in res.answer
    assert sum(world.calls.values()) == 0


@pytest.fixture
def with_calendar_tool(world, fresh_registry):
    from astrowatch.tools.contracts import CalendarAddInput, CalendarAddOutput

    got: list[dict[str, Any]] = []

    @fresh_registry.tool(name="add_plan_to_calendar", description="캘린더 등록", kind=ToolKind.LOCAL,
                         input_model=CalendarAddInput, output_model=CalendarAddOutput)
    def _add(a: CalendarAddInput):
        got.append(a.model_dump())
        if a.plan_id == 99:
            return fail(ErrorCode.NOT_FOUND, "계획 없음")
        return ok(CalendarAddOutput(provider="local", plan_id=a.plan_id, event_id="local-1", title="유성우 관측",
                                    start_dt=dt.datetime(2026, 12, 14, 22), end_dt=dt.datetime(2026, 12, 15, 2),
                                    already_registered=False))

    return got


def test_r5_calendar_registers_plan_by_number(with_calendar_tool, llm):
    res = run_turn(None, "계획 3번을 캘린더에 넣어 줘")
    assert with_calendar_tool == [{"plan_id": 3}]               # 입력은 plan_id 하나뿐
    assert "계획 3번 '유성우 관측'을(를) 로컬 캘린더에 등록했습니다" in res.answer
    # 부작용 Tool 은 캐시하지 않는다 — 같은 요청이면 Tool 이 다시 판단한다(중복 여부는 Tool 이 already_registered 로)
    run_turn(res.session_id, "계획 3번을 캘린더에 넣어 줘")
    assert len(with_calendar_tool) == 2
    miss = run_turn(None, "계획 99번 캘린더에 넣어 줘")
    assert "계획 99번을 찾지 못했습니다" in miss.answer


def test_r5_calendar_guide_without_number(with_calendar_tool, llm):
    res = run_turn(None, "이 추천을 캘린더에 등록해줘")
    assert with_calendar_tool == []
    assert "'캘린더 등록' 버튼" in res.answer and "계획 N번" in res.answer


def test_r5_extract_plan_id():
    from astrowatch.agent.nodes import extract_plan_id

    assert extract_plan_id("계획 3번을 캘린더에") == 3
    assert extract_plan_id("2번 계획 넣어줘") == 2
    assert extract_plan_id("계획 #12 캘린더") == 12
    assert extract_plan_id("이 추천을 캘린더에") is None


def test_r5_knowledge_without_docs_tool(world, llm):
    res = run_turn(None, "블랙홀이 뭐야")
    assert res.intent == Intent.KNOWLEDGE and "아직 지원하지 않아요" in res.answer
    assert sum(world.calls.values()) == 0


# S09 — 장소만 요청

def test_s09_places_only_demoted_to_fact(world, llm, events):
    res = run_turn(None, "대전 천문대 20km 안 찾아줘")
    assert res.intent == Intent.FACT
    assert _tool_calls(res) == ["search_observatory"]
    decision = next(e for e in events if e.type == EventType.LLM_DECISION)
    assert "장소만 요청 → places" in decision.payload["reason"]
    assert res.recommended == []


def test_s09_observation_wish_stays_plan(world, llm):
    res = run_turn(None, "대전 천문대에서 별 보고 싶어")
    assert res.intent == Intent.PLAN
    assert "get_night_window" in _tool_calls(res)


# ── F3023 기존 일정 → 판정 반영 ───────────────────────────────

@pytest.fixture
def with_get_calendar(world, fresh_registry):
    """fake get_calendar. state['events'] 를 돌려주거나 state['fail'] 이면 AUTH 실패."""
    from astrowatch.tools.contracts import CalendarEvent, CalendarQueryInput, CalendarQueryOutput

    state: dict[str, Any] = {"events": [], "fail": False, "args": []}

    @fresh_registry.tool(name="get_calendar", description="일정 조회", kind=ToolKind.LOCAL,
                         input_model=CalendarQueryInput, output_model=CalendarQueryOutput)
    def _get(a: CalendarQueryInput):
        state["args"].append(a.model_dump(mode="json"))
        if state["fail"]:
            return fail(ErrorCode.AUTH, "구글 토큰 없음 secret-detail")
        return ok(CalendarQueryOutput(provider="local", date_from=a.date_from, date_to=a.date_to, events=[
            CalendarEvent(event_id=f"local-{i}", title=t, start_dt=s, end_dt=e, source="local")
            for i, (t, s, e) in enumerate(state["events"])]))

    return state


def test_calendar_conflict_shrinks_window(with_get_calendar, llm, events):
    with_get_calendar["events"] = [("회식", dt.datetime(2026, 12, 14, 21, 0), dt.datetime(2026, 12, 14, 23, 30))]
    res = run_turn(None, R1_MSG)
    assert with_get_calendar["args"] == [{"date_from": "2026-12-14", "date_to": "2026-12-15"}]   # 한 번
    assert "get_calendar" in [s.tool for s in res.steps if s.type == "tool_call"]               # 이벤트로 남음
    j = res.recommended[0]
    assert (j.time_window.start, j.time_window.end) == (23 * 60 + 30, 1560)
    assert any(r.startswith("기존 일정 '회식' 21:00~23:30 과 겹쳐 관측 창을 23:30~02:00+1") for r in j.reasons)
    assert j.latest_arrival_minute == 1560 - 30


def test_calendar_full_overlap_unfit(with_get_calendar, events):
    with_get_calendar["events"] = [("야간 근무", dt.datetime(2026, 12, 14, 20, 0), dt.datetime(2026, 12, 15, 3, 0))]
    set_chat_model_override(_TextFails())
    try:
        res = run_turn(None, R1_MSG)
    finally:
        set_chat_model_override(None)
    assert res.recommended == []
    peak = next(j for j in res.excluded if j.candidate.date == PEAK)
    assert peak.verdict == Verdict.UNFIT and peak.reasons[-1] == "기존 일정 '야간 근무' 과 겹침"
    assert "기존 일정 반영" in res.answer and "야간 근무" in res.answer


def test_calendar_auth_failure_continues(with_get_calendar, llm):
    with_get_calendar["fail"] = True
    res = run_turn(None, R1_MSG)
    assert len(res.recommended) == 1 and res.recommended[0].verdict == Verdict.CONDITIONAL
    assert "캘린더를 확인하지 못했습니다." in res.warnings
    assert not any("secret-detail" in w or "토큰" in w for w in res.warnings)


def test_calendar_not_registered_no_call(world, llm):
    res = run_turn(None, R1_MSG)
    assert "get_calendar" not in [s.tool for s in res.steps if s.type == "tool_call"]
    assert "캘린더를 확인하지 못했습니다." not in res.warnings


def test_calendar_intent_unsaved_recommendation_asks_save_first(with_calendar_tool, llm):
    first = run_turn(None, R1_MSG)
    assert first.recommended
    res = run_turn(first.session_id, "이 추천을 캘린더에 넣어 줘")
    assert with_calendar_tool == []
    assert "먼저 '계획으로 저장' 을 누른 뒤" in res.answer and "계획 N번을 캘린더에 넣어 줘" in res.answer


def test_run_turn_records_turn(world, llm, monkeypatch):
    from astrowatch.agent import service
    from astrowatch.storage import turns

    res = run_turn(None, R1_MSG)
    detail = turns.get_session_detail(res.session_id)
    assert detail is not None and len(detail.turns) == 1
    assert detail.turns[0].user_message == R1_MSG
    assert detail.turns[0].response.answer == res.answer

    def broken(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(turns, "append_turn", broken)
    res2 = run_turn(res.session_id, "같은 날 장소만 강원도로")
    assert res2.session_id == res.session_id and res2.answer != service.GENERIC_ERROR_ANSWER
    assert len(turns.get_session_detail(res.session_id).turns) == 1


def test_cond_changes_describe_followup_edits():
    """후속 요청(R-S15): 종료 시각만 바꾼 경우 바뀐 항목만 사람이 읽는 문장으로 나온다."""
    import datetime as dt

    from astrowatch.agent.nodes import _cond_changes
    from astrowatch.schemas.domain import Conditions

    prev = Conditions(date_from=dt.date(2026, 12, 14), date_to=dt.date(2026, 12, 14), region="서울",
                      start_minute=1320, end_minute=1560, event_query="쌍둥이자리 유성우")
    cur = prev.model_copy(update={"end_minute": 1380, "region": "대전"})
    assert _cond_changes(prev, cur) == ["지역 서울 → 대전", "종료 시각 02:00+1 → 23:00"]
    assert _cond_changes(Conditions(), cur) == []  # 첫 턴은 '변경' 이 아니다
    assert _cond_changes(prev, prev) == []
