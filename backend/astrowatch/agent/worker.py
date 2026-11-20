"""심화 F3021: Worker. Planner 의 작업 목록(TaskList)을 순서대로 Tool 호출로 수행한다.

- Tool 은 필수 구현의 외부 API Tool 을 그대로 부른다 (registry.invoke). 데이터 흐름과 캐시는 classic 의
  execute 와 같다: 현상 → 후보 날짜 → 박명 → 관측 창 시각의 예보.
- 작업마다 WORKER_RESULT 를 발행한다. 실패 뒤 판단(재시도, 건너뜀, 계획 변경 요청)은 WORKER_DECISION 으로 남긴다.

LLM 호출 조건 (모두 만족할 때만):
  1. 실패 코드가 인자를 고치면 결과가 달라질 수 있는 것: NO_RESULT(결과 없음), NOT_FOUND(대상 없음),
     BAD_INPUT 중 형식 오류(잘못된 입력). Tool 응답은 성공이지만 지목한 현상이 목록에 없을 때도 NO_RESULT 로 본다.
  2. settings.worker_llm 이 켜져 있고 LLM 을 쓸 수 있다.
  3. 그 작업의 재시도 횟수가 settings.worker_max_retry 미만이다.
  4. 바꿀 수 있는 인자(고정 인자가 아닌 입력)가 하나 이상 있다.
LLM 을 부르지 않는 경우: 정상 경로, 치명적 실패(AUTH, OUT_OF_RANGE), 일시 장애(UPSTREAM, TIMEOUT, INTERNAL),
  필수 인자 누락(호출 전 검증), 모호함(AMBIGUOUS: 후보가 여럿인 이름), lookup_sky_object 의 대상 없음
  (이름을 추측해 바꾸지 않는다: 판정 단계가 되묻는다). 이때는 규칙으로 판단하고 source="rule" 로 남긴다.

검증: 재시도는 같은 Tool 로만, 고정 인자(Task.fixed_args)는 원래 값 그대로여야 하고, 원래와 같은 인자는 거부한다.
후보 날짜마다 펼치는 작업(date=@candidate_dates)의 date, lat, lon 은 결과를 찾는 열쇠라 언제나 고정으로 본다.
어기면 건너뜀(skip)으로 낮추고 사유를 남긴다. Worker 는 작업 목록에 없는 Tool 을 부르지 않는다.

캐시: 고친 인자로 성공한 결과는 실제 인자 키에 저장한다. 바뀐 인자가 날짜, 좌표 같은 식별 인자가 아닐 때만
(예: hours, query, radius_km) 계획 인자 키에도 둔다. 다른 날짜의 결과가 원래 날짜의 결과로 읽히지 않게 하기 위해서다.
WorkerResult.args_used 는 실제 인자, args_changed 는 바뀐 인자 이름이다.

필수 작업과 선택 작업:
| 구분 | Tool |
|---|---|
| 필수 | search_sky_events (현상을 이름으로 지목했을 때), get_night_window, get_sky_forecast, lookup_sky_object |
| 선택 | search_observatory, get_calendar, 그 밖의 Tool |
필수 작업이 끝내 해결되지 않으면(필수 인자 누락, 거부된 재시도, LLM 실패, 반복 실패) 건너뛰지 않고
replan_request 로 Planner 에 넘긴다. 선택 작업은 건너뛴다. 치명적 실패(AUTH, OUT_OF_RANGE)와
일시 장애(UPSTREAM, TIMEOUT, INTERNAL)는 지금처럼 건너뛰고 판정 단계의 기존 경로(fatal_error 등)가 맡는다.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from astrowatch.agent import nodes as N
from astrowatch.agent.llm import llm_available, model_name
from astrowatch.agent.state import ExecutedCall, cache_key
from astrowatch.config import get_settings
from astrowatch.data.regions import find_region
from astrowatch.events.bus import EventType, bus
from astrowatch.schemas.domain import Candidate, Conditions, Window
from astrowatch.schemas.tasks import Task, TaskList, WorkerDecision, WorkerDecisionKind, WorkerResult
from astrowatch.tools.base import ErrorCode, ToolResult, fail
from astrowatch.tools.contracts import NightWindowOutput, SkyEvent, SkyEventsOutput
from astrowatch.tools.registry import registry

# 인자를 고치면 결과가 달라질 수 있는 실패 (LLM 판단 대상)
ARG_FIXABLE = frozenset({ErrorCode.NO_RESULT, ErrorCode.NOT_FOUND, ErrorCode.BAD_INPUT})
# 결과가 "어느 날짜, 어느 곳, 어떤 대상" 의 것인지 정하는 인자. 바뀌면 계획 인자 키에 결과를 두지 않는다
IDENTITY_ARGS = frozenset({"date", "year", "month", "lat", "lon", "name", "kind", "plan_id", "date_from", "date_to"})
# 필수 작업이라도 Planner 로 넘기지 않는 실패: 치명적(판정의 fatal_error 경로), 일시 장애(판정이 미판정으로 처리)
NO_ESCALATE = frozenset({ErrorCode.AUTH, ErrorCode.OUT_OF_RANGE, ErrorCode.UPSTREAM, ErrorCode.TIMEOUT,
                         ErrorCode.INTERNAL})
REQUIRED_TOOLS = frozenset({N.T_NIGHT, N.T_FORECAST, N.T_OBJECT})
MISSING_PREFIX = "필수 인자 누락"
AMBIGUOUS_WORDS = ("모호", "후보:")

WORKER_SYSTEM = """\
너는 천문 관측 계획 Agent 의 Worker 다. 작업 하나의 Tool 호출이 실패했다. 다음 중 하나를 고른다.
- retry: 같은 Tool 을 인자를 고쳐 다시 부르면 결과가 나올 가능성이 높을 때. new_args_json 에 고친 뒤의 전체 인자를 JSON 객체 문자열로.
- skip: 이 작업 없이도 계획을 이어 갈 수 있을 때 (예: 관측지 검색 결과 없음).
- replan_request: 이 작업의 결과가 없으면 계획 자체를 바꿔야 할 때 (예: 지목한 현상이 그 기간 목록에 없음).
규칙:
- fixed_args 에 있는 인자는 사용자 조건이므로 절대 바꾸지 않는다. 바꾸면 거부된다.
- 원래와 같은 인자로 재시도하지 않는다. 입력 스키마에 없는 인자를 만들지 않는다.
- 존재하지 않을 것 같은 대상의 이름을 추측해 다른 이름으로 바꾸지 않는다.
- reason 은 한국어 한 문장.
"""


class WorkerDecisionOut(BaseModel):
    """LLM structured output. strict 스키마 호환을 위해 인자는 JSON 문자열로 받는다 (new_args)."""

    decision: Literal["retry", "skip", "replan_request"]
    new_args_json: str | None = Field(description="retry 일 때만: 고친 뒤 전체 인자 JSON 객체 문자열. 아니면 null")
    reason: str = Field(description="판단 이유 한 문장")


@dataclass
class _TaskRun:
    task: Task
    attempts: int = 0
    retries: int = 0
    real_calls: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)   # 최종 실패 (code, message)
    decision: WorkerDecision | None = None
    replan_request: str | None = None
    args_used: dict[str, Any] = field(default_factory=dict)
    args_changed: list[str] = field(default_factory=list)
    t0: float = field(default_factory=time.perf_counter)

    def result(self) -> WorkerResult:
        code = self.failures[-1][0] if self.failures else None
        return WorkerResult(
            task_id=self.task.task_id, tool=self.task.tool, args_used=self.args_used or dict(self.task.args),
            ok=not self.failures, error_code=code, attempts=self.attempts,
            from_cache=self.attempts > 0 and self.real_calls == 0,
            duration_ms=int((time.perf_counter() - self.t0) * 1000),
            decision=self.decision, replan_request=self.replan_request, args_changed=list(self.args_changed),
        )


def required_args(tool: str) -> list[str]:
    spec = registry.get(tool)
    if spec is None:
        return []
    try:
        return list(spec.input_model.model_json_schema().get("required") or [])
    except Exception:  # noqa: BLE001
        return []


def arg_names(tool: str) -> list[str]:
    spec = registry.get(tool)
    if spec is None:
        return []
    try:
        return list((spec.input_model.model_json_schema().get("properties") or {}).keys())
    except Exception:  # noqa: BLE001
        return []


def effective_fixed(task: Task) -> list[str]:
    """Worker 가 바꿀 수 없는 인자: 사용자 고정 인자 + 후보 날짜마다 펼치는 작업의 식별 인자(date, lat, lon)."""
    out = list(task.fixed_args)
    if task.args.get("date") == N.EACH_DATE:
        out += [k for k in ("date", "lat", "lon") if k not in out]
    return out


def changed_args(orig: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = sorted(set(orig) | set(new))
    return [k for k in keys if json.dumps(orig.get(k), default=str, sort_keys=True)
            != json.dumps(new.get(k), default=str, sort_keys=True)]


def is_required(task: Task, c: Conditions) -> bool:
    """해결하지 못하면 Planner 에 넘길 작업인가. 현상 검색은 현상을 이름으로 지목했을 때만 필수."""
    if task.tool == N.T_EVENTS:
        return bool(c.event_query) and not N.is_target_query(c)
    return task.tool in REQUIRED_TOOLS


def rule_decision(tool: str, code: str | None, why: str) -> WorkerDecision:
    """worker_llm 이 꺼졌거나 LLM 을 부르지 않는 경우의 규칙."""
    if code == ErrorCode.NO_RESULT.value and tool == N.T_EVENTS:
        return WorkerDecision(kind=WorkerDecisionKind.REPLAN_REQUEST, source="rule",
                              reason=f"{why} → 현상 결과 없음은 계획 변경 요청 (규칙)"[:300])
    return WorkerDecision(kind=WorkerDecisionKind.SKIP, source="rule", reason=f"{why} → 건너뜀 (규칙)"[:300])


def validate_retry(task: Task, tool: str, orig: dict[str, Any], new_args: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """(검증을 통과한 새 인자, 거부 사유). 고정 인자 변경, 같은 인자, 필수 인자 누락은 거부."""
    if not isinstance(new_args, dict) or not new_args:
        return None, "재시도 인자가 비어 있음"
    allowed = set(arg_names(tool)) or set(orig)
    merged = dict(orig)
    for k, v in new_args.items():
        if k not in allowed:
            continue
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    changed = [k for k in effective_fixed(task) if json.dumps(merged.get(k), default=str) != json.dumps(orig.get(k), default=str)]
    if changed:
        return None, f"고정 인자 변경 시도 거부({', '.join(changed)})"
    if cache_key(tool, merged) == cache_key(tool, orig):
        return None, "원래와 같은 인자로는 재시도하지 않음"
    missing = [k for k in required_args(tool) if merged.get(k) in (None, "")]
    if missing:
        return None, f"재시도 인자에 필수 인자 누락({', '.join(missing)})"
    return merged, ""


def ask_llm(task: Task, tool: str, args: dict[str, Any], code: str, message: str, retries_left: int) -> WorkerDecisionOut:
    payload = {
        "task": {"task_id": task.task_id, "tool": tool, "purpose": task.purpose},
        "args": args, "fixed_args": effective_fixed(task), "error": {"code": code, "message": message},
        "input_schema": arg_names(tool), "retries_left": retries_left,
    }
    messages = [("system", WORKER_SYSTEM), ("human", json.dumps(payload, ensure_ascii=False, default=str))]
    out = N._structured_invoke(WorkerDecisionOut, messages)
    assert isinstance(out, WorkerDecisionOut)
    return out


class WorkerRun:
    """한 번의 Worker 실행 (작업 목록 전체)."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.s = get_settings()
        self.sid = state.get("session_id", "-")
        self.c: Conditions = state.get("conditions") or Conditions()
        self.cache: dict[str, ToolResult[Any]] = dict(state.get("cache") or {})
        self.calls: list[ExecutedCall] = list(state.get("calls") or [])
        self.round = int(state.get("replan_count", 0) or 0)
        self.counters = {"called": 0, "reused": 0, "failed": 0, "llm": 0}
        self.events: list[SkyEvent] = []
        self.candidates: list[Candidate] | None = None
        self.nights: dict[dt.date, NightWindowOutput | None] = {}
        self.obj_windows: dict[tuple[str, dt.date], list[Window]] = {}
        self.obj_meta: dict[tuple[str, dt.date], dict[str, Any]] = {}
        self.results: list[WorkerResult] = []
        self.requests: list[dict[str, Any]] = []
        raw = state.get("task_list") or {}
        self.task_list = TaskList.model_validate(raw) if raw else TaskList()

    # ── 호출 한 번 ──
    def _call(self, run: _TaskRun, tool: str, args: dict[str, Any], reason: str) -> tuple[ToolResult[Any], bool]:
        """(결과, 필수 인자 누락으로 호출 전 거부했는가)."""
        run.attempts += 1
        run.args_used = args
        missing = [k for k in required_args(tool) if args.get(k) in (None, "")]
        if missing:
            return fail(ErrorCode.BAD_INPUT, f"{MISSING_PREFIX}: {', '.join(missing)}"), True
        key = cache_key(tool, args)
        side_effect = tool in N._NO_CACHE_TOOLS
        if key in self.cache and not side_effect:
            self.counters["reused"] += 1
            return self.cache[key], False
        bus.publish(self.sid, EventType.TOOL_CALL, stage="execute", tool=tool,
                    payload={"args": args, "reason": reason, "task_id": run.task.task_id,
                             **({"retry": run.retries} if run.retries else {})})
        t = bus.timer()
        result = registry.invoke(tool, args)
        ms = t.ms
        bus.publish(self.sid, EventType.TOOL_RESULT, stage="execute", tool=tool,
                    payload={**result.as_event_payload(), "duration_ms": ms, "task_id": run.task.task_id})
        if not side_effect:
            self.cache[key] = result
        self.calls.append(ExecutedCall(tool=tool, args=args, result=result, duration_ms=ms, replan_round=self.round))
        run.real_calls += 1
        self.counters["called"] += 1
        if not result.ok:
            self.counters["failed"] += 1
        return result, False

    # ── 실패 뒤 판단 ──
    def _decide(self, run: _TaskRun, tool: str, args: dict[str, Any], code: str, message: str,
                missing: bool) -> WorkerDecision:
        task = run.task
        code_enum = ErrorCode(code) if code in ErrorCode._value2member_map_ else None
        llm_attempted = False
        if missing:
            d = WorkerDecision(kind=WorkerDecisionKind.SKIP, source="rule",
                               reason=f"{MISSING_PREFIX} → LLM 미호출, 작업 중단(되물을 대상)"[:300])
        elif code_enum not in ARG_FIXABLE:
            label = "치명적 실패" if code_enum is not None and code_enum.fatal else "일시 장애"
            d = rule_decision(tool, code, f"{label}({code}) → LLM 미호출")
        elif any(w in message for w in AMBIGUOUS_WORDS):
            d = rule_decision(tool, code, f"모호함(AMBIGUOUS, {code}) → LLM 미호출")
        elif tool == N.T_OBJECT:
            d = rule_decision(tool, code, "대상 없음: 이름을 추측해 바꾸지 않음, 판정 단계가 되묻는다")
        elif run.retries >= self.s.worker_max_retry:
            d = rule_decision(tool, code, f"재시도 상한({self.s.worker_max_retry}회) 도달, 재시도 {run.retries}회로 해결 못 함")
        elif not [k for k in arg_names(tool) if k not in effective_fixed(task)]:
            d = rule_decision(tool, code, "바꿀 수 있는 인자 없음(모두 고정)")
        elif not (self.s.worker_llm and llm_available()):
            d = rule_decision(tool, code, f"{code} (Worker LLM 꺼짐)" if not self.s.worker_llm else f"{code} (LLM 없음)")
        else:
            llm_attempted = True
            self.counters["llm"] += 1
            try:
                out = ask_llm(task, tool, args, code, message, self.s.worker_max_retry - run.retries)
                kind = WorkerDecisionKind(out.decision)
                new_args: dict[str, Any] | None = None
                reason = out.reason
                if kind == WorkerDecisionKind.RETRY:
                    try:
                        parsed = json.loads(out.new_args_json or "")
                    except (TypeError, ValueError):
                        parsed = None
                    new_args, why = validate_retry(task, tool, args, parsed)
                    if new_args is None:
                        kind = WorkerDecisionKind.SKIP
                        reason = f"재시도 거부: {why} (LLM: {out.reason})"
                d = WorkerDecision(kind=kind, reason=reason[:300], new_args=new_args, source="llm")
            except Exception:  # noqa: BLE001: LLM 실패는 규칙으로
                d = rule_decision(tool, code, f"{code}, LLM 판단 실패")
        d = self._escalate(task, code_enum, d)
        bus.publish(self.sid, EventType.WORKER_DECISION, stage="execute", tool=tool, payload={
            "task_id": task.task_id, "tool": tool, "decision": d.kind.value, "reason": d.reason,
            "model": model_name() if d.source == "llm" or llm_attempted else None,
            "new_args": d.new_args, "source": d.source, "error_code": code, "args": args,
            "fixed_args": effective_fixed(task), "llm_attempted": llm_attempted, "retry": run.retries,
        })
        return d

    def _escalate(self, task: Task, code: ErrorCode | None, d: WorkerDecision) -> WorkerDecision:
        """필수 작업의 건너뜀은 Planner 요청으로 올린다 (치명적 실패, 일시 장애는 제외). 선택 작업은 그대로."""
        if d.kind != WorkerDecisionKind.SKIP or not is_required(task, self.c) or code in NO_ESCALATE:
            return d
        return d.model_copy(update={"kind": WorkerDecisionKind.REPLAN_REQUEST,
                                    "reason": f"{d.reason} → 필수 작업 미해결, Planner 에 전달"[:300]})

    def attempt(self, run: _TaskRun, tool: str, args: dict[str, Any], reason: str, *,
                on_ok: Callable[[ToolResult[Any]], None] | None = None,
                check: Callable[[], tuple[str, str] | None] | None = None) -> ToolResult[Any]:
        """호출 → 실패면 판단 → (재시도면) 같은 Tool 을 고친 인자로. check 는 작업 목적 기준 실패 판정."""
        orig_key = cache_key(tool, args)
        cur = args
        while True:
            res, missing = self._call(run, tool, cur, reason)
            failure: tuple[str, str] | None = None
            if res.ok:
                if on_ok:
                    on_ok(res)
                failure = check() if check else None
            else:
                failure = (res.code.value if res.code else "INTERNAL", res.error.message if res.error else "")
            if failure is None:
                if cur is not args:
                    changed = changed_args(args, cur)
                    run.args_changed = sorted(set(run.args_changed) | set(changed))
                    if tool not in N._NO_CACHE_TOOLS and not IDENTITY_ARGS.intersection(changed):
                        # 식별 인자가 그대로일 때만 계획 인자 자리에도 둔다 (판정, 응답이 계획 인자로 찾는다).
                        # 날짜, 좌표, 대상이 바뀐 결과는 실제 인자 키에만 있다 (다른 날짜의 결과로 읽히지 않게)
                        note = f"Worker 재시도 결과(인자 수정: {json.dumps(cur, ensure_ascii=False, default=str)[:120]})"
                        self.cache[orig_key] = res.model_copy(update={"note": f"{res.note} / {note}" if res.note else note})
                return res
            code, message = failure
            d = self._decide(run, tool, cur, code, message, missing)
            run.decision = d
            if d.kind == WorkerDecisionKind.RETRY and d.new_args:
                run.retries += 1
                cur = d.new_args
                continue
            run.failures.append((code, message))
            if d.kind == WorkerDecisionKind.REPLAN_REQUEST:
                run.replan_request = f"{tool} {code}: {message} / {d.reason}"[:300]
            return res

    # ── 작업 목록 수행 (execute 와 같은 데이터 흐름) ──
    def _ensure_candidates(self, task: Task) -> list[Candidate]:
        if self.candidates is None:
            region = find_region(self.c.region)
            name = region.name if region else (self.c.region or "")
            self.candidates = N.build_candidates(self.events, self.c, name, float(task.args["lat"]), float(task.args["lon"]))
        return self.candidates

    def _judgeable(self, task: Task) -> list[Candidate]:
        return N.judgeable_candidates(self._ensure_candidates(task), self.obj_meta)

    def _event_missing(self) -> tuple[str, str] | None:
        """지목한 현상(이름)이 받은 현상 목록에 없으면 NO_RESULT (Tool 응답은 성공)."""
        c = self.c
        if not c.event_query or N.is_target_query(c):
            return None
        pool = [e for e in self.events if c.event_kind is None or e.kind == c.event_kind]
        if N._match_event(c.event_query, pool):
            return None
        return ErrorCode.NO_RESULT.value, f"'{c.event_query}' 이(가) 조회한 현상 목록에 없음"

    def run_task(self, task: Task, is_last_events: bool) -> None:
        run = _TaskRun(task=task)
        tool, args = task.tool, task.args
        if tool == N.T_EVENTS:
            def add(res: ToolResult[Any]) -> None:
                data = N._as(SkyEventsOutput, res.data)
                if data:
                    self.events.extend(data.events)
            self.attempt(run, tool, args, task.purpose, on_ok=add,
                         check=self._event_missing if is_last_events else None)
        elif tool == N.T_OBJECT and args.get("date") == N.EACH_DATE:
            cands = self._ensure_candidates(task)
            targets = [x for x in cands if x.event_id.startswith(N.TARGET_PREFIX)]
            updated: dict[str, Candidate] = {}
            for x in targets:
                res = self.attempt(run, tool, N.object_args(args["name"], x.date, args["lat"], args["lon"]),
                                   f"{x.date} '{args['name']}' 고도 구간")
                key = (x.event_id, x.date)
                self.obj_meta[key], windows, refreshed = N.object_lookup(x, args["name"], res)
                if refreshed is not None and windows is not None:
                    self.obj_windows[key] = windows
                    updated[x.event_id] = refreshed
            if updated:
                self.candidates = [updated.get(x.event_id, x) for x in cands]
        elif tool == N.T_NIGHT and args.get("date") == N.EACH_DATE:
            for d in N.night_dates(self._judgeable(task)):
                res = self.attempt(run, tool, N.night_args(d, args["lat"], args["lon"]), f"{d} 후보의 어두운 시간, 월령")
                self.nights[d] = N._as(NightWindowOutput, res.data) if res.ok else None
        elif tool == N.T_FORECAST and args.get("hours") == N.FROM_NIGHT:
            for d, hours in N.forecast_requests(self.c, self._judgeable(task), self.nights):
                self.attempt(run, tool, N.forecast_args(d, args["lat"], args["lon"], hours), f"{d} 관측 창 시각의 하늘 상태")
        else:
            self.attempt(run, tool, args, task.purpose)
        result = run.result()
        self.results.append(result)
        bus.publish(self.sid, EventType.WORKER_RESULT, stage="execute", tool=tool, payload=result.model_dump(mode="json"))
        if result.replan_request:
            self.requests.append({"task_id": task.task_id, "tool": tool, "error_code": result.error_code,
                                  "reason": result.replan_request})

    def run_all(self) -> None:
        tasks = self.task_list.tasks
        last_events = max((i for i, t in enumerate(tasks) if t.tool == N.T_EVENTS), default=-1)
        for i, task in enumerate(tasks):
            self.run_task(task, i == last_events)

    def update(self) -> dict[str, Any]:
        return {"cache": self.cache, "calls": self.calls, "candidates": self.candidates or [],
                "object_windows": self.obj_windows, "object_meta": self.obj_meta,
                "worker_requests": self.requests}

    def summary(self) -> str:
        cands = self.candidates or []
        text = (f"[Worker] 작업 {len(self.results)}개, Tool 호출 {self.counters['called']}회(실패 {self.counters['failed']})"
                f", 캐시 재사용 {self.counters['reused']}회, 후보 {len(cands)}개")
        if self.obj_meta:
            found = sum(1 for m in self.obj_meta.values() if m.get("found"))
            text += f", 천체 고도 {found}/{len(self.obj_meta)}일 계산"
        if self.counters["llm"]:
            text += f", LLM 판단 {self.counters['llm']}회"
        if self.requests:
            text += f", 계획 변경 요청 {len(self.requests)}건"
        return text


@N.stage("execute")
def worker(state: dict[str, Any]) -> tuple[dict[str, Any], str]:
    run = WorkerRun(state)
    run.run_all()
    return run.update(), run.summary()


def route_after_worker(state: dict[str, Any]) -> str:
    """해결 못 한 실패가 있으면 Planner 로 (재계획 상한과 worker_request 회차 상한 안에서)."""
    s = get_settings()
    if not state.get("worker_requests"):
        return "judge"
    if int(state.get("worker_request_rounds") or 0) > s.max_replans:
        return "judge"
    return "planner"
