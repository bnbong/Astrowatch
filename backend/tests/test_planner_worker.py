"""심화 F3021: Planner/Worker 구조. fake Tool + fake LLM, 네트워크와 키 없음."""

from __future__ import annotations

import json
from typing import Any

import pytest

from astrowatch.agent.llm import set_chat_model_override
from astrowatch.agent.service import run_turn
from astrowatch.agent.state import cache_key
from astrowatch.agent.worker import WorkerDecisionOut, WorkerRun
from astrowatch.config import reset_settings_cache
from astrowatch.events.bus import EventType
from astrowatch.schemas.domain import Conditions
from astrowatch.schemas.tasks import Task, TaskList, WorkerResult
from astrowatch.tools.base import ErrorCode, ToolKind, fail, ok
from astrowatch.tools.contracts import ObservatoryInput, ObservatoryOutput, Place

from test_graph import FLEX_MSG, R1_MSG, FakeLLM, _analysis, _stage_starts, events, llm, world  # noqa: F401

XYZ_MSG = "12월 14일 서울에서 존재하지않는유성우XYZ 볼 수 있어?"
XYZ_FIELDS = dict(date_from="2026-12-14", date_to="2026-12-14", start_time="22:00", end_time="02:00",
                  region="서울", event_kind="meteor", event_query="존재하지않는유성우XYZ")


class PWFake(FakeLLM):
    """analyze 는 test_graph 규칙(+XYZ), Worker 판단은 decide(payload) 가 정한다."""

    def __init__(self, decide=None) -> None:
        super().__init__()
        self.decide = decide
        self.worker_calls = 0
        self.payloads: list[dict[str, Any]] = []

    def with_structured_output(self, schema, **kw):
        outer = self
        if schema is WorkerDecisionOut:
            class _W:
                def invoke(self, messages):
                    outer.worker_calls += 1
                    payload = json.loads(messages[-1][1])
                    outer.payloads.append(payload)
                    if outer.decide is None:
                        raise AssertionError("Worker LLM 호출 금지")
                    return outer.decide(payload)
            return _W()
        base = super().with_structured_output(schema, **kw)

        class _A:
            def invoke(self, messages):
                human = [m[1] for m in messages if m[0] == "human"][-1]
                if "XYZ" in human:
                    outer.structured_calls += 1
                    return schema(**_analysis("plan", **XYZ_FIELDS))
                return base.invoke(messages)
        return _A()


@pytest.fixture
def pw_llm():
    fake = PWFake()
    set_chat_model_override(fake)
    yield fake
    set_chat_model_override(None)


