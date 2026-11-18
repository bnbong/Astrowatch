"""대화 한 턴 실행 (F3005~F3011) — API 가 부르는 진입점.

run_turn 은 어떤 경우에도 ChatResponse 를 돌려준다. 이번 턴의 이벤트는 자체 임시 구독자로
모아 화면 타임라인(StepOut)을 만든다 — 로깅 Agent 유무와 무관하다.
"""

from __future__ import annotations

import threading
from typing import Any

from astrowatch.agent import memory
from astrowatch.agent.graph import build_graph
from astrowatch.agent.nodes import T_EVENTS, T_FORECAST, T_NIGHT, T_OBJECT, T_PLACE, places_from
from astrowatch.config import get_settings
from astrowatch.events.bus import Event, EventType, bus
from astrowatch.schemas.api import ChatResponse, StepOut
from astrowatch.schemas.domain import Conditions, Intent, Verdict
from astrowatch.tools.base import ErrorCode
from astrowatch.tools.registry import load_builtin_tools, registry


def _ensure_tools() -> None:
    """앱 밖(스크립트·테스트)에서 불려도 필수 Tool 이 등록돼 있게 한다. 이미 있으면 건드리지 않는다."""
    if not registry.names():
        try:
            load_builtin_tools()
        except Exception:  # noqa: BLE001 — Tool 모듈 문제는 실행 시 BAD_INPUT 으로 드러난다
            pass

CALENDAR_WARNING = "캘린더를 확인하지 못했습니다."
GENERIC_ERROR_ANSWER = "요청을 처리하는 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."

TOOL_API_NAME = {
    T_EVENTS: "공공데이터포털(천문현상)",
    T_NIGHT: "공공데이터포털(출몰·월령)",
    T_FORECAST: "날씨 예보",
    T_PLACE: "카카오 장소 검색",
    T_OBJECT: "천체 카탈로그(로컬)",
}
TOOL_KO = {T_EVENTS: "천문현상 조회", T_NIGHT: "박명·출몰 조회", T_FORECAST: "날씨 예보 조회", T_PLACE: "관측지 검색", T_OBJECT: "천체 고도 계산"}
STAGE_KO = {"analyze": "요청 분석", "plan": "작업 계획", "execute": "Tool 실행", "judge": "판정", "respond": "응답 작성"}
_STEP_TYPES = {
    EventType.STAGE_START, EventType.STAGE_END, EventType.TOOL_CALL, EventType.TOOL_RESULT,
    EventType.LLM_DECISION, EventType.REPLAN, EventType.ERROR,
}


# ── 이벤트 → 타임라인 ─────────────────────────────────────────

def _args_text(args: dict[str, Any]) -> str:
    keys = ("year", "month", "kind", "date", "hours", "query", "radius_km")
    parts = []
    for k in keys:
        if k in args and args[k] not in (None, [], ""):
            v = args[k]
            if k == "hours" and isinstance(v, list) and v:
                v = f"{v[0]}~{v[-1]}시"
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def event_summary(e: Event) -> str:
    p = e.payload
    if e.type == EventType.STAGE_START:
        return f"{STAGE_KO.get(e.stage or '', e.stage)} 시작"
    if e.type == EventType.STAGE_END:
        return str(p.get("summary") or f"{STAGE_KO.get(e.stage or '', e.stage)} 완료")
    if e.type == EventType.TOOL_CALL:
        return f"{TOOL_KO.get(e.tool or '', e.tool)} 호출 ({_args_text(p.get('args') or {})})"
    if e.type == EventType.TOOL_RESULT:
        name = TOOL_KO.get(e.tool or "", e.tool)
        if p.get("ok"):
            return f"{name} 성공" + (f" - {p['note']}" if p.get("note") else "")
        err = p.get("error") or {}
        return f"{name} 실패({err.get('code', '?')}): {err.get('message', '')}".rstrip(": ")
    if e.type == EventType.LLM_DECISION:
        return f"의도 판단: {p.get('decision')} - {p.get('reason', '')}"
    if e.type == EventType.REPLAN:
        return f"재계획 {p.get('round')}회차: {p.get('reason', p.get('strategy'))}"
    if e.type == EventType.ERROR:
        return str(p.get("message") or "오류")
    return e.type.value


def build_steps(events: list[Event]) -> list[StepOut]:
    steps: list[StepOut] = []
    round_: int | None = None
    for e in events:
        if e.type not in _STEP_TYPES:
            continue
        if e.type == EventType.REPLAN:
            round_ = int(e.payload.get("round") or 0) or None
        steps.append(StepOut(
            seq=e.seq,
            type=e.type.value,
            stage=e.stage,
            tool=e.tool,
            ok=e.payload.get("ok") if e.type == EventType.TOOL_RESULT else None,
            summary=event_summary(e),
            duration_ms=e.payload.get("duration_ms"),
            replan_round=round_,
        ))
    return steps


# ── 경고 ─────────────────────────────────────────────────────

def tool_warnings(calls: list[Any]) -> list[str]:
    """Tool 실패를 사용자 안내로 (상세 오류·키·URL 없음, NF3002)."""
    out: list[str] = []
    for c in calls:
        code = c["result"].code
        if c["tool"] == "get_calendar":
            if not c["result"].ok and CALENDAR_WARNING not in out:
                out.append(CALENDAR_WARNING)      # 상세 오류는 넣지 않는다
            continue
        api = TOOL_API_NAME.get(c["tool"], c["tool"])
        if code == ErrorCode.AUTH:
            msg = f"{api} API 키가 설정되지 않았거나 인증에 실패했습니다."
            if c["tool"] == T_PLACE:
                msg += " 장소 정보는 확인하지 못했습니다."
        elif code in (ErrorCode.UPSTREAM, ErrorCode.TIMEOUT, ErrorCode.INTERNAL):
            msg = f"{api} 조회가 일시적으로 실패했습니다."
        else:
            continue
        if msg not in out:
            out.append(msg)
    return out


