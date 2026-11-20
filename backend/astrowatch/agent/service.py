"""대화 한 턴 실행 (F3005~F3011) — API 가 부르는 진입점.

run_turn 은 어떤 경우에도 ChatResponse 를 돌려준다. 이번 턴의 이벤트는 자체 임시 구독자로
모아 화면 타임라인(StepOut)을 만든다 — 로깅 Agent 유무와 무관하다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from astrowatch.agent import memory
from astrowatch.agent.graph import build_graph
from astrowatch.agent.verify import begin_turn, end_turn
from astrowatch.agent.nodes import T_DOCS, T_EVENTS, T_FORECAST, T_NIGHT, T_OBJECT, T_PLACE, places_from
from astrowatch.config import get_settings
from astrowatch.events.bus import Event, EventType, bus
from astrowatch.schemas.api import AuditSummary, ChatResponse, StepOut
from astrowatch.schemas.domain import Conditions, Intent, Verdict
from astrowatch.tools.base import ErrorCode
from astrowatch.tools.registry import load_builtin_tools, registry

logger = logging.getLogger("astrowatch.agent.service")


def _ensure_tools() -> None:
    """앱 밖(스크립트·테스트)에서 불려도 필수 Tool 이 등록돼 있게 한다. 이미 있으면 건드리지 않는다."""
    if not registry.names():
        try:
            load_builtin_tools()
        except Exception:  # noqa: BLE001 (응답은 돌려주되 코드 오류는 traceback 과 함께 ERROR 로 남긴다)
            logger.exception("내장 Tool 로드 실패: Tool 모듈 코드 오류")

CALENDAR_WARNING = "캘린더를 확인하지 못했습니다."
GENERIC_ERROR_ANSWER = "요청을 처리하는 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."

TOOL_API_NAME = {
    T_EVENTS: "공공데이터포털(천문현상)",
    T_NIGHT: "공공데이터포털(출몰·월령)",
    T_FORECAST: "날씨 예보",
    T_PLACE: "카카오 장소 검색",
    T_OBJECT: "천체 카탈로그(로컬)",
    T_DOCS: "배경 지식 문서 색인(로컬)",
}
TOOL_KO = {T_EVENTS: "천문현상 조회", T_NIGHT: "박명·출몰 조회", T_FORECAST: "날씨 예보 조회", T_PLACE: "관측지 검색", T_OBJECT: "천체 고도 계산",
           T_DOCS: "배경 지식 문서 검색"}
STAGE_KO = {"analyze": "요청 분석", "plan": "작업 계획", "execute": "Tool 실행", "judge": "판정", "respond": "응답 작성"}
_STEP_TYPES = {
    EventType.STAGE_START, EventType.STAGE_END, EventType.TOOL_CALL, EventType.TOOL_RESULT,
    EventType.LLM_DECISION, EventType.REPLAN, EventType.ERROR,
    EventType.PLANNER_TASKS, EventType.WORKER_DECISION,   # 심화 F3021 (WORKER_RESULT 는 실행 로그에만)
}
DECISION_KO = {"retry": "재시도", "skip": "건너뜀", "replan_request": "계획 변경 요청"}


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
        who = "Planner 판단" if e.stage == "plan" else "의도 판단"
        return f"{who}: {p.get('decision')} - {p.get('reason', '')}"
    if e.type == EventType.PLANNER_TASKS:
        tools = [str(t.get("tool")) for t in p.get("tasks") or [] if isinstance(t, dict)]
        return f"작업 목록 {p.get('round', 0)}회차({p.get('trigger')}): " + (" → ".join(tools) or "없음")
    if e.type == EventType.WORKER_DECISION:
        src = "LLM" if p.get("source") == "llm" else "규칙"
        return (f"Worker 판단({src}): {p.get('task_id')} {TOOL_KO.get(e.tool or '', e.tool)} "
                f"{p.get('error_code')} → {DECISION_KO.get(str(p.get('decision')), p.get('decision'))} - {p.get('reason', '')}")
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
    turn_seq: int | None = None
    start_seq = end_seq = None
    response: ChatResponse | None = None
    try:
        start_seq = bus.publish(sid, EventType.SESSION_START, payload={"message": message}).seq
        begin_turn(sid, collected, lock, start_seq)     # 심화 F3021: verify 노드가 이번 턴 이벤트를 읽는다
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
            turn_seq = _record_turn(sid, message, response)
        except Exception:  # noqa: BLE001 — 어떤 오류도 사용자에게는 일반 안내로
            bus.publish(sid, EventType.ERROR, payload={"message": "실행 중 오류가 발생했습니다"})
            with lock:
                steps = build_steps(list(collected))
            response = ChatResponse(
                session_id=sid, intent=Intent.CHAT, answer=GENERIC_ERROR_ANSWER,
                conditions=prev_conditions, steps=steps,
                warnings=["처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."],
            )
    finally:
        try:
            end_seq = bus.publish(sid, EventType.SESSION_END,
                                  payload={"intent": getattr(intent, "value", str(intent)), "replan_count": replan_count,
                                           "duration_ms": t.ms}).seq
        finally:
            unsubscribe()
            end_turn(sid)
    # 심화 F3020: 턴이 끝나고 저장된 뒤 로깅 Agent 가 이 턴(SESSION_START..SESSION_END)을 판정한다
    response.audit = audit_after_turn(sid, start_seq, end_seq)
    # 심화 F3021: 요청당 LLM 판단 호출 수 (이벤트 기준, 진단 포함)
    with lock:
        response.llm_calls = count_llm_calls(collected) + (1 if response.audit and response.audit.diagnosis_text else 0)
    # 저장한 턴에도 판정을 붙인다 (복원한 대화에서도 판정이 보이게)
    if turn_seq is not None:
        _update_turn(sid, turn_seq, response)
    return response


def count_llm_calls(events: list[Event]) -> int:
    """이벤트로 남는 LLM 호출: analyze/Planner 의 llm_decision + Worker 의 LLM 판단(실패 시도 포함) + 진단."""
    n = 0
    for e in events:
        if e.type == EventType.LLM_DECISION or e.type == EventType.AUDIT_DIAGNOSIS:
            n += 1
        elif e.type == EventType.WORKER_DECISION and (e.payload.get("source") == "llm" or e.payload.get("llm_attempted")):
            n += 1
    return n


def audit_after_turn(sid: str, seq_from: int | None, seq_to: int | None) -> AuditSummary | None:
    """판정 + (이상일 때만) 진단 + 저장 + AUDIT_RESULT/AUDIT_DIAGNOSIS 발행. 실패는 기록만 하고 None."""
    if seq_from is None or seq_to is None:
        return None
    try:
        from astrowatch.logging_agent.audit import audit_events, load_events
        from astrowatch.logging_agent.diagnosis import diagnose
        from astrowatch.storage.run_audits import save_audit

        events = load_events(sid)
        result = audit_events(sid, events, seq_from=seq_from, seq_to=seq_to)
        diagnosis = diagnose(result, events)          # 정상, 판정 불가에는 LLM 을 부르지 않는다 (None)
        saved = result.model_copy(update={"diagnosis": diagnosis})
        save_audit(saved)
        bus.publish(sid, EventType.AUDIT_RESULT, stage="audit", payload=result.model_dump(mode="json", exclude={"diagnosis"}))
        if diagnosis is not None:
            bus.publish(sid, EventType.AUDIT_DIAGNOSIS, stage="audit",
                        payload={"seq_from": seq_from, "seq_to": seq_to, **diagnosis.model_dump(mode="json")})
        return AuditSummary.from_result(saved)
    except Exception as exc:  # noqa: BLE001 (로깅 Agent 는 메인 흐름을 막지 않는다)
        logger.warning("로깅 Agent 판정 실패(응답은 정상 반환): %s", type(exc).__name__)
        return None


def _latest_audit(sid: str) -> dict[str, Any] | None:
    """직전 판정(메인 Agent 가 참조). 없거나 읽기 실패면 None."""
    try:
        from astrowatch.storage.run_audits import get_latest

        latest = get_latest(sid)
        return latest.model_dump(mode="json") if latest else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("직전 판정 읽기 실패: %s", type(exc).__name__)
        return None


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
        "audit": _latest_audit(sid),   # 심화 F3020: 직전 턴의 로깅 Agent 판정
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
        session.last_recommended = [j.candidate.title for j in rec]   # 제목만 (카드 복원은 turns.response_json)
    session.history = memory.trim_history(
        [*session.history, {"role": "user", "content": message}, {"role": "assistant", "content": answer}]
    )
    memory.save(session)


def _record_turn(sid: str, message: str, response: ChatResponse) -> int | None:
    """화면 복원용 턴 저장. 실패해도 응답에는 영향을 주지 않는다. 저장한 턴 seq (실패면 None)."""
    try:
        from astrowatch.storage.turns import append_turn

        return append_turn(sid, message, response)
    except Exception as exc:  # noqa: BLE001
        logger.warning("대화 턴 저장 실패(응답은 정상 반환): %s", type(exc).__name__)
        return None


def _update_turn(sid: str, seq: int, response: ChatResponse) -> None:
    """턴 종료 뒤 붙은 판정(audit), llm_calls 를 저장한 턴에 반영. 실패는 기록만."""
    try:
        from astrowatch.storage.turns import update_turn_response

        if not update_turn_response(sid, seq, response):
            logger.warning("대화 턴 판정 갱신 실패(응답은 정상 반환): 저장된 턴 없음 (seq=%s)", seq)
    except Exception as exc:  # noqa: BLE001
        logger.warning("대화 턴 판정 갱신 실패(응답은 정상 반환): %s", type(exc).__name__)