def _env(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()


def _of(evts, t):
    return [e for e in evts if e.type == t]


# ── 정해진 형식, 정상 경로 ───────────────────────────────────

def test_normal_path_formats_and_zero_worker_llm(world, pw_llm, events):
    world.forecast = "clear"                           # 모든 Tool 성공 = 정상 경로
    res = run_turn(None, R1_MSG)
    lists = _of(events, EventType.PLANNER_TASKS)
    assert len(lists) == 1
    tl = TaskList.model_validate(lists[0].payload)
    assert tl.trigger == "user" and tl.round == 0
    assert [t.task_id for t in tl.tasks] == ["r0-t1", "r0-t2", "r0-t3"]
    assert [t.tool for t in tl.tasks] == ["search_sky_events", "get_night_window", "get_sky_forecast"]
    assert tl.tasks[1].depends_on == ["r0-t1"] and tl.tasks[2].depends_on == ["r0-t2"]
    assert "date" in tl.tasks[1].fixed_args            # 단일 날짜 → 사용자 고정
    results = [WorkerResult.model_validate(e.payload) for e in _of(events, EventType.WORKER_RESULT)]
    assert [r.task_id for r in results] == ["r0-t1", "r0-t2", "r0-t3"]
    assert results[0].ok and results[0].attempts == 1 and not results[0].from_cache
    # 정상 경로: Worker LLM 0회, 판단 이벤트 없음
    assert pw_llm.worker_calls == 0 and _of(events, EventType.WORKER_DECISION) == []
    assert res.llm_calls == 1                          # analyze 1회뿐
    # 화면 단계 이름은 classic 과 같다 (plan, execute)
    assert _stage_starts(events, res.session_id) == ["analyze", "plan", "execute", "judge", "respond"]
    assert len(res.recommended) == 1
    assert res.audit is not None                       # 로깅 Agent 미연결이면 판정 불가로 남는다


def test_stored_turn_includes_audit(world, pw_llm):
    from astrowatch.storage.turns import get_session_detail

    res = run_turn(None, R1_MSG)
    detail = get_session_detail(res.session_id)
    saved = detail.turns[-1].response
    assert saved.audit is not None and saved.audit.status == res.audit.status
    assert saved.llm_calls == res.llm_calls


# ── LLM 을 부르지 않는 실패 ─────────────────────────────────

def test_fatal_auth_failure_no_llm(world, pw_llm, events):
    world.place_auth_fail = True
    res = run_turn(None, R1_MSG + " 천문대")
    assert pw_llm.worker_calls == 0
    dec = [e.payload for e in _of(events, EventType.WORKER_DECISION) if e.tool == "search_observatory"]
    assert len(dec) == 1 and dec[0]["source"] == "rule" and dec[0]["error_code"] == "AUTH"
    # 예보 범위 밖(OUT_OF_RANGE)도 치명적 실패: 규칙으로만
    assert all(e.payload["llm_attempted"] is False for e in _of(events, EventType.WORKER_DECISION))
    assert dec[0]["llm_attempted"] is False and dec[0]["decision"] == "skip"
    assert res.recommended                             # 장소 실패와 무관하게 계속


def _place_registry(fresh_registry, behave):
    calls: list[dict[str, Any]] = []

    @fresh_registry.tool(name="search_observatory", description="장소", kind=ToolKind.EXTERNAL,
                         input_model=ObservatoryInput, output_model=ObservatoryOutput, fixed_arg_names=("lat", "lon"))
    def _place(a: ObservatoryInput):
        calls.append(a.model_dump(mode="json"))
        return behave(a)

    return calls


def _place_state(args: dict[str, Any]) -> dict[str, Any]:
    tl = TaskList(round=0, trigger="user", tasks=[
        Task(task_id="r0-t1", tool="search_observatory", args=args, fixed_args=["lat", "lon"], purpose="관측지 검색")])
    return {"session_id": "s-pw", "conditions": Conditions(region="서울"), "task_list": tl.model_dump(mode="json"),
            "cache": {}, "calls": []}


ARGS = {"query": "없는관측소XYZ", "lat": 37.5665, "lon": 126.978, "radius_km": 20}


def _no_result_unless_observatory(a):
    if a.query == "천문대":
        return ok(ObservatoryOutput(query=a.query, radius_km=a.radius_km, places=[
            Place(place_id="1", name="테스트 천문대", address="서울", url="https://place.example/1", distance_m=500)]))
    return fail(ErrorCode.NO_RESULT, "검색 결과 없음")


def test_missing_required_args_no_llm_no_call(fresh_registry, fresh_bus, events, pw_llm):
    calls = _place_registry(fresh_registry, _no_result_unless_observatory)
    run = WorkerRun(_place_state({"query": "천문대", "lon": 126.978}))     # lat 누락
    run.run_all()
    assert calls == [] and pw_llm.worker_calls == 0
    assert _of(events, EventType.TOOL_CALL) == []
    r = run.results[0]
    assert not r.ok and r.error_code == "BAD_INPUT" and r.decision.source == "rule"
    dec = _of(events, EventType.WORKER_DECISION)[0].payload
    assert dec["llm_attempted"] is False and "필수 인자 누락" in dec["reason"]


def test_ambiguous_not_sent_to_llm(fresh_registry, fresh_bus, events, pw_llm):
    _place_registry(fresh_registry, lambda a: fail(ErrorCode.NOT_FOUND, "이름이 모호함. 후보: A, B"))
    run = WorkerRun(_place_state(dict(ARGS)))
    run.run_all()
    assert pw_llm.worker_calls == 0
    assert "AMBIGUOUS" in _of(events, EventType.WORKER_DECISION)[0].payload["reason"]


# ── NO_RESULT → LLM 이 인자를 고쳐 같은 Tool 재호출 ─────────

def test_no_result_llm_retry_same_tool_fixed_args_kept(fresh_registry, fresh_bus, events, pw_llm):
    calls = _place_registry(fresh_registry, _no_result_unless_observatory)
    pw_llm.decide = lambda p: WorkerDecisionOut(
        decision="retry", new_args_json=json.dumps({**p["args"], "query": "천문대"}), reason="검색어를 일반어로")
    run = WorkerRun(_place_state(dict(ARGS)))
    run.run_all()
    assert pw_llm.worker_calls == 1
    assert [c["query"] for c in calls] == ["없는관측소XYZ", "천문대"]
    assert all(c["lat"] == ARGS["lat"] and c["lon"] == ARGS["lon"] for c in calls)   # 고정 인자 그대로
    assert {e.tool for e in _of(events, EventType.TOOL_CALL)} == {"search_observatory"}  # 같은 Tool 만
    r = run.results[0]
    assert r.ok and r.attempts == 2 and r.decision.kind.value == "retry" and r.decision.source == "llm"
    dec = _of(events, EventType.WORKER_DECISION)[0].payload
    assert dec["source"] == "llm" and dec["new_args"]["query"] == "천문대" and dec["model"] == "fake-llm"
    # 성공한 재시도 결과는 계획 인자 자리에서도 찾을 수 있다 (판정, 응답용)
    assert run.cache[cache_key("search_observatory", ARGS)].ok


def test_llm_cannot_change_fixed_arg(fresh_registry, fresh_bus, events, pw_llm):
    calls = _place_registry(fresh_registry, _no_result_unless_observatory)
    pw_llm.decide = lambda p: WorkerDecisionOut(
        decision="retry", new_args_json=json.dumps({**p["args"], "query": "천문대", "lat": 36.35}), reason="지역 이동")
    run = WorkerRun(_place_state(dict(ARGS)))
    run.run_all()
    assert len(calls) == 1                              # 다시 부르지 않음
    r = run.results[0]
    assert not r.ok and r.decision.kind.value == "skip" and "고정 인자" in r.decision.reason


def test_retry_bounded_by_worker_max_retry(fresh_registry, fresh_bus, events, pw_llm, monkeypatch):
    _env(monkeypatch, WORKER_MAX_RETRY="2")
    calls = _place_registry(fresh_registry, lambda a: fail(ErrorCode.NO_RESULT, "검색 결과 없음"))
    n = {"i": 0}

    def always_retry(p):
        n["i"] += 1
        return WorkerDecisionOut(decision="retry", new_args_json=json.dumps({**p["args"], "query": f"q{n['i']}"}),
                                 reason="다른 검색어")

    pw_llm.decide = always_retry
    run = WorkerRun(_place_state(dict(ARGS)))
    run.run_all()
    assert len(calls) == 3 and pw_llm.worker_calls == 2          # 원래 1회 + 재시도 2회
    r = run.results[0]
    assert r.attempts == 3 and not r.ok
    # 상한 뒤에는 규칙. 관측지 검색은 선택 작업이라 Planner 로 넘기지 않고 건너뜀 (R2)
    assert r.decision.source == "rule" and r.decision.kind.value == "skip" and r.replan_request is None


# ── Worker 요청 → Planner 재계획 ────────────────────────────

def test_worker_replan_request_reflected_by_planner_rule(world, pw_llm, events, monkeypatch):
    _env(monkeypatch, WORKER_LLM="0")
    res = run_turn(None, XYZ_MSG)
    assert pw_llm.worker_calls == 0
    dec = _of(events, EventType.WORKER_DECISION)[0].payload
    assert dec["source"] == "rule" and dec["decision"] == "replan_request" and dec["error_code"] == "NO_RESULT"
    triggers = [(e.payload["trigger"], e.payload["round"]) for e in _of(events, EventType.PLANNER_TASKS)]
    assert triggers[:2] == [("user", 0), ("worker_request", 1)]
    rep = _of(events, EventType.REPLAN)[0].payload
    assert rep["strategy"] == "other_events_same_period" and rep["trigger"] == "worker_request"
    assert res.replan_count >= 1
    # 재계획 뒤 같은 입력은 캐시로 재사용 (kind 를 푼 현상 조회만 새로 부른다)
    assert all(n == 1 for n in world.calls.values()), world.calls


def test_worker_llm_replan_request(world, pw_llm, events):
    pw_llm.decide = lambda p: WorkerDecisionOut(decision="replan_request", new_args_json=None,
                                                reason="지목한 현상이 목록에 없다")
    res = run_turn(None, XYZ_MSG)
    assert pw_llm.worker_calls == 1
    assert _of(events, EventType.WORKER_DECISION)[0].payload["source"] == "llm"
    assert [e.payload["trigger"] for e in _of(events, EventType.PLANNER_TASKS)][1] == "worker_request"
    assert res.llm_calls == 2                          # analyze 1 + Worker 1


# ── 로깅 Agent 이상 판정 → Planner 재구성 ───────────────────

def test_previous_audit_anomaly_recomposes_once(world, pw_llm, events):
    from astrowatch.agent.planner import planner
    from astrowatch.schemas.domain import Intent

    audit = {"status": "anomaly", "findings": [{"kind": "repeated_call", "message": "x"}]}
    base = {"session_id": "s-a", "intent": Intent.PLAN, "audit": audit,
            "conditions": Conditions(region="서울", date_from="2026-12-14", date_to="2026-12-14")}
    up = planner(base)
    triggers = [e.payload["trigger"] for e in _of(events, EventType.PLANNER_TASKS)]
    assert triggers == ["user", "audit_anomaly"] and up["audit_recomposed"] is True
    assert "repeated_call" in _of(events, EventType.PLANNER_TASKS)[1].payload["reason"]
    planner({**base, "audit_recomposed": True})        # 턴마다 한 번
    assert [e.payload["trigger"] for e in _of(events, EventType.PLANNER_TASKS)][2:] == ["user"]


def test_inturn_anomaly_recomposes_once(world, pw_llm, events):
    world.forecast = "cloudy"
    res = run_turn(None, FLEX_MSG)                     # 재계획 상한 도달 → 턴 중 검증 ANOMALY
    triggers = [e.payload["trigger"] for e in _of(events, EventType.PLANNER_TASKS)]
    assert triggers.count("audit_anomaly") == 1
    assert triggers[:4] == ["user", "judge_replan", "judge_replan", "judge_replan"]
    assert res.replan_count == 3 and all(n == 1 for n in world.calls.values())   # 재구성 뒤에도 같은 입력 재호출 없음


# ── classic 구조와 그래프 그림 ──────────────────────────────

def test_classic_structure_still_runs(world, pw_llm, events, monkeypatch):
    _env(monkeypatch, AGENT_STRUCTURE="classic")
    res = run_turn(None, R1_MSG)
    assert _of(events, EventType.PLANNER_TASKS) == [] and _of(events, EventType.WORKER_RESULT) == []
    tools = [s.tool for s in res.steps if s.type == "tool_call"]
    assert tools == ["search_sky_events", "get_night_window", "get_sky_forecast"]
    assert len(res.recommended) == 1


def test_graph_ascii_shows_active_structure(monkeypatch):
    from astrowatch.agent.graph import graph_ascii

    text = graph_ascii()
    assert "planner_worker" in text
    for node in ("planner", "worker", "judge", "verify", "respond"):
        assert node in text
    _env(monkeypatch, AGENT_STRUCTURE="classic")
    classic = graph_ascii()
    assert "classic" in classic and "execute" in classic and "verify" not in classic



# ── 리뷰 반영: 고정 날짜, 캐시 오염, 필수 작업 실패 전달 ────────

from astrowatch.agent import nodes as N  # noqa: E402
from astrowatch.agent.planner import PlannerRun, derive_fixed_args, to_tasks  # noqa: E402
from astrowatch.agent.worker import effective_fixed, route_after_worker, validate_retry  # noqa: E402
from astrowatch.schemas.domain import Intent  # noqa: E402
from astrowatch.tools.contracts import (  # noqa: E402
    NightWindowInput,
    NightWindowOutput,
    SkyForecastInput,
    SkyForecastOutput,
)

SEOUL = {"lat": 37.5665, "lon": 126.978}
D0, D1 = "2026-12-14", "2026-12-15"


def _forecast_registry(fresh_registry, behave):
    calls: list[dict[str, Any]] = []

    @fresh_registry.tool(name="get_sky_forecast", description="예보", kind=ToolKind.EXTERNAL,
                         input_model=SkyForecastInput, output_model=SkyForecastOutput, fixed_arg_names=("lat", "lon"))
    def _fc(a: SkyForecastInput):
        calls.append(a.model_dump(mode="json"))
        return behave(a)

    return calls


def _fc_ok(a):
    return ok(SkyForecastOutput(date=a.date, source="mock", hourly=[], coverage="none"))


def _single_task_state(task: Task, c: Conditions) -> dict[str, Any]:
    tl = TaskList(round=0, trigger="user", tasks=[task])
    return {"session_id": "s-pw", "conditions": c, "task_list": tl.model_dump(mode="json"), "cache": {}, "calls": []}


def test_derive_fixed_args_from_user_conditions():
    """R1: 사용자가 날짜, 지역을 고정하면 예보, 박명, 현상 검색 작업의 날짜와 좌표 인자가 고정된다."""
    c = Conditions(region="서울", date_from=D0, fixed=["date_from", "region"])
    fc = derive_fixed_args(N.T_FORECAST, {"date": N.EACH_DATE, **SEOUL, "hours": N.FROM_NIGHT}, c, [])
    assert set(fc) == {"date", "lat", "lon"} and "hours" not in fc
    assert set(derive_fixed_args(N.T_EVENTS, {"year": 2026, "month": 12}, c, [])) == {"year", "month"}
    # 고정하지 않았으면 계획이 준 것만
    assert derive_fixed_args(N.T_FORECAST, {"date": D0, **SEOUL}, Conditions(region="서울"), ["lat"]) == ["lat"]
    # 실제 계획(build_plan → to_tasks): 단일 날짜 고정 요청의 예보 작업도 date 고정
    c1 = Conditions(region="서울", date_from=D0, date_to=D0, fixed=["date_from", "region"])
    calls, _ = N.build_plan(Intent.PLAN, c1)
    tasks = {t.tool: t for t in to_tasks(calls, 0, c1)}
    assert {"date", "lat", "lon"} <= set(tasks[N.T_FORECAST].fixed_args)


def test_fixed_date_retry_to_other_date_rejected(fresh_registry, fresh_bus, events, pw_llm):
    """R1: 날짜를 고정한 예보 작업은 LLM 이 다른 날짜로 재시도하려 해도 거부된다 (다시 부르지 않음)."""
    calls = _forecast_registry(fresh_registry, lambda a: fail(ErrorCode.NO_RESULT, "예보 없음")
                               if a.date.isoformat() == D0 else _fc_ok(a))
    c = Conditions(region="서울", date_from=D0, fixed=["date_from"])
    args = {"date": D0, **SEOUL, "hours": [22, 23]}
    task = Task(task_id="r0-t1", tool=N.T_FORECAST, args=args, fixed_args=derive_fixed_args(N.T_FORECAST, args, c, []))
    pw_llm.decide = lambda p: WorkerDecisionOut(decision="retry", new_args_json=json.dumps({**p["args"], "date": D1}),
                                                reason="다음 날")
    run = WorkerRun(_single_task_state(task, c))
    run.run_all()
    assert [x["date"] for x in calls] == [D0]
    r = run.results[0]
    assert not r.ok and "고정 인자 변경 시도 거부(date)" in r.decision.reason
    assert cache_key(N.T_FORECAST, {**args, "date": D1}) not in run.cache


def test_candidate_date_expansion_date_always_fixed():
    """R1: 후보 날짜마다 펼치는 작업의 date 는 사용자 고정이 없어도 바꿀 수 없다 (결과를 찾는 열쇠)."""
    task = Task(task_id="r0-t3", tool=N.T_FORECAST, args={"date": N.EACH_DATE, **SEOUL, "hours": N.FROM_NIGHT})
    assert {"date", "lat", "lon"} <= set(effective_fixed(task))
    concrete = {"date": D0, **SEOUL, "hours": [22]}
    new, why = validate_retry(task, N.T_FORECAST, concrete, {**concrete, "date": D1})
    assert new is None and "date" in why
    new, why = validate_retry(task, N.T_FORECAST, concrete, {**concrete, "hours": [23]})
    assert new is not None and new["hours"] == [23]


def test_retry_with_changed_hours_caches_under_plan_key(fresh_registry, fresh_bus, events, pw_llm):
    """R1: hours 만 바꾼 성공 결과는 같은 날짜의 것이라 계획 인자 자리에도 둔다."""
    _forecast_registry(fresh_registry, lambda a: _fc_ok(a) if a.hours == [22] else fail(ErrorCode.NO_RESULT, "예보 없음"))
    c = Conditions(region="서울", date_from=D0, fixed=["date_from"])
    args = {"date": D0, **SEOUL, "hours": [22, 23]}
    task = Task(task_id="r0-t1", tool=N.T_FORECAST, args=args, fixed_args=derive_fixed_args(N.T_FORECAST, args, c, []))
    pw_llm.decide = lambda p: WorkerDecisionOut(decision="retry", new_args_json=json.dumps({**p["args"], "hours": [22]}),
                                                reason="시각 줄이기")
    run = WorkerRun(_single_task_state(task, c))
    run.run_all()
    r = run.results[0]
    assert r.ok and r.args_changed == ["hours"] and r.args_used["hours"] == [22]
    cached = run.cache[cache_key(N.T_FORECAST, args)]
    assert cached.ok and cached.data.date.isoformat() == D0


def test_retry_with_changed_date_never_pollutes_plan_key(fresh_registry, fresh_bus, events, pw_llm):
    """R1: 날짜가 바뀐 재시도 결과(고정이 아닌 경우)는 실제 인자 키에만 두고, 원래 날짜 자리에 두지 않는다."""
    _forecast_registry(fresh_registry, lambda a: fail(ErrorCode.NO_RESULT, "예보 없음")
                       if a.date.isoformat() == D0 else _fc_ok(a))
    args = {"date": D0, **SEOUL, "hours": [22]}
    task = Task(task_id="r0-t1", tool=N.T_FORECAST, args=args, fixed_args=[])
    pw_llm.decide = lambda p: WorkerDecisionOut(decision="retry", new_args_json=json.dumps({**p["args"], "date": D1}),
                                                reason="다음 날")
    run = WorkerRun(_single_task_state(task, Conditions(region="서울")))
    run.run_all()
    r = run.results[0]
    assert r.ok and r.args_used["date"] == D1 and r.args_changed == ["date"]
    assert run.cache[cache_key(N.T_FORECAST, {**args, "date": D1})].ok
    assert not run.cache[cache_key(N.T_FORECAST, args)].ok          # 원래 날짜 자리는 원래 실패 그대로


def test_required_night_window_bad_input_reaches_planner(fresh_registry, fresh_bus, events):
    """R2: 필수 작업(박명)의 필수 인자 누락은 건너뛰지 않고 Planner 요청이 된다."""
    @fresh_registry.tool(name="get_night_window", description="박명", kind=ToolKind.EXTERNAL,
                         input_model=NightWindowInput, output_model=NightWindowOutput, fixed_arg_names=("lat", "lon"))
    def _night(a):  # pragma: no cover
        raise AssertionError("호출 전 검증에서 막혀야 한다")

    task = Task(task_id="r0-t2", tool=N.T_NIGHT, args={"date": D0, "lon": SEOUL["lon"]})   # lat 누락
    state = _single_task_state(task, Conditions(region="서울", date_from=D0))
    run = WorkerRun(state)
    run.run_all()
    r = run.results[0]
    assert r.error_code == "BAD_INPUT" and r.decision.kind.value == "replan_request" and r.replan_request
    [req] = run.requests
    assert req["task_id"] == "r0-t2" and req["tool"] == N.T_NIGHT and req["error_code"] == "BAD_INPUT"
    assert "필수 인자 누락" in req["reason"]
    state = {**state, **run.update()}
    assert route_after_worker(state) == "planner"
    pr = PlannerRun(state)
    text = pr.worker_request()
    assert text.startswith("[Worker 요청]") and pr.update["planner_next"] == "judge"   # 전략 없음 → 판정 (stop 경로)


def test_optional_observatory_no_result_is_skip(fresh_registry, fresh_bus, events):
    """R2: 선택 작업(관측지)의 결과 없음은 Planner 로 넘기지 않고 건너뛴다."""
    _place_registry(fresh_registry, lambda a: fail(ErrorCode.NO_RESULT, "검색 결과 없음"))
    run = WorkerRun(_place_state(dict(ARGS)))
    run.run_all()
    r = run.results[0]
    assert not r.ok and r.decision.kind.value == "skip" and r.replan_request is None and run.requests == []


def test_required_task_fatal_auth_stays_skip(fresh_registry, fresh_bus, events):
    """R2: 치명적 실패(AUTH)는 필수 작업이라도 Planner 로 넘기지 않는다 (판정의 fatal_error 경로 유지)."""
    _forecast_registry(fresh_registry, lambda a: fail(ErrorCode.AUTH, "키 없음"))
    task = Task(task_id="r0-t1", tool=N.T_FORECAST, args={"date": D0, **SEOUL, "hours": [22]})
    run = WorkerRun(_single_task_state(task, Conditions(region="서울")))
    run.run_all()
    assert run.results[0].decision.kind.value == "skip" and run.requests == []


def test_audit_worker_retry_not_unplanned_but_unplanned_tool_is(fresh_registry, fresh_bus, events, pw_llm):
    """통합: 인자를 고친 Worker 재시도(같은 Tool)는 UNPLANNED_CALL 이 아니고, 계획에 없던 Tool 호출은 그렇다."""
    from astrowatch.logging_agent.audit import audit_events
    from astrowatch.schemas.audit import FindingKind

    _place_registry(fresh_registry, _no_result_unless_observatory)
    pw_llm.decide = lambda p: WorkerDecisionOut(
        decision="retry", new_args_json=json.dumps({**p["args"], "query": "천문대"}), reason="검색어를 일반어로")
    state = _place_state(dict(ARGS))
    fresh_bus.publish("s-pw", EventType.SESSION_START, payload={"message": "관측지"})
    fresh_bus.publish("s-pw", EventType.PLANNER_TASKS, stage="plan", payload=state["task_list"])
    WorkerRun(state).run_all()
    assert len(_of(events, EventType.TOOL_CALL)) == 2                # 원래 + 재시도
    retry_only = audit_events("s-pw", list(events))
    assert FindingKind.UNPLANNED_CALL not in [f.kind for f in retry_only.findings]
    fresh_bus.publish("s-pw", EventType.TOOL_CALL, stage="execute", tool="get_calendar",
                      payload={"args": {"date_from": D0, "date_to": D1}})
    with_extra = audit_events("s-pw", list(events))
    unplanned = [f for f in with_extra.findings if f.kind == FindingKind.UNPLANNED_CALL]
    assert len(unplanned) == 1 and "get_calendar" in unplanned[0].message