# ── 실행 ─────────────────────────────────────────────────────

def run_turn(session_id: str | None, message: str) -> ChatResponse:
    _ensure_tools()
    sid = session_id or memory.new_session_id()
    collected: list[Event] = []
    lock = threading.Lock()

    def collect(e: Event) -> None:
        if e.session_id == sid:
            with lock:
                collected.append(e)

    unsubscribe = bus.subscribe(collect)
    t = bus.timer()
    intent = Intent.CHAT
    replan_count = 0
    try:
        bus.publish(sid, EventType.SESSION_START, payload={"message": message})
        try:
            session = memory.load(sid)
        except Exception:  # noqa: BLE001
            session = memory.Session(session_id=sid)
        prev_conditions = session.conditions or Conditions()
        try:
            state = _invoke(sid, message, session)
            intent = state.get("intent") or Intent.CHAT
            replan_count = int(state.get("replan_count") or 0)
            response = _to_response(sid, state, collected, lock)
            _persist(session, state, message, response.answer)
            return response
        except Exception:  # noqa: BLE001 — 어떤 오류도 사용자에게는 일반 안내로
            bus.publish(sid, EventType.ERROR, payload={"message": "실행 중 오류가 발생했습니다"})
            with lock:
                steps = build_steps(list(collected))
            return ChatResponse(
                session_id=sid, intent=Intent.CHAT, answer=GENERIC_ERROR_ANSWER,
                conditions=prev_conditions, steps=steps,
                warnings=["처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."],
            )
    finally:
        try:
            bus.publish(sid, EventType.SESSION_END,
                        payload={"intent": getattr(intent, "value", str(intent)), "replan_count": replan_count,
                                 "duration_ms": t.ms})
        finally:
            unsubscribe()


def _invoke(sid: str, message: str, session: memory.Session) -> dict[str, Any]:
    init: dict[str, Any] = {
        "session_id": sid,
        "message": message,
        "history": list(session.history),
        "conditions": session.conditions or Conditions(),
        "memory_summary": session.memory_summary or "",
        "cache": memory.cached_tool_results(sid),
        "calls": [],
        "candidates": [],
        "judged": [],
        "plan": [],
        "missing": [],
        "warnings": [],
        "replan_count": 0,
        "replan_strategy": None,
        "replan_limit_reached": False,
        "stop_reason": None,
        "has_last_recommended": bool(session.last_recommended),
        "replan_history": [],
    }
    state = build_graph().invoke(init, config={"recursion_limit": 60})
    memory.remember_tool_results(sid, state.get("cache") or {})
    return state


def _to_response(sid: str, state: dict[str, Any], collected: list[Event], lock: threading.Lock) -> ChatResponse:
    judged = state.get("judged") or []
    intent = state.get("intent") or Intent.CHAT
    warnings = list(state.get("warnings") or [])
    for w in tool_warnings(state.get("calls") or []):
        if w not in warnings:
            warnings.append(w)
    with lock:
        steps = build_steps(list(collected))
    return ChatResponse(
        session_id=sid,
        intent=intent,
        answer=state.get("answer") or GENERIC_ERROR_ANSWER,
        conditions=state.get("user_conditions") or state.get("conditions") or Conditions(),
        needs_clarification=list(state.get("missing") or []) if intent == Intent.CLARIFY else [],
        recommended=[j for j in judged if j.verdict in (Verdict.FIT, Verdict.CONDITIONAL)],
        excluded=[j for j in judged if j.verdict in (Verdict.UNFIT, Verdict.UNJUDGED)],
        places=places_from(state),
        replan_count=int(state.get("replan_count") or 0),
        replan_limit=get_settings().max_replans,
        replan_limit_reached=bool(state.get("replan_limit_reached")),
        stop_reason=state.get("stop_reason") if intent == Intent.PLAN else None,
        steps=steps,
        warnings=warnings,
        **_safety(judged),
    )


SNOW_NOTE = " 눈·결빙 주의."


def _safety(judged: list[Any]) -> dict[str, str]:
    """추천 후보에 눈 예보가 있으면 기본 안전 문구에 '눈·결빙 주의'를 덧붙인다 (스키마는 그대로)."""
    rec = [j for j in judged if j.verdict in (Verdict.FIT, Verdict.CONDITIONAL)]
    if any("눈 예보" in r for j in rec for r in j.reasons):   # '눈'·'비/눈' 모두
        return {"safety_note": ChatResponse.model_fields["safety_note"].default + SNOW_NOTE}
    return {}


def _persist(session: memory.Session, state: dict[str, Any], message: str, answer: str) -> None:
    session.conditions = state.get("user_conditions") or state.get("conditions") or session.conditions
    session.memory_summary = state.get("memory_summary") or session.memory_summary
    rec = [j for j in state.get("judged") or [] if j.verdict in (Verdict.FIT, Verdict.CONDITIONAL)]
    if state.get("intent") == Intent.PLAN:
        session.last_recommended = rec
    session.history = memory.trim_history(
        [*session.history, {"role": "user", "content": message}, {"role": "assistant", "content": answer}]
    )
    memory.save(session)
