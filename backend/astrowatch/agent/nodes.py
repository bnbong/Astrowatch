"""LangGraph 노드 5개 (F3005~F3011).

    analyze  LLM: 의도·조건 추출 → 이전 조건과 병합
    plan     규칙: Tool 호출 순서 (LLM 없음)
    execute  Tool 호출 · 캐시 · 후보 생성
    judge    순수 함수 판정 + 재계획 결정
    respond  LLM: 최종 문장 (되묻기·키 없음은 템플릿)

각 노드는 STAGE_START / STAGE_END(duration_ms, summary) 를 발행한다.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import re
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from astrowatch.agent import judge as J
from astrowatch.agent import prompts as P
from astrowatch.agent.llm import get_chat_model, llm_available, model_name
from astrowatch.agent.replan import STRATEGY_LABEL, describe, exhausted_stop_reason, next_strategy, tried_until
from astrowatch.agent.state import AgentState, ExecutedCall, PlannedCall, cache_key
from astrowatch.config import get_settings
from astrowatch.data.regions import find_region
from astrowatch.events.bus import EventType, bus
from astrowatch.schemas.domain import Candidate, Conditions, Intent, JudgedCandidate, Verdict, Window
from astrowatch.tools.base import ErrorCode, ToolResult
from astrowatch.tools.contracts import (
    EventKind,
    NightWindowOutput,
    ObservatoryOutput,
    SkyEvent,
    SkyEventsOutput,
    SkyForecastOutput,
    SkyObjectOutput,
)
from astrowatch.tools.event_kind import classify, classify_query
from astrowatch.tools.registry import registry

# Tool 이름
T_EVENTS = "search_sky_events"
T_NIGHT = "get_night_window"
T_FORECAST = "get_sky_forecast"
T_PLACE = "search_observatory"
T_OBJECT = "lookup_sky_object"     # 심화 로컬 Tool — 천체 고도
T_CALENDAR = "add_plan_to_calendar"  # 심화 F3023 — 등록돼 있을 때만 쓴다 (부작용 Tool: 캐시하지 않음)
T_DOCS = "search_space_docs"       # 심화 RAG — 등록돼 있을 때만 쓴다
T_GETCAL = "get_calendar"          # 심화 F3023 — 등록돼 있으면 기존 일정을 판정에 반영
_NO_CACHE_TOOLS = {T_CALENDAR}
_TEMP_CODES = {ErrorCode.TIMEOUT, ErrorCode.UPSTREAM, ErrorCode.INTERNAL}

# 계획 자리표시자 — execute 에서 후보 날짜별로 펼친다
EACH_DATE = "@candidate_dates"
FROM_NIGHT = "@from_night_window"

MAX_DATES = 10              # 한 번에 보는 날짜 수 상한 (호출 폭주 방지)
METEOR_SPAN_DAYS = 2        # 유성우 활동 기간 근사 (극대일 ±2일)
DEFAULT_RANGE_DAYS = 10     # 날짜 미지정 시 기본 탐색 범위 (중기예보 한도)
GENERIC_TITLE = "일반 관측(별·은하수)"
TARGET_PREFIX = "target-"   # 현상 목록에 없어 이름으로 만든 대상 후보
_GENERIC_WORDS = {"유성우", "유성", "일식", "월식", "현상", "관측"}
_WEEKDAYS = "월화수목금토일"


class RunState(AgentState, total=False):
    """그래프 내부 전용 키 (계약 AgentState 확장)."""

    replan_pending: bool        # judge → plan 으로 되돌릴지
    fact_kind: str | None       # FACT 의도의 질문 종류 (LLM 판단, 없으면 규칙 폴백)
    user_conditions: Conditions  # 재계획 전 사용자 조건 — 세션에는 이것을 저장한다
    replan_history: list[dict[str, Any]]  # [{round, strategy, reason}] — respond 컨텍스트용
    date_defaulted: bool        # 날짜를 말하지 않아 기본 범위를 넣었는가 (F3002 기본값·추정 표시)
    assumptions: list[str]      # 기본값으로 진행한 사실 — 응답에 밝힌다
    # 천체 고도 (lookup_sky_object) — 키 (event_id, date). Candidate 에 담을 수 없어 judge 에 따로 넘긴다
    object_windows: dict[tuple[str, dt.date], list[Window]]
    object_meta: dict[tuple[str, dt.date], dict[str, Any]]
    stop_reason: str | None     # 추천 0개로 끝난 이유 (ChatResponse.stop_reason)
    date_issue: str | None      # 존재하지 않는 날짜·뒤집힌 범위 → 되묻기 문장 (R2)
    calendar_plan_id: int | None  # CALENDAR 의도에서 뽑은 '계획 N번'
    has_last_recommended: bool  # 세션에 직전 추천이 있는가 (저장 전이라 plan_id 없음)


# ── 공통 ─────────────────────────────────────────────────────

def today() -> dt.date:
    """오늘 날짜(설정 시간대). 테스트에서 monkeypatch 한다."""
    try:
        tz = ZoneInfo(get_settings().timezone)
    except Exception:  # noqa: BLE001 — tzdata 없음
        tz = dt.timezone(dt.timedelta(hours=9))
    return dt.datetime.now(tz).date()


def stage(name: str) -> Callable[[Callable[[RunState], tuple[dict[str, Any], str]]], Callable[[RunState], dict[str, Any]]]:
    """노드 함수 (state) -> (update, summary) 를 감싸 단계 이벤트를 발행한다."""

    def deco(fn: Callable[[RunState], tuple[dict[str, Any], str]]) -> Callable[[RunState], dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(state: RunState) -> dict[str, Any]:
            sid = state.get("session_id", "-")
            bus.publish(sid, EventType.STAGE_START, stage=name)
            t = bus.timer()
            update, summary = fn(state)
            bus.publish(sid, EventType.STAGE_END, stage=name, payload={"duration_ms": t.ms, "summary": summary})
            return update

        return wrapper

    return deco


def _as(model: type[BaseModel], data: Any) -> Any:
    """Tool data 가 모델이 아니면(dict) 모델로 바꾼다. 실패하면 None."""
    if data is None or isinstance(data, model):
        return data
    try:
        return model.model_validate(data)
    except Exception:  # noqa: BLE001
        return None


def night_args(d: dt.date, lat: float, lon: float) -> dict[str, Any]:
    return {"date": d.isoformat(), "lat": lat, "lon": lon}


def forecast_args(d: dt.date, lat: float, lon: float, hours: list[int]) -> dict[str, Any]:
    return {"date": d.isoformat(), "lat": lat, "lon": lon, "hours": hours}


def _dates(c: Conditions) -> list[dt.date]:
    if c.date_from is None:
        return []
    end = c.date_to or c.date_from
    if end < c.date_from:
        end = c.date_from
    days = min((end - c.date_from).days + 1, MAX_DATES)
    return [c.date_from + dt.timedelta(days=i) for i in range(days)]


def _months(c: Conditions) -> list[tuple[int, int]]:
    """조회할 (연, 월). 새벽 극대·유성우 전후 활동을 위해 범위 앞뒤로 며칠 여유를 둔다."""
    ds = _dates(c)
    if not ds:
        return []
    out: list[tuple[int, int]] = []
    d = ds[0] - dt.timedelta(days=METEOR_SPAN_DAYS)
    end = ds[-1] + dt.timedelta(days=METEOR_SPAN_DAYS + 1)
    while d <= end:
        if (d.year, d.month) not in out:
            out.append((d.year, d.month))
        d += dt.timedelta(days=1)
    return out


def place_args(c: Conditions, region: Any) -> dict[str, Any]:
    """search_observatory 인자 (plan·places_from 공통 — 같은 cache_key 를 만들기 위해)."""
    return {"query": c.place_query or "천문대", "lat": region.lat, "lon": region.lon,
            "radius_km": c.max_distance_km or 20}


def object_args(name: str, d: dt.date, lat: float, lon: float) -> dict[str, Any]:
    return {"name": name, "date": d.isoformat(), "lat": lat, "lon": lon}


_OBJECT_WORDS = ("떠", "뜨", "뜬", "고도", "남중", "지는", "져", "지평선", "보여", "보이", "위치", "어디")


def _known_object(name: str | None) -> bool:
    """카탈로그(또는 행성)에 있는 천체 이름인가 — 없으면 고도 Tool 로 보내지 않는다."""
    if not name or classify(name) != EventKind.OTHER:
        return False
    from astrowatch.astro.catalog import find_object
    from astrowatch.astro.planets import find_body

    return find_body(name) is not None or find_object(name) is not None


# FACT 질문 종류 (analyze LLM 이 고른다)
FK_OBJECT = "object_visibility"   # 특정 천체가 언제·몇 시에·얼마나 높이·어느 방향에 보이는지
FK_EVENTS = "events"              # 이 달의 천문현상 목록 · 특정 현상 날짜
FK_NIGHT = "night_window"         # 일몰·박명·월출몰·월령
FK_FORECAST = "forecast"          # 하늘 상태
FK_PLACES = "places"              # 관측지
FACT_KINDS = (FK_OBJECT, FK_EVENTS, FK_NIGHT, FK_FORECAST, FK_PLACES)


def resolve_fact_kind(llm_kind: str | None, message: str, c: Conditions) -> str:
    """LLM 판단을 그대로 쓴다. 비었으면 규칙 폴백 — 카탈로그(행성 포함)에 있는 천체면 object_visibility 우선."""
    if llm_kind in FACT_KINDS:
        if llm_kind == FK_OBJECT and not c.event_query:
            return FK_EVENTS            # 천체 이름이 없으면 고도를 계산할 대상이 없다
        return llm_kind
    if _known_object(c.event_query):
        return FK_OBJECT
    m = message or ""
    if any(k in m for k in ("박명", "일몰", "일출", "월출", "월몰", "월령", "해 지", "해지", "어두워")):
        return FK_NIGHT
    if any(k in m for k in ("날씨", "예보", "구름", "비 ", "비와", "맑")):
        return FK_FORECAST
    if c.place_query or any(k in m for k in ("천문대", "관측지", "관측소")):
        return FK_PLACES
    return FK_EVENTS


def _fact_missing(fact_kind: str, c: Conditions) -> list[str]:
    missing = []
    if fact_kind != FK_EVENTS and find_region(c.region) is None:
        missing.append("region")
    return missing


def fact_plan(fact_kind: str | None, c: Conditions) -> list[PlannedCall]:
    """FACT 질문 종류 → 필요한 Tool 1~2개. 날짜가 없으면 오늘."""
    kind = fact_kind or FK_EVENTS
    d = c.date_from or today()
    if kind == FK_EVENTS:
        args: dict[str, Any] = {"year": d.year, "month": d.month}
        if c.event_kind and c.event_kind != EventKind.DEEP_SKY:
            args["kind"] = c.event_kind.value
        return [PlannedCall(tool=T_EVENTS, args=args, reason="천문현상 사실 조회", fixed_args=[])]
    region = find_region(c.region)
    if region is None:
        return []
    loc_fixed = ["lat", "lon"] if "region" in c.fixed else []
    night = PlannedCall(tool=T_NIGHT, args=night_args(d, region.lat, region.lon),
                        reason="박명·출몰·월령 조회", fixed_args=loc_fixed)
    if kind == FK_OBJECT and c.event_query:
        calls = []
        if registry.get(T_OBJECT) is not None:
            calls.append(PlannedCall(tool=T_OBJECT, args=object_args(c.event_query, d, region.lat, region.lon),
                                     reason=f"{c.event_query} 고도·남중 계산", fixed_args=loc_fixed))
        night["reason"] = "어두운 시간(천문박명) — 천체가 잘 보이는 시간대를 고르는 기준"
        return [*calls, night]
    if kind == FK_PLACES:
        return [PlannedCall(tool=T_PLACE, args=place_args(c, region), reason="관측지 검색", fixed_args=loc_fixed)]
    if kind == FK_FORECAST:
        return [PlannedCall(tool=T_FORECAST, args=forecast_args(d, region.lat, region.lon, []),
                            reason="예보 조회", fixed_args=loc_fixed)]
    return [night]


# ── analyze ──────────────────────────────────────────────────

class AnalyzeOutput(BaseModel):
    """LLM structured output. strict json_schema 호환을 위해 모든 필드를 required(nullable)로 둔다."""

    intent: Literal["plan", "fact", "confirm", "calendar", "knowledge", "chat", "clarify"]
    reason: str = Field(description="의도 판단 이유 한 문장")
    reset_conditions: bool = Field(description="이전 조건을 버리는 새 요청이면 true")
    date_flexible: bool = Field(description="'다른 날도 괜찮아', '전후', '쯤' 처럼 날짜를 바꿔도 된다고 했으면 true")
    date_from: str | None = Field(description="YYYY-MM-DD")
    date_to: str | None = Field(description="YYYY-MM-DD")
    start_time: str | None = Field(description="HH:MM 24시간제")
    end_time: str | None = Field(description="HH:MM 24시간제")
    region: str | None
    event_kind: Literal["meteor", "planet", "moon", "eclipse", "occultation", "season", "deep_sky", "other"] | None
    event_query: str | None
    place_query: str | None
    max_distance_km: float | None
    allow_partly_cloudy: bool | None
    avoid_moon: bool | None
    fixed: list[str]
    missing: list[str]
    fact_kind: Literal["object_visibility", "events", "night_window", "forecast", "places"] | None = Field(
        ..., description="intent=fact 일 때 질문 종류. 아니면 null")
    date_invalid: str | None = Field(
        ..., description="존재하지 않는 날짜(2월 30일 등)나 뒤집힌 범위를 말했으면 그 원문. 이때 date_from/date_to 는 null")
    single_night: bool = Field(
        ..., description="한 밤(자정을 넘겨도)만 말했으면 true — date_to 는 date_from 과 같게, 새벽 종료는 end_time 으로만")


_FIXABLE = {"date_from", "date_to", "start_minute", "end_minute", "region", "event_kind", "event_query", "place_query"}


def _to_minute(text: str | None) -> int | None:
    m = J.hhmm_to_min(text)
    if m is None:
        return None
    m %= 1440
    if m < J.NOON:          # 정오 이전은 다음 날 새벽으로 본다
        m += 1440
    return min(m, J.MAX_MINUTE)


def _parse_dates(out: AnalyzeOutput) -> tuple[dict[str, dt.date], list[str]]:
    """(파싱된 날짜, 파싱 실패 원문). 실패를 '날짜 미지정'으로 삼키지 않는다 (R2).

    자정 넘김 한 밤(밤 10시~새벽 2시)을 LLM 이 date_to=다음 날로 주면 저녁 기준 날짜 하나로 접는다 (R4).
    """
    dates: dict[str, dt.date] = {}
    invalid: list[str] = []
    if out.date_invalid and out.date_invalid.strip():
        invalid.append(out.date_invalid.strip())
    for key in ("date_from", "date_to"):
        v = getattr(out, key)
        if v and v.strip():
            try:
                dates[key] = dt.date.fromisoformat(v.strip()[:10])
            except ValueError:
                invalid.append(v.strip())
    if "date_from" in dates and "date_to" not in dates:
        dates["date_to"] = dates["date_from"]
    if "date_from" in dates and dates.get("date_to") == dates["date_from"] + dt.timedelta(days=1):
        # 접기는 LLM 이 '한 밤'이라고 판단했을 때만. 시각만 보고 접으면 "주말 밤 9시~12시"(이틀 밤, 종료 24:00)도
        # 한 밤으로 잘못 접힌다 — 실제 회귀 사례.
        s, e = _to_minute(out.start_time), _to_minute(out.end_time)
        crosses_midnight = s is not None and e is not None and s < 1440 < e
        if out.single_night and (crosses_midnight or e is None or s is None or e <= 1440):
            dates["date_to"] = dates["date_from"]
    return dates, invalid


def date_problem(out: AnalyzeOutput) -> str | None:
    """존재하지 않는 날짜·뒤집힌 범위면 되묻기 문장. 정상이면 None."""
    dates, invalid = _parse_dates(out)
    if invalid:
        t = invalid[0]
        return f"'{t}' {J.josa(t, '은', '는')} 존재하지 않는 날짜입니다. 날짜를 다시 알려 주세요."
    d0, d1 = dates.get("date_from"), dates.get("date_to")
    if d0 is not None and d1 is not None and d1 < d0:
        return f"날짜 범위가 뒤집혔습니다({d0} ~ {d1}). 시작 날짜와 끝 날짜를 다시 알려 주세요."
    return None


def to_conditions_update(out: AnalyzeOutput) -> Conditions:
    """LLM 출력 → 부분 갱신용 Conditions (말한 값만 set). 날짜에 문제가 있으면 날짜는 갱신하지 않는다."""
    raw: dict[str, Any] = {}
    if date_problem(out) is None:
        raw.update(_parse_dates(out)[0])
    s = _to_minute(out.start_time)
    e = _to_minute(out.end_time)
    if s is not None:
        raw["start_minute"] = s
    if e is not None:
        raw["end_minute"] = e
    for key in ("region", "event_kind", "event_query", "place_query", "max_distance_km",
                "allow_partly_cloudy", "avoid_moon"):
        v = getattr(out, key)
        if v is not None and v != "":
            raw[key] = v
    if "max_distance_km" in raw:
        raw["max_distance_km"] = max(0.1, min(20.0, float(raw["max_distance_km"])))
    fixed = [f for f in out.fixed if f in _FIXABLE]
    try:
        return Conditions(fixed=fixed, **raw)
    except ValidationError:
        # 문제 필드만 버린다
        ok: dict[str, Any] = {}
        for k, v in raw.items():
            try:
                Conditions(**{**ok, k: v})
                ok[k] = v
            except ValidationError:
                continue
        return Conditions(fixed=fixed, **ok)


def _history_messages(history: list[dict[str, str]]) -> list[tuple[str, str]]:
    msgs = []
    for h in history[-12:]:
        role = "human" if h.get("role") == "user" else "ai"
        msgs.append((role, h.get("content", "")))
    return msgs


def _structured_invoke(schema: type[BaseModel], messages: list[Any]) -> BaseModel:
    """json_schema → function_calling 순서로 시도."""
    model = get_chat_model()
    last: Exception | None = None
    for method in ("json_schema", "function_calling"):
        try:
            result = model.with_structured_output(schema, method=method).invoke(messages)
            if isinstance(result, schema):
                return result
            if isinstance(result, dict):
                return schema.model_validate(result)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError("structured output 실패") from last


def call_analyze_llm(state: RunState, prev: Conditions) -> AnalyzeOutput:
    d = today()
    system = P.ANALYZE_SYSTEM.format(
        today=d.isoformat(),
        weekday=_WEEKDAYS[d.weekday()],
        prev_conditions=prev.model_dump_json(exclude_defaults=True) or "{}",
        memory_summary=state.get("memory_summary") or "없음",
    )
    messages = [("system", system), *_history_messages(state.get("history") or []), ("human", state.get("message", ""))]
    out = _structured_invoke(AnalyzeOutput, messages)
    assert isinstance(out, AnalyzeOutput)
    return out


@stage("analyze")
def analyze(state: RunState) -> tuple[dict[str, Any], str]:
    sid = state.get("session_id", "-")
    prev = state.get("conditions") or Conditions()
    warnings = list(state.get("warnings") or [])
    base = {"plan": [], "candidates": [], "judged": [], "replan_pending": False, "date_defaulted": False,
            "assumptions": []}

    if not llm_available():
        warnings.append(P.NO_LLM_WARNING)
        return {**base, "intent": Intent.CHAT, "intent_reason": "LLM 키 없음", "conditions": prev,
                "missing": [], "warnings": warnings, "user_conditions": prev}, "LLM 키 없음 → 사용법 안내로 진행"

    try:
        out = call_analyze_llm(state, prev)
    except Exception:  # noqa: BLE001 — 상세 오류는 사용자에게 보이지 않는다
        warnings.append(P.LLM_FAIL_WARNING)
        bus.publish(sid, EventType.ERROR, stage="analyze", payload={"message": "LLM 요청 해석 실패"})
        return {**base, "intent": Intent.CHAT, "intent_reason": "LLM 호출 실패", "conditions": prev,
                "missing": [], "warnings": warnings, "user_conditions": prev}, "LLM 호출 실패 → 사용법 안내로 진행"

    update = to_conditions_update(out)
    conditions = (Conditions() if out.reset_conditions else prev).merged(update)
    # 규칙: 날짜를 하루로 못박았으면(범위가 아니면) 재계획 ①(다른 날짜)을 쓰지 않는다 — 기획안 5-4
    # 단, 사용자가 날짜 유연성(다른 날도 괜찮아·전후·쯤)을 말했으면 적용하지 않는다
    if not out.date_flexible and conditions.date_from is not None and (
        conditions.date_to is None or conditions.date_to == conditions.date_from
    ):
        if "date_from" not in conditions.fixed:
            conditions = conditions.model_copy(update={"fixed": sorted({*conditions.fixed, "date_from"})})
    intent = Intent(out.intent)
    reason = out.reason
    missing: list[str] = []
    fact_kind: str | None = None
    llm_fact_kind = out.fact_kind
    assumptions: list[str] = []
    date_issue = date_problem(out)
    calendar_plan_id: int | None = None

    if intent == Intent.PLAN and places_only(conditions, state.get("message", "")):
        # S09: 현상·시간·관측 의사 없이 장소만 물으면 관측 계획을 돌리지 않는다
        intent, llm_fact_kind = Intent.FACT, FK_PLACES
        reason = f"{reason} / 장소만 요청 → places"
    if intent == Intent.CALENDAR:
        calendar_plan_id = extract_plan_id(state.get("message", ""))

    if intent == Intent.CLARIFY:
        # 되물을지는 코드가 정한다 (날짜는 되묻지 않음). 부족한 조건이 없으면 계획으로 진행
        intent = Intent.PLAN
    if intent == Intent.PLAN:
        missing = conditions.missing_for_plan()
        if conditions.region is not None and find_region(conditions.region) is None:
            missing.append("region")
    elif intent == Intent.FACT:
        fact_kind = resolve_fact_kind(llm_fact_kind, state.get("message", ""), conditions)
        missing = _fact_missing(fact_kind, conditions)
        src = "LLM" if out.fact_kind == fact_kind else "규칙 폴백"
        reason = f"{reason} / fact_kind={fact_kind}({src})"
    missing = list(dict.fromkeys(missing))

    # 되묻기 반복 방지: 직전 턴에 물었는데 또 못 받은 시작 시각은 어두운 창 시작으로 진행
    asked = asked_last_turn(state.get("history") or [])
    if "start_minute" in missing and "start_minute" in asked:
        missing.remove("start_minute")
        assumptions.append("시작 시각을 알려 주지 않아 어두워지는 시각(천문박명 종료)부터 계산했습니다")
        reason = f"{reason} / 시작 시각 두 번째 미응답 → 어두운 시간 시작부터"

    if missing and intent in (Intent.PLAN, Intent.FACT):
        reason = f"{reason} / 조건 부족({', '.join(missing)}) → 되묻기"
        intent = Intent.CLARIFY

    # R2: 존재하지 않는 날짜·뒤집힌 범위는 '날짜 미지정'이 아니다 — 외부 조회 없이 되묻는다
    if date_issue and intent in (Intent.PLAN, Intent.FACT, Intent.CLARIFY):
        missing = ["date_from", *[m for m in missing if m != "date_from"]]
        reason = f"{reason} / 날짜 오류 → 되묻기"
        intent = Intent.CLARIFY
    else:
        date_issue = None

    # 날짜 미지정 → 기본 범위 (사용자 조건에는 남기지 않는다)
    user_conditions = conditions
    date_defaulted = False
    if intent == Intent.PLAN and conditions.date_from is None:
        d0 = today()
        d1 = d0 + dt.timedelta(days=DEFAULT_RANGE_DAYS - 1)
        fixed = [f for f in conditions.fixed if f not in ("date_from", "date_to")]
        conditions = conditions.model_copy(update={"date_from": d0, "date_to": d1, "fixed": fixed})
        date_defaulted = True
        reason = f"{reason} / 날짜 미지정 → 기본 범위 {d0}~{d1}"

    bus.publish(sid, EventType.LLM_DECISION, stage="analyze",
                payload={"decision": intent.value, "reason": reason, "model": model_name()})
    summary = f"의도 {intent.value}"
    if missing:
        summary += f" · 부족한 조건 {', '.join(missing)}"
    if date_defaulted:
        summary += f" · 날짜 기본 범위 {conditions.date_from}~{conditions.date_to}"
    return {**base, "intent": intent, "intent_reason": reason, "conditions": conditions, "missing": missing,
            "warnings": warnings, "fact_kind": fact_kind, "user_conditions": user_conditions,
            "date_defaulted": date_defaulted, "assumptions": assumptions, "date_issue": date_issue,
            "calendar_plan_id": calendar_plan_id, "stop_reason": None}, summary


_OBSERVE_WORDS = ("보고 싶", "보고싶", "볼 수", "볼수", "보러", "관측", "계획", "보일", "보여", "볼만", "볼 만",
                  "유성", "은하수", "별 보", "별보")


def places_only(c: Conditions, message: str) -> bool:
    """현상·대상·시간 없이 관측지만 찾는 요청인가 (S09). 관측 의사를 말했으면 계획으로 둔다."""
    if not c.place_query:
        return False
    if any(v is not None for v in (c.event_query, c.event_kind, c.start_minute, c.end_minute)):
        return False
    m = (message or "").replace("관측지", "").replace("관측소", "")
    return not any(w in m for w in _OBSERVE_WORDS)


_PLAN_NO = re.compile(r"(?:계획|플랜)\s*#?\s*(\d+)|(\d+)\s*번")


def extract_plan_id(message: str) -> int | None:
    """'계획 3번', '3번 계획', '계획 #3' → 3."""
    m = _PLAN_NO.search(message or "")
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if n > 0 else None


def asked_last_turn(history: list[dict[str, str]]) -> set[str]:
    """직전 assistant 메시지가 되묻기였다면 물었던 조건 이름들."""
    last = next((h.get("content", "") for h in reversed(history) if h.get("role") == "assistant"), "")
    return {k for k, q in P.CLARIFY_QUESTIONS.items() if q in last}


def route_after_analyze(state: RunState) -> str:
    intent = state.get("intent")
    if intent in (Intent.PLAN, Intent.FACT):
        return "plan"
    if intent == Intent.CALENDAR and state.get("calendar_plan_id") and registry.get(T_CALENDAR) is not None:
        return "plan"
    if intent == Intent.KNOWLEDGE and registry.get(T_DOCS) is not None:
        return "plan"
    return "respond"


# ── plan ─────────────────────────────────────────────────────

def build_plan(intent: Intent, c: Conditions, fact_kind: str | None = None, *,
               calendar_plan_id: int | None = None, message: str = "") -> tuple[list[PlannedCall], str]:
    region = find_region(c.region)
    fixed = set(c.fixed)
    loc_fixed = ["lat", "lon"] if "region" in fixed else []

    if intent == Intent.CALENDAR:
        # 입력은 plan_id 하나뿐 — 제목·시각은 Tool 이 저장소에서 읽는다
        if calendar_plan_id and registry.get(T_CALENDAR) is not None:
            return [PlannedCall(tool=T_CALENDAR, args={"plan_id": calendar_plan_id},
                                reason=f"사용자가 요청한 계획 {calendar_plan_id}번 캘린더 등록",
                                fixed_args=["plan_id"])], f"캘린더 등록({T_CALENDAR})"
        return [], "Tool 불필요"
    if intent == Intent.KNOWLEDGE:
        if registry.get(T_DOCS) is not None and message:
            return [PlannedCall(tool=T_DOCS, args={"query": message}, reason="배경 지식 문서 검색",
                                fixed_args=[])], f"배경 지식 검색({T_DOCS})"
        return [], "Tool 불필요"

    if intent == Intent.FACT:
        calls = fact_plan(fact_kind, c)
        if not calls:
            return [], "지역을 찾지 못해 계획 없음"
        return calls, f"사실 조회({fact_kind or FK_EVENTS}): " + " + ".join(p["tool"] for p in calls)

    if intent != Intent.PLAN or region is None or c.date_from is None:
        return [], "Tool 불필요"

    plan: list[PlannedCall] = []
    for y, m in _months(c):
        args = {"year": y, "month": m}
        if c.event_kind and c.event_kind != EventKind.DEEP_SKY:
            args["kind"] = c.event_kind.value
        fa = (["year", "month"] if "date_from" in fixed else []) + (["kind"] if "event_kind" in fixed and c.event_kind else [])
        target = c.event_query or (c.event_kind.value if c.event_kind else "전체 현상")
        plan.append(PlannedCall(tool=T_EVENTS, args=args, reason=f"{y}-{m:02d} {target} 날짜·시각 확인", fixed_args=fa))
    loc = {"lat": region.lat, "lon": region.lon}
    if c.event_query and is_target_query(c) and registry.get(T_OBJECT) is not None:
        # 현상 목록에 없을 때만 실행된다 (execute 가 판단). 날짜마다 천체 고도 계산
        plan.append(PlannedCall(tool=T_OBJECT, args={"name": c.event_query, "date": EACH_DATE, **loc},
                                reason=f"현상 목록에 없으면 날짜마다 '{c.event_query}' 고도(20° 이상) 구간 계산",
                                fixed_args=(["name"] if "event_query" in fixed else []) + loc_fixed))
    ds = _dates(c)
    if ds and registry.get(T_GETCAL) is not None:
        # 자정 넘김 관측 창까지 덮도록 끝 날짜 +1일. 한 번만 부른다
        plan.append(PlannedCall(tool=T_GETCAL,
                                args={"date_from": ds[0].isoformat(),
                                      "date_to": (ds[-1] + dt.timedelta(days=1)).isoformat()},
                                reason="기존 일정과 겹치는 관측 시간 확인", fixed_args=[]))
    plan.append(PlannedCall(tool=T_NIGHT, args={"date": EACH_DATE, **loc},
                            reason=f"후보 날짜마다 {region.name} 어두운 시간·월령 확인",
                            fixed_args=(["date"] if "date_from" in fixed else []) + loc_fixed))
    plan.append(PlannedCall(tool=T_FORECAST, args={"date": EACH_DATE, **loc, "hours": FROM_NIGHT},
                            reason="관측 창 시각의 하늘 상태 확인", fixed_args=loc_fixed))
    if c.place_query:
        plan.append(PlannedCall(tool=T_PLACE,
                                args=place_args(c, region),
                                reason=f"{region.name} 근처 '{c.place_query}' 검색", fixed_args=loc_fixed))
    reason = " → ".join(f"{p['tool']}" for p in plan)
    obj = " → 천체 고도" if any(p["tool"] == T_OBJECT for p in plan) else ""
    cal = " → 내 일정" if any(p["tool"] == T_GETCAL for p in plan) else ""
    return plan, f"현상{obj}{cal} → 출몰·월령 → 날씨{' → 장소' if c.place_query else ''} ({reason})"


@stage("plan")
def plan(state: RunState) -> tuple[dict[str, Any], str]:
    intent = state.get("intent", Intent.CHAT)
    c = state.get("conditions") or Conditions()
    calls, reason = build_plan(intent, c, state.get("fact_kind"), calendar_plan_id=state.get("calendar_plan_id"),
                               message=state.get("message", ""))
    round_ = state.get("replan_count", 0)
    prefix = f"[재계획 {round_}회차] " if round_ else ""
    return {"plan": calls, "plan_reason": reason, "replan_pending": False}, f"{prefix}{len(calls)}단계 계획: {reason}"


# ── execute ──────────────────────────────────────────────────

def _norm(text: str) -> str:
    return "".join(text.split()).lower()


def _match_event(query: str, events: list[SkyEvent]) -> list[SkyEvent]:
    """현상 이름 매칭: 부분 문자열 → 실패하면 고유 단어(일반어 제외) 느슨한 매칭."""
    q = _norm(query)
    hits = [e for e in events if q in _norm(e.title) or _norm(e.title) in q]
    if hits:
        return hits
    tokens = [t for t in query.split() if len(t) >= 2 and t not in _GENERIC_WORDS]
    for t in tokens:
        stem = t[:-2] if t.endswith("자리") and len(t) > 3 else t
        hits = [e for e in events if _norm(stem) in _norm(e.title)]
        if hits:
            return hits
    return []


def is_target_query(c: Conditions) -> bool:
    """날짜가 정해진 '현상'이 아니라 관측 '대상'을 지목했는가 (유성우·식 등 시각이 있는 현상은 지어내지 않는다)."""
    if c.event_kind == EventKind.DEEP_SKY:
        return True
    if not c.event_query or c.event_kind not in (None, EventKind.OTHER):
        return False
    return classify(c.event_query) == EventKind.OTHER


def build_candidates(events: list[SkyEvent], c: Conditions, region_name: str, lat: float, lon: float) -> list[Candidate]:
    dates = _dates(c)
    in_range = set(dates)
    pool = [e for e in events if c.event_kind is None or e.kind == c.event_kind]
    if c.event_query:
        pool = _match_event(c.event_query, pool)

    out: list[Candidate] = []
    seen: set[tuple[str, dt.date]] = set()
    for e in pool:
        t = J.hhmm_to_min(e.time)
        eve, minute = e.date, t
        if t is not None and t < J.NOON:
            # 새벽 시각 → 전날 저녁 기준 밤
            prev_day = e.date - dt.timedelta(days=1)
            if prev_day in in_range:
                eve, minute = prev_day, t + 1440
            elif e.kind == EventKind.METEOR:
                minute = t + 1440   # 유성우는 여러 밤 활동 — 같은 날짜 밤으로 근사
        # 유성우는 극대일 전후 며칠 활동한다 → 범위 안의 ±METEOR_SPAN_DAYS 날짜도 후보
        span = METEOR_SPAN_DAYS if e.kind == EventKind.METEOR else 0
        for offset in range(-span, span + 1):
            day = eve + dt.timedelta(days=offset)
            if day not in in_range or (e.event_id, day) in seen:
                continue
            seen.add((e.event_id, day))
            title = e.title if offset == 0 else f"{e.title} (극대 {eve:%m/%d} 전후)"
            out.append(Candidate(event_id=e.event_id, title=title, kind=e.kind, date=day, region=region_name,
                                 lat=lat, lon=lon, event_minute=minute))

    # 지목한 대상이 현상 목록에 없으면(은하·성운 등 언제든 보이는 천체) 이름 그대로 날짜마다 후보
    if not out and is_target_query(c):
        title = c.event_query or "심우주 천체(은하·성운·성단)"
        kind = classify_query(c.event_query) if c.event_query else EventKind.DEEP_SKY
        if c.event_kind == EventKind.DEEP_SKY:
            kind = EventKind.DEEP_SKY
        for d in dates:
            out.append(Candidate(event_id=f"{TARGET_PREFIX}{d:%Y%m%d}", title=title, kind=kind, date=d,
                                 region=region_name, lat=lat, lon=lon))

    # 현상을 지정하지 않았으면, 현상이 없는 날도 일반 관측 후보로 본다
    if c.event_query is None and c.event_kind is None:
        covered = {x.date for x in out if x.kind != EventKind.SEASON}
        for d in dates:
            if d not in covered:
                out.append(Candidate(event_id=f"general-{d:%Y%m%d}", title=GENERIC_TITLE, kind=EventKind.OTHER,
                                     date=d, region=region_name, lat=lat, lon=lon))
    out.sort(key=lambda x: (x.date, x.title))
    return out


@stage("execute")
def execute(state: RunState) -> tuple[dict[str, Any], str]:
    sid = state.get("session_id", "-")
    c = state.get("conditions") or Conditions()
    cache: dict[str, ToolResult[Any]] = dict(state.get("cache") or {})
    calls: list[ExecutedCall] = list(state.get("calls") or [])
    round_ = state.get("replan_count", 0)
    counters = {"called": 0, "reused": 0, "failed": 0}

    def run(tool: str, args: dict[str, Any], reason: str) -> ToolResult[Any]:
        key = cache_key(tool, args)
        side_effect = tool in _NO_CACHE_TOOLS   # 일정 등록처럼 부작용이 있는 Tool 은 캐시하지 않는다
        if key in cache and not side_effect:   # 같은 인자는 다시 부르지 않는다 (F3009)
            counters["reused"] += 1
            return cache[key]
        bus.publish(sid, EventType.TOOL_CALL, stage="execute", tool=tool, payload={"args": args, "reason": reason})
        t = bus.timer()
        result = registry.invoke(tool, args)
        ms = t.ms
        bus.publish(sid, EventType.TOOL_RESULT, stage="execute", tool=tool,
                    payload={**result.as_event_payload(), "duration_ms": ms})
        if not side_effect:
            cache[key] = result
        calls.append(ExecutedCall(tool=tool, args=args, result=result, duration_ms=ms, replan_round=round_))
        counters["called"] += 1
        if not result.ok:
            counters["failed"] += 1
        return result

    events: list[SkyEvent] = []
    candidates: list[Candidate] | None = None
    nights: dict[dt.date, NightWindowOutput | None] = {}
    obj_windows: dict[tuple[str, dt.date], list[Window]] = {}
    obj_meta: dict[tuple[str, dt.date], dict[str, Any]] = {}

    def ensure_candidates(step: PlannedCall) -> list[Candidate]:
        nonlocal candidates
        if candidates is None:
            region = find_region(c.region)
            name = region.name if region else (c.region or "")
            candidates = build_candidates(events, c, name, float(step["args"]["lat"]), float(step["args"]["lon"]))
        return candidates

    def judgeable(step: PlannedCall) -> list[Candidate]:
        """박명·예보를 볼 후보 — 지목한 대상을 확인 못 한 후보(R1)는 판단하지 않으므로 외부 조회도 하지 않는다."""
        return [x for x in ensure_candidates(step) if not obj_meta.get((x.event_id, x.date), {}).get("unresolved")]

    for step in state.get("plan") or []:
        tool, args = step["tool"], step["args"]
        if tool == T_EVENTS:
            res = run(tool, args, step["reason"])
            data = _as(SkyEventsOutput, res.data) if res.ok else None
            if data:
                events.extend(data.events)
        elif tool == T_OBJECT and args.get("date") == EACH_DATE:
            cands = ensure_candidates(step)
            targets = [x for x in cands if x.event_id.startswith(TARGET_PREFIX)]   # 현상 목록에 없던 대상만
            updated: dict[str, Candidate] = {}
            for x in targets:
                res = run(tool, object_args(args["name"], x.date, args["lat"], args["lon"]),
                          f"{x.date} '{args['name']}' 고도 구간")
                key = (x.event_id, x.date)
                out = _as(SkyObjectOutput, res.data) if res.ok else None
                if out is None:
                    # 지목한 대상이 현상 목록에도 카탈로그에도 없다 → 판단하지 않는다 (R1). 일시 오류와 구분
                    temporary = res.ok or res.code in _TEMP_CODES
                    obj_meta[key] = {"found": False, "query": args["name"],
                                     "unresolved": "temporary" if temporary else "not_found",
                                     "similar": [] if temporary else similar_from_error(res)}
                    continue
                obj_windows[key] = [Window(start=w.start, end=max(w.start, w.end)) for w in out.visible_windows]
                obj_meta[key] = {"found": True, "name": out.object.name_ko, "min_alt": out.min_alt,
                                 "transit_minute": out.transit_minute, "transit_alt": out.transit_alt,
                                 "transit_az": out.transit_az, "never_rises": out.never_rises}
                kind = EventKind.OTHER if out.object.kind in ("star", "constellation", "planet") else EventKind.DEEP_SKY
                updated[x.event_id] = x.model_copy(update={"kind": kind, "title": out.object.name_ko})
            if updated:
                candidates = [updated.get(x.event_id, x) for x in cands]
        elif tool == T_NIGHT and args.get("date") == EACH_DATE:
            for d in sorted({x.date for x in judgeable(step) if x.kind != EventKind.SEASON}):
                res = run(tool, night_args(d, args["lat"], args["lon"]), f"{d} 후보의 어두운 시간·월령")
                nights[d] = _as(NightWindowOutput, res.data) if res.ok else None
        elif tool == T_FORECAST and args.get("hours") == FROM_NIGHT:
            for d in sorted({x.date for x in judgeable(step)}):
                night = nights.get(d)
                if night is None:
                    continue                    # 관측 창을 모르면 예보도 의미 없음
                hours = J.forecast_hours(c, night)
                if not hours:
                    continue
                run(tool, forecast_args(d, args["lat"], args["lon"], hours), f"{d} 관측 창 시각의 하늘 상태")
        else:
            run(tool, args, step["reason"])

    cands = candidates or []
    summary = (f"Tool 호출 {counters['called']}회(실패 {counters['failed']}) · 캐시 재사용 {counters['reused']}회"
               f" · 후보 {len(cands)}개")
    if obj_meta:
        found = sum(1 for m in obj_meta.values() if m.get("found"))
        summary += f" · 천체 고도 {found}/{len(obj_meta)}일 계산"
    return {"cache": cache, "calls": calls, "candidates": cands, "object_windows": obj_windows,
            "object_meta": obj_meta}, summary


def similar_from_error(res: ToolResult[Any]) -> list[str]:
    """lookup_sky_object NOT_FOUND 메시지의 '비슷한 이름: A, B' / '후보: A, B' → [A, B]."""
    msg = res.error.message if res.error else ""
    m = re.search(r"(?:비슷한 이름|후보):\s*(.+)$", msg or "")
    if not m:
        return []
    return [x.strip().rstrip(".") for x in m.group(1).split(",") if x.strip().rstrip(".")][:3]


# ── judge ────────────────────────────────────────────────────

def all_unresolved(judged: list[JudgedCandidate]) -> bool:
    """후보 전부가 '지목한 대상 미확인'(R1) 으로 판단 보류인가."""
    return bool(judged) and all(
        j.verdict == Verdict.UNJUDGED and any(J.TARGET_NOT_FOUND in r or J.TARGET_TEMP_FAIL in r for r in j.reasons)
        for j in judged
    )


def lookup_inputs(state: RunState) -> tuple[dict[dt.date, NightWindowOutput | None], dict[dt.date, ToolResult[Any]]]:
    """캐시에서 후보별 박명·예보 결과를 다시 찾는다 (execute 와 같은 인자 규칙)."""
    c = state.get("conditions") or Conditions()
    cache = state.get("cache") or {}
    nights: dict[dt.date, NightWindowOutput | None] = {}
    forecasts: dict[dt.date, ToolResult[Any]] = {}
    for cand in state.get("candidates") or []:
        if cand.date in nights:
            continue
        res = cache.get(cache_key(T_NIGHT, night_args(cand.date, cand.lat, cand.lon)))
        night = _as(NightWindowOutput, res.data) if res is not None and res.ok else None
        nights[cand.date] = night
        if night is None:
            continue
        hours = J.forecast_hours(c, night)
        fres = cache.get(cache_key(T_FORECAST, forecast_args(cand.date, cand.lat, cand.lon, hours)))
        if fres is not None:
            if fres.ok and not isinstance(fres.data, SkyForecastOutput):
                data = _as(SkyForecastOutput, fres.data)
                fres = ToolResult[Any](ok=True, data=data, note=fres.note) if data else fres
            forecasts[cand.date] = fres
    return nights, forecasts


def _fatal_upstream(state: RunState) -> bool:
    """현상·박명 Tool 이 키 문제로 실패했으면 재계획해도 소용없다."""
    for call in state.get("calls") or []:
        if call["tool"] in (T_EVENTS, T_NIGHT) and call["result"].code == ErrorCode.AUTH:
            return True
    return False


def calendar_events_from(state: RunState) -> list[Any] | None:
    """이번 계획의 get_calendar 결과(execute 가 부른 것)에서 일정 목록. 없거나 실패면 None."""
    from astrowatch.tools.contracts import CalendarQueryOutput

    for step in state.get("plan") or []:
        if step["tool"] == T_GETCAL:
            res = (state.get("cache") or {}).get(cache_key(T_GETCAL, step["args"]))
            data = _as(CalendarQueryOutput, res.data) if res is not None and res.ok else None
            return list(data.events) if data else None
    return None


VERDICT_KO = {Verdict.FIT: "충족", Verdict.CONDITIONAL: "조건부 충족", Verdict.UNFIT: "불충족", Verdict.UNJUDGED: "판단 안 함"}


@stage("judge")
def judge(state: RunState) -> tuple[dict[str, Any], str]:
    if state.get("intent") != Intent.PLAN:
        return {"judged": [], "replan_pending": False}, "판정 생략 (계획 요청 아님)"
    s = get_settings()
    sid = state.get("session_id", "-")
    c = state.get("conditions") or Conditions()
    nights, forecasts = lookup_inputs(state)
    judged = J.rank_judged(J.judge_candidates(state.get("candidates") or [], c, nights, forecasts, s,
                                              object_windows=state.get("object_windows") or {},
                                              object_meta=state.get("object_meta") or {},
                                              calendar_events=calendar_events_from(state)))
    counts = {v: sum(1 for j in judged if j.verdict == v) for v in Verdict}
    summary = " · ".join(f"{VERDICT_KO[v]} {n}" for v, n in counts.items() if n) or "후보 0개"
    good = counts[Verdict.FIT] + counts[Verdict.CONDITIONAL]
    update: dict[str, Any] = {"judged": judged, "replan_pending": False, "stop_reason": None,
                              "replan_limit_reached": False}
    if good > 0:
        return update, summary

    if _fatal_upstream(state):
        update["stop_reason"] = "fatal_error"
        return update, f"{summary} · 키/인증 문제로 재계획하지 않음"
    if all_unresolved(judged):
        # 대상 자체를 확인 못 함 → 다른 날짜·지역도 같은 결과. 이름을 되묻는다 (stop_reason 없음)
        return update, f"{summary} · 지목한 대상 미확인 → 재계획하지 않음"

    count = state.get("replan_count", 0)
    tried = tried_until(state.get("replan_strategy"))
    if count < s.max_replans:
        nxt = next_strategy(c, judged, count, tried, date_defaulted=bool(state.get("date_defaulted")))
        if nxt is not None:
            name, new_c = nxt
            round_ = count + 1
            why = "충족·조건부 충족 후보 0개"
            if judged:
                why += f" ({judged[0].reasons[-1]})" if judged[0].reasons else ""
            reason = f"{why} → {STRATEGY_LABEL[name]}: {describe(name, c, new_c)}"
            bus.publish(sid, EventType.REPLAN, stage="judge",
                        payload={"round": round_, "strategy": name, "reason": reason,
                                 "limit_reached": round_ >= s.max_replans})
            history = [*(state.get("replan_history") or []), {"round": round_, "strategy": STRATEGY_LABEL[name], "reason": reason}]
            update.update(conditions=new_c, replan_count=round_, replan_strategy=name, replan_pending=True,
                          replan_history=history)
            return update, f"{summary} · 재계획 {round_}회차({STRATEGY_LABEL[name]})"
    if count >= s.max_replans:
        update.update(replan_limit_reached=True, stop_reason="limit_reached")
        return update, f"{summary} · 재계획 상한({s.max_replans}회) 도달"
    stop = exhausted_stop_reason(c, judged, count, tried)
    update["stop_reason"] = stop
    return update, f"{summary} · {STOP_LABEL.get(stop, stop)} → 재계획 종료"


STOP_LABEL = {
    "limit_reached": "재계획 상한 도달",
    "no_alternative": "대안 전략 소진",
    "target_unobservable": "대상이 그 지역에서 관측 불가",
    "fixed_constraints": "고정 조건 때문에 적용할 전략 없음",
    "fatal_error": "키/인증 문제로 중단",
}


def route_after_judge(state: RunState) -> str:
    return "plan" if state.get("replan_pending") else "respond"


# ── respond ──────────────────────────────────────────────────

def _cond_line(c: Conditions) -> str:
    parts = []
    if c.date_from:
        parts.append(str(c.date_from) if c.date_to in (None, c.date_from) else f"{c.date_from}~{c.date_to}")
    if c.region:
        parts.append(c.region)
    if c.start_minute is not None and c.end_minute is not None:
        parts.append(f"{J.label(c.start_minute)}~{J.label(c.end_minute)}")
    elif c.start_minute is not None:
        parts.append(f"{J.label(c.start_minute)}부터")
    elif c.end_minute is not None:
        parts.append(f"{J.label(c.end_minute)}까지")
    if c.event_query or c.event_kind:
        parts.append(c.event_query or c.event_kind.value)  # type: ignore[union-attr]
    return " ".join(parts) or "조건 없음"


def judged_view(j: JudgedCandidate) -> dict[str, Any]:
    tw = j.time_window
    return {
        "title": j.candidate.title,
        "date": j.candidate.date.isoformat(),
        "region": j.candidate.region,
        "verdict": VERDICT_KO[j.verdict],
        "time_window": f"{J.label(tw.start)}~{J.label(tw.end)}" if tw else None,
        "time_window_minutes": tw.minutes if tw else None,
        "clear_minutes": j.clear_minutes,
        "moon_interferes": j.moon_interferes,
        "lun_age": j.lun_age,
        "latest_arrival": J.label(j.latest_arrival_minute) if j.latest_arrival_minute is not None else None,
        "forecast_source": j.forecast_source,
        "reasons": j.reasons,
    }


def place_lookup(state: RunState) -> tuple[list[Any], bool]:
    """(장소 목록, 조회 실패 여부). 최종 조건의 지역으로 만든 정확한 cache_key 결과만 쓴다.

    재계획으로 지역이 바뀐 뒤 그 지역 검색이 실패했으면 이전 지역 장소를 붙이지 않는다.
    """
    c = state.get("conditions") or Conditions()
    region = find_region(c.region)
    if region is None:
        return [], False
    res = (state.get("cache") or {}).get(cache_key(T_PLACE, place_args(c, region)))
    if res is None:
        return [], False
    if not res.ok:
        return [], True
    data = _as(ObservatoryOutput, res.data)
    return (list(data.places) if data else []), False


def places_from(state: RunState) -> list[Any]:
    return place_lookup(state)[0]


def _tool_results_view(state: RunState) -> list[dict[str, Any]]:
    out = []
    for call in state.get("calls") or []:
        r = call["result"]
        item: dict[str, Any] = {"tool": call["tool"], "ok": r.ok, "round": call["replan_round"]}
        if r.error:
            item["error"] = r.error.code.value
            item["message"] = r.error.message
        if r.note:
            item["note"] = r.note
        out.append(item)
    return out


def plan_context(state: RunState) -> dict[str, Any]:
    s = get_settings()
    judged = state.get("judged") or []
    rec = [j for j in judged if j.verdict in (Verdict.FIT, Verdict.CONDITIONAL)]
    exc = [j for j in judged if j.verdict in (Verdict.UNFIT, Verdict.UNJUDGED)]
    places, place_failed = place_lookup(state)
    user_c = state.get("user_conditions") or state.get("conditions") or Conditions()
    searched = _cond_line(state.get("conditions") or Conditions())
    mock = any(j.forecast_source == "mock" for j in judged)
    return {
        "user_conditions": _cond_line(user_c),
        "searched_conditions": searched if searched != _cond_line(user_c) else None,
        "replan_history": state.get("replan_history") or [],
        "date_note": (f"날짜를 지정하지 않아 {state['conditions'].date_from}~{state['conditions'].date_to} 범위에서 찾았습니다"
                      if state.get("date_defaulted") and state.get("conditions") else None),
        "assumptions": state.get("assumptions") or [],
        "deep_sky": any(j.candidate.kind == EventKind.DEEP_SKY for j in judged),
        "forecast_note": "(검증용 모의 예보)" if mock else None,
        "min_obs_minutes": s.min_obs_minutes,
        "recommended": [judged_view(j) for j in rec],
        "excluded": [judged_view(j) for j in exc[:8]],
        "places": [{"name": p.name, "distance_m": p.distance_m, "url": p.url} for p in places[:5]],
        "place_lookup_failed": place_failed,
        "tool_results": _tool_results_view(state),
        "replan": {"count": state.get("replan_count", 0), "limit": s.max_replans,
                   "last_strategy": state.get("replan_strategy"),
                   "limit_reached": bool(state.get("replan_limit_reached")),
                   "stop_reason": state.get("stop_reason"),
                   "stop_reason_label": STOP_LABEL.get(state.get("stop_reason") or "")},
        "suggest_relax": (not rec) and state.get("stop_reason") in RELAX_REASONS,
        "calendar_conflicts": [f"{j.candidate.date} {j.candidate.title}: {r}" for j in judged for r in j.reasons
                               if r.startswith(J.CALENDAR_CONFLICT)][:5],
        "warnings": state.get("warnings") or [],
    }


def template_plan_answer(ctx: dict[str, Any]) -> str:
    """LLM 없이 만드는 기본 답변 (LLM 실패 대비)."""
    lines: list[str] = [f"요청 조건: {ctx['user_conditions']}"]
    for h in ctx["replan_history"]:
        lines.append(f"재계획 {h['round']}회차({h['strategy']}): {h['reason']}")
    if ctx["date_note"]:
        lines.append(ctx["date_note"])
    lines.extend(ctx["assumptions"])
    if ctx["searched_conditions"]:
        lines.append(f"최종 탐색 조건: {ctx['searched_conditions']}")
    min_obs = ctx["min_obs_minutes"]
    if ctx["recommended"]:
        lines.append("추천 (1순위부터):")
        for r in ctx["recommended"]:
            clear = f", 맑은 시간 {r['clear_minutes']}분" if r["clear_minutes"] is not None else ""
            moon = f", 월령 {r['lun_age']:g}일" if r["lun_age"] is not None else ""
            lines.append(f"- {r['date']} {r['title']} ({r['region']}) · {r['verdict']} · 관측 창 {r['time_window']}{clear}{moon}")
            if r["latest_arrival"]:
                lines.append(f"  늦어도 {r['latest_arrival']} 까지 도착하면 최소 {min_obs}분 관측할 수 있어요.")
            if r["verdict"] == "조건부 충족":
                lines.append("  날씨는 관측일 D-3부터 예보로 확인할 수 있어요.")
    else:
        lines.append("조건을 모두 만족하는 후보를 찾지 못했어요.")
    if ctx["excluded"]:
        lines.append("제외:")
        for r in ctx["excluded"][:5]:
            lines.append(f"- {r['date']} {r['title']}: {r['reasons'][-1] if r['reasons'] else r['verdict']}")
    if ctx.get("calendar_conflicts"):
        lines.append("기존 일정 반영: " + " / ".join(ctx["calendar_conflicts"]))
    if ctx["places"]:
        lines.append("근처 관측지: " + ", ".join(f"{p['name']}({p['distance_m'] / 1000:.1f}km)" for p in ctx["places"]))
    elif ctx["place_lookup_failed"]:
        lines.append("장소는 확인하지 못했습니다.")
    lines.append(f"판단 기준: 관측 창 = 어두운 시간 ∩ 내 시간 ∩ 현상 시간, 최소 {min_obs}분, 맑음(강수확률 30% 이하)만 맑은 시간으로 셈.")
    if ctx["tool_results"]:
        used = sorted({f"{t['tool']}({'성공' if t['ok'] else '실패'})" for t in ctx["tool_results"]})
        lines.append("근거: " + ", ".join(used))
    if ctx["forecast_note"]:
        lines.append(ctx["forecast_note"])
    stop = ctx["replan"].get("stop_reason")
    if stop == "limit_reached":
        lines.append(f"재계획 상한({ctx['replan']['limit']}회)에 도달해 더 찾지 않았습니다.")
    elif stop == "target_unobservable":
        lines.append("대상이 이 지역에서 관측 가능한 고도로 뜨지 않아 다른 날짜·인접 지역으로 다시 찾지 않았습니다.")
    elif stop == "fixed_constraints":
        lines.append("고정하신 조건 때문에 바꿔 볼 수 있는 조건이 없어 다시 찾지 않았습니다.")
    elif stop == "no_alternative":
        lines.append("시도할 수 있는 대안(다른 날짜·다른 현상·인접 지역)을 모두 확인했습니다.")
    if ctx.get("suggest_relax"):
        if ctx["deep_sky"]:
            lines.append("달이 적은 다음 기간(다음 합삭 전후)으로 다시 찾아 볼까요?")
        else:
            lines.append("날짜 범위나 지역을 넓혀 볼까요?")
    return "\n".join(lines)


RELAX_REASONS = {"limit_reached", "no_alternative", "fixed_constraints"}


def unresolved_target(state: RunState) -> dict[str, Any] | None:
    """후보 전부가 '지목한 대상 미확인'이면 {name, similar, temporary} (R1)."""
    if not all_unresolved(state.get("judged") or []):
        return None
    metas = [m for m in (state.get("object_meta") or {}).values() if m.get("unresolved")]
    c = state.get("user_conditions") or state.get("conditions") or Conditions()
    name = next((m.get("query") for m in metas if m.get("query")), None) or c.event_query or "요청한 천체"
    similar: list[str] = []
    for m in metas:
        for x in m.get("similar") or []:
            if x not in similar:
                similar.append(x)
    temporary = bool(metas) and all(m.get("unresolved") == "temporary" for m in metas)
    return {"name": name, "similar": similar[:3], "temporary": temporary}


def unresolved_answer(info: dict[str, Any], ctx: dict[str, Any]) -> str:
    """대상 미확인 답변 — 추천·관측 창을 말하지 않고 이름을 되묻는다 (LLM 없이 고정 형식)."""
    name = info["name"]
    lines = [f"요청 조건: {ctx['user_conditions']}"]
    if info["temporary"]:
        lines.append(f"'{name}' 천체 정보를 일시 오류로 확인하지 못해 관측 가능 여부를 판단하지 않았습니다.")
        lines.append("잠시 후 같은 요청을 다시 보내 주시겠어요?")
        return "\n".join(lines)
    lines.append(f"'{name}'{J.josa(name, '을', '를')} 천문현상 목록과 천체 카탈로그에서 찾지 못해 관측 가능 여부를 "
                 "판단하지 않았습니다.")
    lines.append("좌표를 모르는 천체는 고도와 관측 창을 추정하지 않으므로 추천하지 않았어요.")
    if ctx["tool_results"]:
        used = sorted({f"{t['tool']}({'성공' if t['ok'] else '실패'})" for t in ctx["tool_results"]})
        lines.append("근거: " + ", ".join(used))
    sims = info["similar"]
    if sims:
        last = sims[-1]
        lines.append(f"혹시 {' 또는 '.join(sims)}{J.josa(last, '을', '를')} 말씀하신가요?")
    else:
        lines.append("천체 이름을 다시 확인해 주세요.")
        lines.append("혹시 다른 이름(예: 'M42', '안드로메다 은하')을 말씀하신가요?")
    return "\n".join(lines)


def clarify_answer(missing: list[str], c: Conditions, date_issue: str | None = None) -> str:
    qs = []
    if date_issue:
        qs.append(date_issue)
        c = c.model_copy(update={"date_from": None, "date_to": None})   # 이전 날짜를 조용히 보여 주지 않는다
    for m in missing:
        if m == "date_from" and date_issue:
            continue
        if m == "region" and c.region and find_region(c.region) is None:
            qs.append(f"'{c.region}' 지역은 아직 지원하지 않아요. 가까운 시·군 이름으로 알려 주세요.")
        elif m in P.CLARIFY_QUESTIONS:
            qs.append(P.CLARIFY_QUESTIONS[m])
    if not qs:
        qs.append("관측 날짜, 지역, 시작 가능 시각을 조금 더 알려 주세요.")
    known = _cond_line(c)
    head = f"지금까지 조건: {known}\n" if known != "조건 없음" else ""
    return head + " ".join(qs)


def _llm_text(system: str, payload: dict[str, Any] | None, state: RunState, with_history: bool = False) -> str:
    model = get_chat_model()
    msgs: list[Any] = [("system", system)]
    if with_history:
        msgs.extend(_history_messages(state.get("history") or []))
    human = state.get("message", "")
    if payload is not None:
        human = f"사용자 요청: {human}\n\n판정 데이터(JSON):\n{json.dumps(payload, ensure_ascii=False, default=str)}"
    msgs.append(("human", human))
    res = model.invoke(msgs)
    text = getattr(res, "content", res)
    if isinstance(text, list):  # content blocks
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    text = str(text).strip()
    if not text:
        raise RuntimeError("빈 응답")
    return text


def memory_summary_for(state: RunState, intent: Intent) -> str:
    c = state.get("conditions") or Conditions()
    rec = [j for j in state.get("judged") or [] if j.verdict in (Verdict.FIT, Verdict.CONDITIONAL)]
    s = f"최근 조건: {_cond_line(c)}."
    if rec:
        s += " 직전 추천: " + ", ".join(f"{j.candidate.title}({j.candidate.date}, {VERDICT_KO[j.verdict]})" for j in rec[:3]) + "."
    elif intent == Intent.PLAN:
        s += " 직전 요청은 추천 후보가 없었음."
    return s


@stage("respond")
def respond(state: RunState) -> tuple[dict[str, Any], str]:
    intent = state.get("intent", Intent.CHAT)
    c = state.get("conditions") or Conditions()
    warnings = list(state.get("warnings") or [])
    fallback = False

    if intent == Intent.CLARIFY:
        answer = clarify_answer(state.get("missing") or [], c, state.get("date_issue"))
    elif intent == Intent.PLAN:
        ctx = plan_context(state)
        unresolved = unresolved_target(state)
        try:
            if unresolved is not None:
                answer = unresolved_answer(unresolved, ctx)
            elif not llm_available():
                raise RuntimeError
            else:
                answer = _llm_text(P.RESPOND_PLAN_SYSTEM.format(min_obs=ctx["min_obs_minutes"]), ctx, state)
        except Exception:  # noqa: BLE001
            answer, fallback = template_plan_answer(ctx), True
    elif intent == Intent.FACT:
        payload: dict[str, Any] = {"tool_results": fact_context(state)}
        vis = visibility_view(state) if state.get("fact_kind") == FK_OBJECT else None
        if vis is not None:
            payload["visibility"] = vis
        try:
            if not llm_available():
                raise RuntimeError
            answer = _llm_text(P.RESPOND_FACT_SYSTEM, payload, state)
        except Exception:  # noqa: BLE001
            answer = visibility_answer(vis) if vis is not None else _template_fact(payload["tool_results"])
            fallback = True
    elif intent == Intent.CHAT:
        try:
            if not llm_available() or P.NO_LLM_WARNING in warnings or P.LLM_FAIL_WARNING in warnings:
                raise RuntimeError
            answer = _llm_text(P.RESPOND_CHAT_SYSTEM, None, state, with_history=True)
        except Exception:  # noqa: BLE001
            answer = P.USAGE_TEMPLATE
    elif intent == Intent.CALENDAR:
        answer = calendar_answer(state)
    elif intent == Intent.KNOWLEDGE and registry.get(T_DOCS) is not None and state.get("calls"):
        payload = {"tool_results": fact_context(state)}
        try:
            if not llm_available():
                raise RuntimeError
            answer = _llm_text(P.RESPOND_FACT_SYSTEM, payload, state)
        except Exception:  # noqa: BLE001
            answer, fallback = _template_fact(payload["tool_results"]), True
    else:
        answer = _other_intent_answer(intent)

    if fallback and llm_available():
        warnings.append("응답 문장 생성에 실패해 기본 형식으로 보여 드립니다.")
    summary = f"{intent.value} 응답 {len(answer)}자" + (" (템플릿)" if fallback else "")
    return {"answer": answer, "warnings": warnings, "memory_summary": memory_summary_for(state, intent)}, summary


def summarize_result(tool: str, r: ToolResult[Any]) -> dict[str, Any]:
    """ToolResult 객체에서 직접 만든 응답용 요약 (건수 · 상위 5개 · 오류 코드)."""
    item: dict[str, Any] = {"tool": tool, "ok": r.ok}
    if r.error:
        item["error_code"] = r.error.code.value
        item["message"] = r.error.message
    if r.note:
        item["note"] = r.note
    if not r.ok:
        return item
    if tool == T_EVENTS and (d := _as(SkyEventsOutput, r.data)):
        item.update(year=d.year, month=d.month, count=len(d.events),
                    top=[{"date": e.date.isoformat(), "time": e.time, "title": e.title, "kind": e.kind.value}
                         for e in d.events[:5]])
    elif tool == T_NIGHT and (n := _as(NightWindowOutput, r.data)):
        item.update(n.model_dump(mode="json"))
    elif tool == T_FORECAST and (f := _as(SkyForecastOutput, r.data)):
        item.update(date=f.date.isoformat(), source=f.source, coverage=f.coverage, count=len(f.hourly),
                    top=[{"hour": h.hour, "sky": h.sky.value, "pop": h.pop} for h in f.hourly[:12]])
    elif tool == T_OBJECT and (so := _as(SkyObjectOutput, r.data)):
        item.update(object=so.object.model_dump(mode="json"), date=so.date.isoformat(), min_alt=so.min_alt,
                    visible=[f"{J.label(w.start)}~{J.label(w.end)}" for w in so.visible_windows],
                    transit=J.label(so.transit_minute), transit_alt=so.transit_alt, transit_az=so.transit_az,
                    never_rises=so.never_rises)
    elif tool == T_PLACE and (o := _as(ObservatoryOutput, r.data)):
        item.update(count=len(o.places),
                    top=[{"name": p.name, "distance_m": p.distance_m, "address": p.address} for p in o.places[:5]])
    return item


def _cached(state: RunState, tool: str) -> ToolResult[Any] | None:
    """이번 계획에서 tool 을 부른 결과 (캐시 재사용 포함)."""
    cache = state.get("cache") or {}
    for step in state.get("plan") or []:
        if step["tool"] == tool:
            return cache.get(cache_key(tool, step["args"]))
    return None


BEST_HALF = 60   # '제일 잘 보이는 시간대' 반폭(분)


def best_viewing(dark: Any, transit: int, visible: list[Window]) -> tuple[Window | None, str]:
    """어두운 창 안에서 고도가 가장 높은 시간대. (창, 근거) — 어두울 때 안 뜨면 (None, 사유).

    남중이 어두운 창 안이면 남중 ±1시간, 밖이면 남중에 가까운 어두운 창 끝 1시간. 고도 20° 이상 구간으로 자른다.
    """
    in_dark = [dark.intersect(w) for w in visible if dark.intersect(w).minutes > 0]
    if not visible:
        return None, "그날 고도 20° 이상으로 오르지 않음"
    if not in_dark:
        return None, "어두운 시간에는 고도 20° 아래 — 낮에만 떠 있음"
    if dark.start <= transit <= dark.end:
        best, basis = Window(start=max(dark.start, transit - BEST_HALF), end=min(dark.end, transit + BEST_HALF)), "남중 전후"
    elif transit < dark.start:
        best, basis = Window(start=dark.start, end=min(dark.end, dark.start + BEST_HALF)), "남중이 어두워지기 전이라 초저녁"
    else:
        best, basis = Window(start=max(dark.start, dark.end - BEST_HALF), end=dark.end), "남중이 날 밝은 뒤라 새벽"
    seg = max(in_dark, key=lambda w: best.intersect(w).minutes)
    clipped = best.intersect(seg)
    if clipped.minutes == 0:   # 고도 20° 이상 구간과 안 겹치면 그 구간에서 남중에 가까운 1시간
        clipped = (Window(start=seg.start, end=min(seg.end, seg.start + BEST_HALF)) if transit < seg.start
                   else Window(start=max(seg.start, seg.end - BEST_HALF), end=seg.end))
    return clipped, basis


def visibility_view(state: RunState) -> dict[str, Any] | None:
    """object_visibility 응답 컨텍스트: 천체 창 · 남중 · 어두운 창 · 제일 잘 보이는 시간대 (코드 계산)."""
    ores, nres = _cached(state, T_OBJECT), _cached(state, T_NIGHT)
    obj = _as(SkyObjectOutput, ores.data) if ores is not None and ores.ok else None
    night = _as(NightWindowOutput, nres.data) if nres is not None and nres.ok else None
    dark = J.dark_window(night) if night else None
    view: dict[str, Any] = {"weather_note": "날씨(구름)는 따로 확인해야 합니다 — 이 답은 고도·어두운 시간만 계산했습니다."}
    if obj is None:
        view["object_error"] = (ores.error.message if ores is not None and ores.error else "천체 고도를 계산하지 못함")
    else:
        visible = [Window(start=w.start, end=max(w.start, w.end)) for w in obj.visible_windows]
        az = obj.transit_az
        view.update(
            object=obj.object.name_ko, date=obj.date.isoformat(), min_alt=obj.min_alt,
            object_windows=[f"{J.label(w.start)}~{J.label(w.end)}" for w in visible],
            transit=J.label(obj.transit_minute), transit_alt=obj.transit_alt,
            transit_dir=_compass(az) if az is not None else None, never_rises=obj.never_rises,
        )
        if dark is not None:
            best, basis = best_viewing(dark, obj.transit_minute, visible)
            view["best_window"] = f"{J.label(best.start)}~{J.label(best.end)}" if best else None
            view["best_basis"] = basis
    if dark is not None:
        view["dark_window"] = f"{J.label(dark.start)}~{J.label(dark.end)}"
    else:
        view["dark_window"] = None
        view["dark_error"] = "어두운 시간(천문박명)을 확인하지 못함"
    return view


_DIRS = ("북", "북동", "동", "남동", "남", "남서", "서", "북서")


def _compass(az: float) -> str:
    return _DIRS[int(((az + 22.5) % 360) // 45)]


def visibility_answer(v: dict[str, Any]) -> str:
    """LLM 없이 만드는 object_visibility 답 (프롬프트와 같은 규칙)."""
    if "object" not in v:
        return f"천체 고도를 확인하지 못했습니다 ({v.get('object_error')}).\n{v['weather_note']}"
    lines = []
    name, alt = v["object"], v["min_alt"]
    if v.get("best_window"):
        lines.append(f"{v['date']} {name}이(가) 제일 잘 보이는 시간대는 {v['best_window']} 입니다 ({v['best_basis']}).")
    elif v.get("best_basis"):
        lines.append(f"{v['date']} {name}: {v['best_basis']}.")
    if v.get("object_windows"):
        lines.append(f"고도 {alt:g}° 이상: {', '.join(v['object_windows'])}")
    lines.append(f"남중 {v['transit']} (고도 {v['transit_alt']:.0f}°{', ' + v['transit_dir'] + '쪽' if v.get('transit_dir') else ''})")
    lines.append(f"어두운 시간 {v['dark_window']}" if v.get("dark_window") else v.get("dark_error", ""))
    lines.append(v["weather_note"])
    return "\n".join(x for x in lines if x)


def fact_context(state: RunState) -> list[dict[str, Any]]:
    """FACT 응답 근거. 이번 턴 호출이 없으면(캐시 재사용) 계획한 인자의 캐시 결과를 쓴다."""
    out = [summarize_result(x["tool"], x["result"]) for x in state.get("calls") or []]
    if out:
        return out
    cache = state.get("cache") or {}
    for step in state.get("plan") or []:
        res = cache.get(cache_key(step["tool"], step["args"]))
        if res is not None:
            out.append(summarize_result(step["tool"], res))
    return out


def _template_fact(results: list[dict[str, Any]]) -> str:
    if not results:
        return "조회할 내용을 찾지 못했어요."
    lines = []
    for r in results:
        if not r.get("ok"):
            lines.append(f"{r['tool']}: 확인하지 못했습니다 ({r.get('message') or r.get('error_code') or '조회 실패'}).")
        elif r["tool"] == T_EVENTS:
            top = ", ".join(f"{e['date']} {e['title']}" for e in r.get("top") or [])
            more = f" 외 {r['count'] - 5}건" if r.get("count", 0) > 5 else ""
            lines.append(f"천문현상 {r.get('count', 0)}건: {top or '없음'}{more}")
        elif r["tool"] == T_NIGHT:
            lines.append(f"{r.get('date')} 일몰 {r.get('sunset')}, 천문박명 종료 {r.get('astro_dusk_end')}, "
                         f"새벽 천문박명 시작 {r.get('astro_dawn_start')}, 월령 {r.get('lun_age')}")
        elif r["tool"] == T_PLACE:
            lines.append(f"관측지 {r.get('count', 0)}곳: " + (", ".join(p["name"] for p in r.get("top") or []) or "없음"))
        elif r["tool"] == T_OBJECT:
            name = (r.get("object") or {}).get("name_ko", "천체")
            alt = r.get("min_alt") or 20
            if r.get("never_rises"):
                lines.append(f"{r.get('date')} {name}: 고도 {alt:g}° 이상으로 오르지 않음 "
                             f"(최고 {r.get('transit_alt')}°, {r.get('transit')})")
            else:
                lines.append(f"{r.get('date')} {name}: 고도 {alt:g}° 이상 {', '.join(r.get('visible') or [])}, "
                             f"남중 {r.get('transit')}(고도 {r.get('transit_alt')}°)")
        elif r["tool"] == T_FORECAST:
            lines.append(f"{r.get('date')} 예보({r.get('source')}, {r.get('coverage')}) {r.get('count', 0)}시간치")
        else:
            lines.append(f"{r['tool']}: 조회 성공")
    return "\n".join(lines)


CALENDAR_GUIDE = ("저장한 계획 화면의 '캘린더 등록' 버튼을 누르거나 \"계획 N번을 캘린더에 넣어 줘\" 처럼 "
                  "계획 번호를 알려 주시면 등록해 드려요. (추천은 먼저 '계획 저장'으로 저장해 주세요.)")
CALENDAR_SAVE_FIRST = ("직전 추천은 아직 계획으로 저장되지 않아 계획 번호가 없어요. 먼저 '계획으로 저장' 을 누른 뒤 "
                       "\"계획 N번을 캘린더에 넣어 줘\" 라고 말씀해 주세요.")
CALENDAR_UNAVAILABLE = ("캘린더 연동은 아직 제공하지 않습니다. 추천은 화면의 '계획 저장' 버튼으로 저장해 두고 "
                        "일정은 직접 등록해 주세요.")


def calendar_answer(state: RunState) -> str:
    """CALENDAR 의도 — 등록 Tool 이 실제로 있을 때만 등록·안내한다 (R5)."""
    if registry.get(T_CALENDAR) is None:
        return CALENDAR_UNAVAILABLE
    plan_id = state.get("calendar_plan_id")
    call = next((x for x in reversed(state.get("calls") or []) if x["tool"] == T_CALENDAR), None)
    if not plan_id and state.get("has_last_recommended"):
        return CALENDAR_SAVE_FIRST
    if not plan_id or call is None:
        return CALENDAR_GUIDE
    r = call["result"]
    if not r.ok:
        if r.code == ErrorCode.NOT_FOUND:
            return f"계획 {plan_id}번을 찾지 못했습니다. 저장한 계획 목록에서 번호를 확인해 주세요."
        if r.code == ErrorCode.AUTH:
            return f"캘린더 인증이 설정되지 않아 계획 {plan_id}번을 등록하지 못했습니다."
        return f"계획 {plan_id}번을 캘린더에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."
    data = r.data if isinstance(r.data, dict) else (r.data.model_dump() if hasattr(r.data, "model_dump") else {})
    title = data.get("title") or f"계획 {plan_id}번"
    when = ""
    if data.get("start_dt") and data.get("end_dt"):
        a, b = data["start_dt"], data["end_dt"]
        fmt = (lambda v: v.strftime("%Y-%m-%d %H:%M") if hasattr(v, "strftime") else str(v))
        when = f" ({fmt(a)} ~ {fmt(b)})"
    link = f"\n일정 링크: {data['html_link']}" if data.get("html_link") else ""
    if data.get("already_registered"):
        return f"계획 {plan_id}번 '{title}'은(는) 이미 캘린더에 등록돼 있어요{when}.{link}"
    provider = {"google": "구글", "local": "로컬"}.get(str(data.get("provider")), "")
    return f"계획 {plan_id}번 '{title}'을(를) {provider + ' ' if provider else ''}캘린더에 등록했습니다(실제 캘린더에 생성됨){when}.{link}"


def _other_intent_answer(intent: Intent) -> str:
    if intent == Intent.CONFIRM:
        return "직전 추천을 계획으로 저장하려면 화면의 '계획 저장' 버튼을 눌러 주세요."
    if intent == Intent.CALENDAR:
        return CALENDAR_GUIDE if registry.get(T_CALENDAR) is not None else CALENDAR_UNAVAILABLE
    if registry.get(T_DOCS) is not None:
        return "배경 지식 문서를 확인하지 못했어요. 질문을 조금 바꿔 다시 물어봐 주세요."
    return "배경 지식 질문은 아직 지원하지 않아요. 날짜·지역·시간을 알려 주시면 관측 가능 여부를 판정해 드려요."


# 노드 이름 (그래프·이벤트 공통)
NODE_ORDER = ("analyze", "plan", "execute", "judge", "respond")
