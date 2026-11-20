"""심화 F3021: verify 노드. 수행 결과의 검증은 로깅 Agent(규칙 엔진 audit_events)가 맡는다.

judge 가 응답으로 가기로 한 뒤, 이번 턴에서 지금까지 발행된 이벤트를 audit_events 로 판정해
state["audit_inturn"] 에 둔다. ANOMALY 이고 이번 턴에 아직 재구성하지 않았으면 Planner 로 돌아간다 (턴마다 한 번).

이벤트 출처 (단순하고 끊기지 않는 순서):
  1. service.run_turn 이 이미 들고 있는 임시 구독자 목록 (begin_turn 으로 등록, 메모리)
  2. 없으면 run_logs 저장소에서 이 세션의 마지막 session_start 이후 이벤트 (로깅 Agent 가 붙어 있을 때)
  3. 그래도 없으면 UNDETERMINED (판정 불가). 어떤 실패도 흐름을 막지 않는다.
여기서는 이벤트를 새로 발행하지 않는다 (턴이 끝난 뒤 service 가 AUDIT_RESULT 를 발행한다).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

_TURNS: dict[str, tuple[list[Any], threading.Lock, int | None]] = {}
_REG_LOCK = threading.Lock()


def begin_turn(session_id: str, events: list[Any], lock: threading.Lock, seq_from: int | None) -> None:
    with _REG_LOCK:
        _TURNS[session_id] = (events, lock, seq_from)


def end_turn(session_id: str) -> None:
    with _REG_LOCK:
        _TURNS.pop(session_id, None)


def turn_events(session_id: str) -> tuple[list[Any], int | None]:
    with _REG_LOCK:
        entry = _TURNS.get(session_id)
    if entry is not None:
        events, lock, seq_from = entry
        with lock:
            return list(events), seq_from
    try:
        from astrowatch.logging_agent.audit import load_events

        rows = load_events(session_id)
    except Exception:  # noqa: BLE001: 저장소가 없거나 비어 있음
        return [], None
    starts = [int(getattr(e, "seq", 0)) for e in rows if str(getattr(e, "type", "")) == "session_start"]
    if not starts:
        return [], None
    lo = max(starts)
    return [e for e in rows if int(getattr(e, "seq", 0)) >= lo], lo


def _undetermined(session_id: str) -> dict[str, Any]:
    from astrowatch.schemas.audit import AuditResult, AuditStatus

    return AuditResult(session_id=session_id, seq_from=0, seq_to=0, status=AuditStatus.UNDETERMINED,
                       audited_at=datetime.now(timezone.utc).isoformat(timespec="seconds")).model_dump(mode="json")


def verify(state: dict[str, Any]) -> dict[str, Any]:
    sid = state.get("session_id", "-")
    try:
        from astrowatch.logging_agent.audit import audit_events

        events, seq_from = turn_events(sid)
        if not events:
            return {"audit_inturn": _undetermined(sid)}
        result = audit_events(sid, events, seq_from=seq_from)
        return {"audit_inturn": result.model_dump(mode="json")}
    except Exception:  # noqa: BLE001: 검증 실패는 판정 불가로, 흐름은 계속
        return {"audit_inturn": _undetermined(sid)}


def route_after_verify(state: dict[str, Any]) -> str:
    audit = state.get("audit_inturn") or {}
    if str(audit.get("status")) == "anomaly" and not state.get("audit_recomposed") and state.get("task_list"):
        return "planner"
    return "respond"
