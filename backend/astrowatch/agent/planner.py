"""심화 F3021: Planner. 분석된 조건을 작업 목록(TaskList)으로 분해하고, 필요하면 다시 구성한다.

작업 목록을 만들 때마다 PLANNER_TASKS(TaskList.model_dump) 를 발행한다. 작업 행은 build_plan, fact_plan 의
PlannedCall 을 그대로 바꾼 것이다 (task_id "r{회차}-t{순번}", fixed_args, purpose=reason, depends_on).

trigger
| trigger | 언제 | 무엇을 |
|---|---|---|
| user | 첫 계획 | build_plan 결과 그대로 |
| judge_replan | judge 가 재계획을 결정 (replan.py 전략, MAX_REPLANS 와 stop_reason 그대로) | 바뀐 조건으로 다시 |
| worker_request | Worker 가 스스로 해결 못 한 실패를 넘김 | 규칙으로 전략 매핑. 규칙이 없을 때만 LLM 이 전략 선택 |
| audit_anomaly | 직전 턴 또는 턴 중 검증(verify)이 ANOMALY | 중복 작업, 계획 밖 Tool 제거 후 다시 (턴마다 한 번) |

LLM 호출 조건: 요청 해석은 analyze(LLM) 가 이미 했다. Planner 는 규칙으로만 작업을 만들고,
worker_request 중 규칙으로 매핑할 수 없는 요청이 왔고 LLM 을 쓸 수 있을 때만 LLM 에게
알려진 전략(replan.STRATEGY_ORDER) 중 하나 또는 stop 을 고르게 한다 (LLM_DECISION 이벤트로 남긴다).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from astrowatch.agent import nodes as N
from astrowatch.agent.llm import llm_available, model_name
from astrowatch.agent.replan import (
    _BUILDERS,
    OTHER_EVENTS_SAME_PERIOD,
    STRATEGY_LABEL,
    STRATEGY_ORDER,
    describe,
    tried_until,
)
from astrowatch.agent.state import PlannedCall, cache_key
from astrowatch.config import get_settings
from astrowatch.events.bus import EventType, bus
from astrowatch.schemas.domain import Conditions, Intent
from astrowatch.schemas.tasks import Task, TaskList
from astrowatch.tools.registry import registry

# 데이터 흐름 의존: 이 Tool 은 앞의 어떤 Tool 결과가 있어야 인자를 정할 수 있는가
_DEPENDS = {
    N.T_OBJECT: (N.T_EVENTS,),
    N.T_NIGHT: (N.T_EVENTS, N.T_OBJECT),
    N.T_FORECAST: (N.T_NIGHT,),
}

PLANNER_SYSTEM = """\
너는 천문 관측 계획 Agent 의 Planner 다. Worker 가 해결하지 못한 실패를 받았다. 다음 중 하나만 고른다.
- 주어진 strategies 목록의 전략 이름 하나 (목록에 없는 이름은 고르지 않는다)
- stop: 계획을 바꾸지 않고 지금 결과로 판정한다
사용자가 고정한 조건(fixed)은 바꿀 수 없다. reason 은 한국어 한 문장.
"""


class PlannerChoice(BaseModel):
    choice: str = Field(description="strategies 중 하나 또는 stop")
    reason: str = Field(description="한 문장")


# 사용자 고정 조건 → 그 조건을 담는 Tool 인자 (인자가 실제로 있을 때만 고정한다)
_DATE_ARGS = ("date", "year", "month", "date_from", "date_to")


def derive_fixed_args(tool: str, args: dict[str, Any], c: Conditions | None, given: list[str]) -> list[str]:
    """계획이 준 fixed_args 에 사용자 고정 조건을 더한다.

    | 사용자 고정 | 고정할 인자 |
    |---|---|
    | date_from 또는 date_to | date (박명, 예보, 천체), year/month (현상 검색), date_from/date_to (일정 조회) |
    | region | lat, lon |
    | event_kind | kind |
    | event_query | name (천체 고도) |
    """
    out = list(given)
    fixed = set(c.fixed) if c is not None else set()
    want: list[str] = []
    if fixed & {"date_from", "date_to"}:
        want += [k for k in _DATE_ARGS if k in args]
    if "region" in fixed:
        want += ["lat", "lon"]
    if "event_kind" in fixed:
        want.append("kind")
    if "event_query" in fixed and tool == N.T_OBJECT:
        want.append("name")
    out += [k for k in want if k in args and k not in out]
    return out


def to_tasks(calls: list[PlannedCall], round_: int, c: Conditions | None = None) -> list[Task]:
    tasks: list[Task] = []
    for i, p in enumerate(calls, 1):
        deps = [t.task_id for t in tasks if t.tool in _DEPENDS.get(p["tool"], ())]
        args = dict(p["args"])
        tasks.append(Task(task_id=f"r{round_}-t{i}", tool=p["tool"], args=args,
                          fixed_args=derive_fixed_args(p["tool"], args, c, list(p.get("fixed_args") or [])),
                          purpose=(p.get("reason") or "")[:200], depends_on=deps))
    return tasks


def to_plan(tasks: list[Task]) -> list[PlannedCall]:
    """judge, respond 가 읽는 state["plan"] (classic 과 같은 형식)."""
    return [PlannedCall(tool=t.tool, args=dict(t.args), reason=t.purpose, fixed_args=list(t.fixed_args)) for t in tasks]


def recompose(tasks: list[Task], audit: dict[str, Any] | None, base_tools: set[str]) -> tuple[list[Task], list[str]]:
    """로깅 Agent 이상 판정을 반영해 다시 구성: 같은 (Tool, 인자) 중복 제거, 계획 밖/미등록 Tool 제거."""
    seen: set[str] = set()
    kept: list[Task] = []
    notes: list[str] = []
    for t in tasks:
        key = cache_key(t.tool, t.args)
        if key in seen:
            notes.append(f"{t.task_id} 중복 제거")
            continue
        if t.tool not in base_tools or registry.get(t.tool) is None:
            notes.append(f"{t.task_id} 계획 밖 Tool({t.tool}) 제거")
            continue
        seen.add(key)
        kept.append(t)
    kinds = sorted({str(f.get("kind")) for f in (audit or {}).get("findings") or [] if isinstance(f, dict)})
    if "excessive_replan" in kinds:
        notes.append("재계획 과다 판정: 같은 인자 결과는 캐시로 재사용")
    if "repeated_call" in kinds:
        notes.append("같은 입력 반복 호출 판정: 같은 인자는 캐시로만 재사용")
    return kept, [f"이상 판정({', '.join(kinds) or 'anomaly'})", *notes]


def _is_anomaly(audit: dict[str, Any] | None) -> bool:
    return bool(audit) and str((audit or {}).get("status")) == "anomaly"


class PlannerRun:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.s = get_settings()
        self.sid = state.get("session_id", "-")
        self.update: dict[str, Any] = {"replan_pending": False, "worker_requests": [], "planner_next": "worker"}
        self.lists: list[dict[str, Any]] = list(state.get("task_lists") or [])
        self.llm_calls = 0

    # ── 공통 ──
    def build(self, c: Conditions) -> tuple[list[PlannedCall], str]:
        st = self.state
        return N.build_plan(st.get("intent", Intent.CHAT), c, st.get("fact_kind"),
                            calendar_plan_id=st.get("calendar_plan_id"), message=st.get("message", ""))

    def publish(self, tasks: list[Task], round_: int, trigger: str, reason: str) -> TaskList:
        tl = TaskList(round=round_, tasks=tasks, reason=reason[:300], trigger=trigger)
        dumped = tl.model_dump(mode="json")
        bus.publish(self.sid, EventType.PLANNER_TASKS, stage="plan", payload=dumped)
        self.lists.append(dumped)
        self.update.update(task_list=dumped, task_lists=self.lists, plan=to_plan(tasks))
        return tl

    def apply_strategy(self, name: str, c: Conditions, new_c: Conditions, why: str) -> None:
        """judge 와 같은 형식으로 재계획을 기록한다 (REPLAN 이벤트, replan_count, replan_history)."""
        count = int(self.state.get("replan_count") or 0)
        round_ = count + 1
        reason = f"{why} → {STRATEGY_LABEL[name]}: {describe(name, c, new_c)}"
        bus.publish(self.sid, EventType.REPLAN, stage="plan",
                    payload={"round": round_, "strategy": name, "reason": reason,
                             "limit_reached": round_ >= self.s.max_replans, "trigger": "worker_request"})
        history = [*(self.state.get("replan_history") or []),
                   {"round": round_, "strategy": STRATEGY_LABEL[name], "reason": reason}]
        self.update.update(conditions=new_c, replan_count=round_, replan_strategy=name, replan_history=history)

    # ── trigger 별 ──
    def first(self) -> str:
        c = self.state.get("conditions") or Conditions()
        calls, reason = self.build(c)
        tasks = to_tasks(calls, 0, c)
        tl = self.publish(tasks, 0, "user", reason)
        self.update["plan_reason"] = reason
        summary = f"{len(calls)}단계 계획: {reason}"
        prev = self.state.get("audit")
        if _is_anomaly(prev) and tasks and not self.state.get("audit_recomposed"):
            kept, notes = recompose(tl.tasks, prev, {t.tool for t in tasks})
            self.publish(kept, 0, "audit_anomaly", "직전 턴 " + ", ".join(notes))
            self.update["audit_recomposed"] = True
            summary += f", 직전 턴 이상 판정 반영해 재구성({len(kept)}개)"
        return summary

    def judge_replan(self) -> str:
        c = self.state.get("conditions") or Conditions()
        round_ = int(self.state.get("replan_count") or 0)
        calls, reason = self.build(c)
        hist = self.state.get("replan_history") or []
        why = hist[-1]["reason"] if hist else "judge 재계획"
        self.publish(to_tasks(calls, round_, c), round_, "judge_replan", why)
        self.update["plan_reason"] = reason
        return f"[재계획 {round_}회차] {len(calls)}단계 계획: {reason}"

    def audit_anomaly(self) -> str:
        audit = self.state.get("audit_inturn")
        cur = TaskList.model_validate(self.state.get("task_list") or {})
        c = self.state.get("conditions") or Conditions()
        calls, _ = self.build(c)
        kept, notes = recompose(cur.tasks, audit, {p["tool"] for p in calls})
        self.publish(kept, cur.round, "audit_anomaly", "턴 중 검증 " + ", ".join(notes))
        self.update["audit_recomposed"] = True
        return f"[로깅 Agent 이상 판정] 작업 목록 재구성 {len(cur.tasks)} → {len(kept)}개"

    def worker_request(self) -> str:
        requests = list(self.state.get("worker_requests") or [])
        self.update["worker_request_rounds"] = int(self.state.get("worker_request_rounds") or 0) + 1
        c = self.state.get("conditions") or Conditions()
        cur = TaskList.model_validate(self.state.get("task_list") or {})
        count = int(self.state.get("replan_count") or 0)
        tools = {r.get("tool") for r in requests}
        why = "; ".join(str(r.get("reason")) for r in requests)[:200]

        # 규칙 1: 관측지 검색 결과 없음 → 장소 작업만 빼고 계속 (조건 변화 없음, 재계획 횟수 그대로)
        if tools == {N.T_PLACE}:
            kept = [t for t in cur.tasks if t.tool != N.T_PLACE]
            self.publish(kept, cur.round, "worker_request", f"관측지 결과 없음 → 장소 작업 제외 ({why})")
            return f"[Worker 요청] 장소 작업 제외 {len(cur.tasks)} → {len(kept)}개"
        # 규칙 2: 지목한 대상 없음(lookup_sky_object) → 기존 미확인 대상 경로(판정이 되묻는다)
        if tools <= {N.T_OBJECT}:
            self.update["planner_next"] = "judge"
            return "[Worker 요청] 지목한 대상 미확인 → 판정 단계에서 되묻기"
        if count >= self.s.max_replans:
            self.update["planner_next"] = "judge"
            return f"[Worker 요청] 재계획 상한({self.s.max_replans}회) 도달 → 판정으로"

        name: str | None = None
        new_c: Conditions | None = None
        # 규칙 3: 현상 결과 없음(이름 지목) → 같은 기간 다른 현상
        if N.T_EVENTS in tools:
            built = _BUILDERS[OTHER_EVENTS_SAME_PERIOD](c)
            if built is not None:
                name, new_c = OTHER_EVENTS_SAME_PERIOD, built
        if name is None:
            name, new_c = self.llm_choose(c, requests)
        if name is None or new_c is None:
            self.update["planner_next"] = "judge"
            return "[Worker 요청] 적용할 전략 없음 → 지금 결과로 판정"
        self.apply_strategy(name, c, new_c, f"Worker 요청({why})")
        round_ = count + 1
        calls, reason = self.build(new_c)
        self.publish(to_tasks(calls, round_, new_c), round_, "worker_request",
                     f"Worker 요청 반영: {STRATEGY_LABEL[name]} ({why})")
        self.update["plan_reason"] = reason
        return f"[재계획 {round_}회차, Worker 요청] {STRATEGY_LABEL[name]}, {len(calls)}단계 계획"

    def llm_choose(self, c: Conditions, requests: list[dict[str, Any]]) -> tuple[str | None, Conditions | None]:
        """규칙으로 매핑할 수 없는 요청에만. 적용 가능한 전략이 없거나 LLM 이 없으면 (None, None)."""
        tried = tried_until(self.state.get("replan_strategy"))
        options = {n: _BUILDERS[n](c) for n in STRATEGY_ORDER if n not in tried}
        options = {n: v for n, v in options.items() if v is not None}
        if not options or not llm_available():
            return None, None
        payload = {"requests": requests, "strategies": {n: STRATEGY_LABEL[n] for n in options},
                   "fixed": c.fixed, "conditions": json.loads(c.model_dump_json(exclude_defaults=True))}
        self.llm_calls += 1
        try:
            out = N._structured_invoke(PlannerChoice, [("system", PLANNER_SYSTEM),
                                                       ("human", json.dumps(payload, ensure_ascii=False, default=str))])
            choice, reason = getattr(out, "choice", "stop"), getattr(out, "reason", "")
        except Exception:  # noqa: BLE001
            choice, reason = "stop", "LLM 판단 실패 → 전략 변경 없음"
        bus.publish(self.sid, EventType.LLM_DECISION, stage="plan",
                    payload={"decision": choice, "reason": reason, "model": model_name(), "agent": "planner",
                             "options": list(options)})
        if choice in options:
            return choice, options[choice]
        return None, None


@N.stage("plan")
def planner(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    run = PlannerRun(state)
    if state.get("worker_requests"):
        text = run.worker_request()
    elif state.get("replan_pending"):
        text = run.judge_replan()
    elif state.get("task_list") and _is_anomaly(state.get("audit_inturn")) and not state.get("audit_recomposed"):
        text = run.audit_anomaly()
    else:
        text = run.first()
    run.update["turn_llm_calls"] = int(state.get("turn_llm_calls") or 0) + run.llm_calls
    tl = run.update.get("task_list") or state.get("task_list") or {}
    tools = [t.get("tool") for t in tl.get("tasks") or []]
    return run.update, {"summary": f"[Planner] {text}", "planned_tools": tools,
                        "trigger": tl.get("trigger"), "round": tl.get("round")}


def route_after_planner(state: dict[str, Any]) -> str:
    return "judge" if state.get("planner_next") == "judge" else "worker"


__all__ = ["planner", "route_after_planner", "to_tasks", "to_plan", "recompose", "derive_fixed_args"]
