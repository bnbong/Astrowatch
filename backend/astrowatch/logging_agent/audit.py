"""심화 F3020: 로깅 Agent 의 이상 징후 판정 (규칙 엔진, LLM 없음).

실행 로그(run_logs) 이벤트를 대조하고 횟수를 세어 판정한다.

| 규칙 | 조건 |
|---|---|
| REPEATED_CALL | 한 턴 안에서 같은 Tool + 같은 입력(정규화 JSON)의 실제 tool_call 이 2회 이상 |
| EXCESSIVE_REPLAN | 한 턴의 replan 이벤트 수 >= AUDIT_REPLAN_THRESHOLD, 또는 limit_reached=true 인 replan |
| UNPLANNED_CALL | tool_call 의 Tool 이 그 시점까지 발행된 계획(planner_tasks 또는 plan stage_end 의 planned_tools)에 없음 |

- 캐시 재사용은 tool_call 을 발행하지 않는다(nodes.execute). payload 에 from_cache/cached 표시가 있는 tool_call 도 제외한다.
- 규칙은 턴(session_start 로 시작하는 구간) 단위로 적용한다. 세션 전체 판정은 턴별 결과를 합친다.
  (턴을 넘어선 같은 입력 호출, 턴마다 새로 세는 재계획을 이상으로 오판하지 않기 위해서다)
- 근거 이벤트(tool_call, stage_end, replan, planner_tasks, worker_result)가 하나도 없으면 UNDETERMINED(판정 불가).
- audit_result, audit_diagnosis 이벤트는 판정 대상이 아니다 (자기 자신의 출력).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from astrowatch.config import get_settings
from astrowatch.schemas.audit import AuditFinding, AuditResult, AuditStatus, FindingKind

RULE_VERSION = "1"
EVIDENCE_TYPES = frozenset({"tool_call", "stage_end", "replan", "planner_tasks", "worker_result"})
SELF_TYPES = frozenset({"audit_result", "audit_diagnosis"})
CACHE_MARKERS = ("from_cache", "cached", "cache_hit")


# ── 이벤트 접근 (LogEventOut, Event, dict 모두 받는다) ─────────

def _get(e: Any, key: str) -> Any:
    if isinstance(e, dict):
        return e.get(key)
    return getattr(e, key, None)


def _type(e: Any) -> str:
    t = _get(e, "type")
    return str(getattr(t, "value", t) or "")


def _payload(e: Any) -> dict[str, Any]:
    p = _get(e, "payload")
    return p if isinstance(p, dict) else {}


def _seq(e: Any) -> int:
    try:
        return int(_get(e, "seq") or 0)
    except (TypeError, ValueError):
        return 0


def canonical_args(args: Any) -> str:
    return json.dumps(args if args is not None else {}, sort_keys=True, ensure_ascii=False, default=str)


def _clip(text: str, n: int = 300) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ── 턴 구간 ──────────────────────────────────────────────────

def split_turns(events: list[Any]) -> list[list[Any]]:
    """seq 순 이벤트를 session_start 기준으로 나눈다. 첫 session_start 앞 이벤트도 한 구간."""
    turns: list[list[Any]] = []
    cur: list[Any] = []
    for e in events:
        if _type(e) == "session_start" and cur:
            turns.append(cur)
            cur = []
        cur.append(e)
    if cur:
        turns.append(cur)
    return turns


def _planned_from(e: Any) -> list[str] | None:
    """계획 이벤트면 Tool 이름 목록, 아니면 None."""
    t, p = _type(e), _payload(e)
    if t == "planner_tasks":
        tasks = p.get("tasks") or []
        return [str(x.get("tool")) for x in tasks if isinstance(x, dict) and x.get("tool")]
    if t == "stage_end" and _get(e, "stage") == "plan" and isinstance(p.get("planned_tools"), list):
        return [str(x) for x in p["planned_tools"] if x]
    return None


def _is_real_call(e: Any) -> bool:
    if _type(e) != "tool_call":
        return False
    p = _payload(e)
    return not any(p.get(k) is True for k in CACHE_MARKERS)


# ── 규칙 (한 턴) ─────────────────────────────────────────────

def _repeated(turn: list[Any]) -> list[AuditFinding]:
    groups: dict[tuple[str, str], list[int]] = {}
    for e in turn:
        if _is_real_call(e):
            key = (str(_get(e, "tool") or ""), canonical_args(_payload(e).get("args")))
            groups.setdefault(key, []).append(_seq(e))
    out = []
    for (tool, args), seqs in groups.items():
        if len(seqs) >= 2:
            out.append(AuditFinding(
                kind=FindingKind.REPEATED_CALL,
                message=_clip(f"{tool} 를 같은 입력으로 {len(seqs)}회 호출 (입력 {args})"),
                evidence_seqs=seqs, count=len(seqs),
            ))
    return out


def _replans(turn: list[Any], threshold: int) -> list[AuditFinding]:
    reps = [e for e in turn if _type(e) == "replan"]
    if not reps:
        return []
    limit_hit = any(_payload(e).get("limit_reached") is True for e in reps)
    if len(reps) < threshold and not limit_hit:
        return []
    why = f"재계획 {len(reps)}회 (기준 {threshold}회)"
    if limit_hit:
        why += ", 재계획 상한 도달"
    return [AuditFinding(kind=FindingKind.EXCESSIVE_REPLAN, message=why,
                         evidence_seqs=[_seq(e) for e in reps], count=len(reps))]


def _unplanned(turn: list[Any]) -> tuple[list[AuditFinding], bool]:
    """(경고, 계획 근거가 있었는지). PLANNER_TASKS 가 있으면 그것만, 없으면 plan stage_end 의 planned_tools."""
    has_tasks = any(_type(e) == "planner_tasks" for e in turn)

    def plan_tools(e: Any) -> list[str] | None:
        tools = _planned_from(e)
        if tools is None or has_tasks != (_type(e) == "planner_tasks"):
            return None
        return tools

    if not any(plan_tools(e) is not None for e in turn):
        return [], False
    planned: set[str] = set()       # 그 시점까지 발행된 계획의 Tool (재계획마다 늘어난다)
    bad: dict[str, list[int]] = {}
    for e in turn:
        tools = plan_tools(e)
        if tools is not None:
            planned.update(tools)
        elif _is_real_call(e):
            tool = str(_get(e, "tool") or "")
            if tool not in planned:
                bad.setdefault(tool, []).append(_seq(e))
    out = [AuditFinding(kind=FindingKind.UNPLANNED_CALL,
                        message=_clip(f"계획에 없던 Tool {tool} 호출 {len(seqs)}회 (계획: {', '.join(sorted(planned)) or '없음'})"),
                        evidence_seqs=seqs, count=len(seqs))
           for tool, seqs in bad.items()]
    return out, True


def turn_notes(events: Iterable[Any]) -> list[str]:
    """판정과 함께 보여 줄 참고 문구 (AuditResult 계약에는 넣지 않는다). 계획 근거 없이 Tool 을 부른 턴 등."""
    notes = []
    for turn in split_turns(sorted(events, key=_seq)):
        turn = [e for e in turn if _type(e) not in SELF_TYPES]
        if any(_is_real_call(e) for e in turn) and not _unplanned(turn)[1]:
            notes.append(f"seq {_seq(turn[0])}~{_seq(turn[-1])}: 계획 이벤트가 없어 계획 외 호출 규칙을 적용하지 못함")
    return notes


# ── 진입점 ───────────────────────────────────────────────────

def audit_events(
    session_id: str,
    events: Iterable[Any],
    *,
    seq_from: int | None = None,
    seq_to: int | None = None,
    replan_threshold: int | None = None,
) -> AuditResult:
    """순수 함수. events 는 seq, type, stage, tool, payload 를 가진 행(LogEventOut, Event, dict)."""
    threshold = int(replan_threshold or get_settings().audit_replan_threshold)
    rows = sorted(
        (e for e in events
         if (seq_from is None or _seq(e) >= seq_from) and (seq_to is None or _seq(e) <= seq_to)),
        key=_seq,
    )
    examined = [e for e in rows if _type(e) not in SELF_TYPES]
    findings: list[AuditFinding] = []
    for turn in split_turns(examined):
        findings += _repeated(turn)
        findings += _replans(turn, threshold)
        findings += _unplanned(turn)[0]

    if findings:
        status = AuditStatus.ANOMALY
    elif any(_type(e) in EVIDENCE_TYPES for e in examined):
        status = AuditStatus.NORMAL
    else:
        status = AuditStatus.UNDETERMINED

    lo = seq_from if seq_from is not None else (_seq(rows[0]) if rows else 0)
    hi = seq_to if seq_to is not None else (_seq(rows[-1]) if rows else 0)
    return AuditResult(
        session_id=session_id, seq_from=lo, seq_to=hi, status=status, findings=findings,
        evidence_count=len(examined), rule_version=RULE_VERSION,
        audited_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def load_events(session_id: str, db_path: Path | None = None) -> list[Any]:
    from astrowatch.storage.run_logs import get_session_events

    return get_session_events(session_id, db_path=db_path)


def audit_turn(session_id: str, seq_from: int, seq_to: int, *, db_path: Path | None = None) -> AuditResult:
    """run_logs 에서 이 턴의 이벤트를 읽어 판정한다."""
    return audit_events(session_id, load_events(session_id, db_path), seq_from=seq_from, seq_to=seq_to)


def audit_session(session_id: str, *, db_path: Path | None = None) -> AuditResult:
    """세션 전체. 규칙은 턴별로 적용하고 결과를 합친다."""
    events = load_events(session_id, db_path)
    examined = [e for e in events if _type(e) not in SELF_TYPES]
    if not examined:
        return audit_events(session_id, [])
    return audit_events(session_id, examined, seq_from=_seq(min(examined, key=_seq)), seq_to=_seq(max(examined, key=_seq)))
