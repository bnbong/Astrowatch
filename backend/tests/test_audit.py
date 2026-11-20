"""심화 F3020: 로깅 Agent 이상 징후 판정: 규칙 엔진, 진단, 저장, API, run_turn 연결. 네트워크와 키 없음."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from astrowatch.agent import llm as llm_mod
from astrowatch.events.bus import EventType
from astrowatch.logging_agent import diagnosis as diag_mod
from astrowatch.logging_agent.audit import audit_events, audit_session, audit_turn, turn_notes
from astrowatch.logging_agent.diagnosis import DiagnosisOut, diagnose
from astrowatch.schemas.api import LogEventOut
from astrowatch.schemas.audit import AuditDiagnosis, AuditStatus, FindingKind
from astrowatch.storage import run_audits

NOW = datetime(2026, 11, 20, 12, 0)
SID = "sess-audit-1"
EV = "search_sky_events"
NIGHT = "get_night_window"


def ev(seq, type_, stage=None, tool=None, **payload):
    return LogEventOut(session_id=SID, seq=seq, ts=NOW, type=type_, stage=stage, tool=tool, payload=payload)


def plan_end(seq, tools):
    return ev(seq, "stage_end", "plan", planned_tools=tools, summary="계획")


def call(seq, tool, **args):
    return ev(seq, "tool_call", "execute", tool, args=args)


def normal_turn(start=1):
    s = start
    return [
        ev(s, "session_start", message="쌍둥이자리 유성우"),
        plan_end(s + 1, [EV, NIGHT]),
        call(s + 2, EV, year=2026, month=12),
        ev(s + 3, "tool_result", "execute", EV, ok=True),
        call(s + 4, NIGHT, date="2026-12-14"),
        ev(s + 5, "tool_result", "execute", NIGHT, ok=True),
        ev(s + 6, "session_end", replan_count=0),
    ]


# ── 규칙 엔진 ────────────────────────────────────────────────

def test_normal_run():
    r = audit_events(SID, normal_turn())
    assert r.status == AuditStatus.NORMAL and r.findings == [] and r.warning_count == 0
    assert r.evidence_count == 7 and (r.seq_from, r.seq_to) == (1, 7)


def test_repeated_same_input():
    evs = [ev(1, "session_start"), plan_end(2, [EV])] + [call(3 + i, EV, month=12, year=2026) for i in range(3)]
    evs.append(call(6, EV, year=2026, month=11))       # 다른 입력은 별개
    r = audit_events(SID, evs)
    assert r.status == AuditStatus.ANOMALY
    [f] = r.findings
    assert f.kind == FindingKind.REPEATED_CALL and f.count == 3 and f.evidence_seqs == [3, 4, 5]


def test_cache_reuse_not_counted():
    # 캐시 재사용은 nodes.execute 가 tool_call 을 발행하지 않는다. 표시가 붙은 tool_call 도 세지 않는다.
    evs = [ev(1, "session_start"), plan_end(2, [EV]), call(3, EV, year=2026),
           ev(4, "tool_call", "execute", EV, args={"year": 2026}, from_cache=True),
           ev(5, "worker_result", tool=EV, from_cache=True, args_used={"year": 2026})]
    assert audit_events(SID, evs).status == AuditStatus.NORMAL


def test_same_input_in_different_turns_is_not_repeat():
    evs = normal_turn(1) + normal_turn(8)
    assert audit_events(SID, evs).status == AuditStatus.NORMAL


def test_excessive_replan_threshold_from_settings(monkeypatch):
    from astrowatch.config import reset_settings_cache

    evs = [ev(1, "session_start"), plan_end(2, [EV]),
           ev(3, "replan", "judge", round=1, limit_reached=False),
           ev(4, "replan", "judge", round=2, limit_reached=False)]
    assert audit_events(SID, evs).status == AuditStatus.NORMAL      # 기본 기준 3
    monkeypatch.setenv("AUDIT_REPLAN_THRESHOLD", "2")
    reset_settings_cache()
    r = audit_events(SID, evs)
    assert r.status == AuditStatus.ANOMALY
    assert r.findings[0].kind == FindingKind.EXCESSIVE_REPLAN and r.findings[0].evidence_seqs == [3, 4]
    assert audit_events(SID, evs, replan_threshold=5).status == AuditStatus.NORMAL


def test_replan_limit_reached_flags():
    evs = [ev(1, "session_start"), ev(2, "replan", "judge", round=1, limit_reached=True)]
    r = audit_events(SID, evs)
    assert r.status == AuditStatus.ANOMALY and "상한" in r.findings[0].message


def test_unplanned_call_from_plan_stage_end():
    evs = [ev(1, "session_start"), plan_end(2, [EV]), call(3, EV, year=2026), call(4, "get_calendar", date="x")]
    r = audit_events(SID, evs)
    [f] = r.findings
    assert f.kind == FindingKind.UNPLANNED_CALL and f.evidence_seqs == [4]


def test_unplanned_uses_planner_tasks_when_present():
    tasks = {"round": 0, "tasks": [{"task_id": "r0-t1", "tool": NIGHT, "args": {}, "fixed_args": []}]}
    evs = [ev(1, "session_start"), ev(2, "planner_tasks", "plan", **tasks),
           plan_end(3, [EV, NIGHT]), call(4, NIGHT, date="d"), call(5, EV, year=2026)]
    r = audit_events(SID, evs)
    assert [f.kind for f in r.findings] == [FindingKind.UNPLANNED_CALL] and r.findings[0].evidence_seqs == [5]


def test_replan_plan_extends_planned_set():
    evs = [ev(1, "session_start"), plan_end(2, [EV]), call(3, EV, year=2026),
           ev(4, "replan", "judge", round=1, limit_reached=False), plan_end(5, [EV, NIGHT]), call(6, NIGHT, date="d")]
    assert audit_events(SID, evs).status == AuditStatus.NORMAL


def test_no_plan_evidence_gives_no_unplanned_finding_but_note():
    evs = [ev(1, "session_start"), call(2, EV, year=2026)]
    assert audit_events(SID, evs).status == AuditStatus.NORMAL
    assert turn_notes(evs) and "계획 이벤트가 없어" in turn_notes(evs)[0]


@pytest.mark.parametrize("evs", [[], [ev(1, "session_start"), ev(2, "session_end")],
                                 [ev(1, "session_start"), ev(2, "session_end"), ev(3, "audit_result", status="normal")]])
def test_undetermined_without_evidence(evs):
    r = audit_events(SID, evs)
    assert r.status == AuditStatus.UNDETERMINED and r.findings == []


def test_seq_range_and_audit_events_ignored():
    evs = normal_turn(1) + [ev(8, "audit_result", status="normal")] + [ev(9, "session_start"), ev(10, "session_end")]
    assert audit_events(SID, evs, seq_from=9, seq_to=10).status == AuditStatus.UNDETERMINED
    r = audit_events(SID, evs, seq_from=1, seq_to=7)
    assert r.status == AuditStatus.NORMAL and r.evidence_count == 7


# ── 진단 ────────────────────────────────────────────────────

def _anomaly():
    evs = [ev(1, "session_start"), plan_end(2, [EV]), call(3, EV, year=2026), call(4, EV, year=2026)]
    return audit_events(SID, evs), evs


class _FakeDiagLLM:
    model_name = "fake-diag"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def with_structured_output(self, schema, **_kw):
        outer = self

        class _R:
            def invoke(self, messages):
                outer.calls += 1
                if outer.fail:
                    raise RuntimeError("boom")
                return DiagnosisOut(culprit="worker", fix="캐시 키를 확인한다",
                                    text="Worker 가 같은 입력을 두 번 불렀다. 근거는 seq 3, 4 이다. 캐시를 먼저 본다. 네 번째 문장.")

        return _R()


@pytest.fixture
def fake_llm():
    holder = SimpleNamespace(llm=None)

    def use(m):
        holder.llm = m
        llm_mod.set_chat_model_override(m)
        return m

    yield use
    llm_mod.set_chat_model_override(None)


def test_no_llm_on_normal_or_undetermined(monkeypatch, fake_llm):
    fake_llm(_FakeDiagLLM())

    def boom():
        raise AssertionError("LLM 을 부르면 안 된다")

    monkeypatch.setattr(llm_mod, "get_chat_model", boom)
    assert diagnose(audit_events(SID, normal_turn()), normal_turn()) is None
    assert diagnose(audit_events(SID, []), []) is None


def test_diagnosis_on_anomaly_three_sentences(fake_llm):
    m = fake_llm(_FakeDiagLLM())
    result, evs = _anomaly()
    d = diagnose(result, evs)
    assert m.calls == 1 and d.culprit == "worker" and not d.failed and d.model == "fake-diag"
    assert d.text.count(".") == 3 and "네 번째" not in d.text
    assert result.warning_count == 1              # 진단은 경고 수에 섞이지 않는다


def test_diagnosis_off_by_setting(monkeypatch, fake_llm):
    from astrowatch.config import reset_settings_cache

    m = fake_llm(_FakeDiagLLM())
    monkeypatch.setenv("AUDIT_LLM", "0")
    reset_settings_cache()
    assert diagnose(*_anomaly()) is None and m.calls == 0


def test_diagnosis_failure_keeps_result(fake_llm):
    fake_llm(_FakeDiagLLM(fail=True))
    result, evs = _anomaly()
    before = result.model_dump()
    d = diagnose(result, evs)
    assert d.failed and d.text.startswith("진단 실패")
    assert result.model_dump() == before and result.status == AuditStatus.ANOMALY


def test_no_llm_configured_returns_none():
    assert diagnose(*_anomaly()) is None          # GMS_KEY 없음 → 호출 없음


# ── 저장소 ──────────────────────────────────────────────────

def test_storage_roundtrip_and_upsert():
    result, _ = _anomaly()
    run_audits.save_audit(result)
    diag = AuditDiagnosis(culprit="planner", fix="f", text="t")
    run_audits.save_audit(result.model_copy(update={"diagnosis": diag}))   # 같은 구간 → 덮어쓰기
    rows = run_audits.list_audits(SID)
    assert len(rows) == 1 and rows[0].diagnosis.culprit == "planner"
    assert rows[0].findings == result.findings and rows[0].status == AuditStatus.ANOMALY
    wide = result.model_copy(update={"seq_from": 1, "seq_to": 4, "status": AuditStatus.NORMAL, "findings": []})
    run_audits.save_audit(wide.model_copy(update={"seq_from": 0}))
    assert run_audits.get_latest(SID).seq_from == 0              # 같은 끝이면 넓은 구간
    assert run_audits.latest_by_session()[SID].seq_from == 0
    assert run_audits.get_latest("nope-session") is None


def test_audit_turn_and_session_from_run_logs(fresh_bus):
    from astrowatch.logging_agent.agent import LoggingAgent

    agent = LoggingAgent()
    agent.attach(fresh_bus)
    try:
        for e in normal_turn() + [ev(8, "session_start"), plan_end(9, [EV]), call(10, "get_calendar")]:
            fresh_bus.publish(SID, EventType(e.type), stage=e.stage, tool=e.tool, payload=e.payload)
    finally:
        agent.detach()
    assert audit_turn(SID, 1, 7).status == AuditStatus.NORMAL
    r = audit_session(SID)
    assert r.status == AuditStatus.ANOMALY and r.findings[0].evidence_seqs == [10]
    assert audit_session("no-such-sess").status == AuditStatus.UNDETERMINED


# ── API ─────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from astrowatch.app import main
    from astrowatch.app.api import logs as logs_api

    store = SimpleNamespace(events={SID: _anomaly()[1]})
    store.get_session_events = lambda sid: list(store.events.get(sid, []))
    store.list_sessions = lambda: []
    store.delete_session = lambda sid: store.events.pop(sid, None) is not None
    monkeypatch.setattr(logs_api, "_store", lambda: store)
    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    with TestClient(main.create_app(), raise_server_exceptions=False, headers={"X-Requested-With": "Astrowatch"}) as c:
        yield c


def test_api_audit_endpoints(client):
    assert client.get(f"/api/logs/{SID}/audit").status_code == 404
    assert client.get(f"/api/logs/{SID}/audits").json() == []
    r = client.post(f"/api/logs/{SID}/audit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "anomaly" and body["findings"][0]["kind"] == "repeated_call"
    assert body["diagnosis"] is None                   # LLM 미설정
    assert client.get(f"/api/logs/{SID}/audit").json()["seq_to"] == 4
    assert len(client.get(f"/api/logs/{SID}/audits").json()) == 1
    assert client.post("/api/logs/unknown-sess/audit").status_code == 404
    assert client.post(f"/api/logs/{SID}/audit", headers={"X-Requested-With": ""}).status_code == 403


def test_api_session_list_badge(client, monkeypatch):
    from astrowatch.app.api import logs as logs_api
    from astrowatch.schemas.api import LogSessionOut

    client.post(f"/api/logs/{SID}/audit")
    store = logs_api._store()
    store.list_sessions = lambda: [LogSessionOut(session_id=SID, started_at=NOW, last_at=NOW, event_count=4,
                                                 tool_calls=2, replans=0),
                                   LogSessionOut(session_id="other-sess", started_at=NOW, last_at=NOW, event_count=1,
                                                 tool_calls=0, replans=0)]
    rows = {s["session_id"]: s for s in client.get("/api/logs").json()}
    assert rows[SID]["audit_status"] == "anomaly" and rows[SID]["audit_warnings"] == 1
    assert rows["other-sess"]["audit_status"] is None


def test_api_audit_rate_limited(client, monkeypatch):
    """R6: 지금 판정(POST) 은 LLM 진단을 부를 수 있어 IP 당 분당 AUDIT_RATE_PER_MIN 회로 제한한다."""
    from astrowatch.config import reset_settings_cache

    monkeypatch.setenv("AUDIT_RATE_PER_MIN", "2")
    reset_settings_cache()
    assert client.post(f"/api/logs/{SID}/audit").status_code == 200
    assert client.post(f"/api/logs/{SID}/audit").status_code == 200
    r = client.post(f"/api/logs/{SID}/audit")
    assert r.status_code == 429 and "retry-after" in r.headers
    assert client.get(f"/api/logs/{SID}/audit").status_code == 200      # 조회는 제한 없음
    assert client.post("/api/chat", json={"message": ""}).status_code != 429   # 대화 버킷과 따로


def test_api_audit_reuses_stored_diagnosis(client, fake_llm):
    """R6: 같은 구간에 성공한 진단이 있으면 POST 를 반복해도 LLM 을 다시 부르지 않는다. force=1 이면 다시."""
    m = fake_llm(_FakeDiagLLM())
    first = client.post(f"/api/logs/{SID}/audit").json()
    assert m.calls == 1 and first["diagnosis"]["culprit"] == "worker"
    again = client.post(f"/api/logs/{SID}/audit").json()
    assert m.calls == 1 and again["diagnosis"] == first["diagnosis"]
    client.post(f"/api/logs/{SID}/audit", params={"force": 1})
    assert m.calls == 2


def test_api_audit_retries_failed_diagnosis(client, fake_llm):
    m = fake_llm(_FakeDiagLLM(fail=True))
    assert client.post(f"/api/logs/{SID}/audit").json()["diagnosis"]["failed"] is True
    before = m.calls
    client.post(f"/api/logs/{SID}/audit")
    assert m.calls > before                            # 실패한 진단은 재사용하지 않는다


# ── run_turn 연결 ───────────────────────────────────────────

def test_run_turn_attaches_audit_and_passes_previous_to_state(fresh_bus, monkeypatch):
    from astrowatch.agent import memory, service
    from astrowatch.logging_agent.agent import LoggingAgent
    from astrowatch.storage.run_logs import get_session_events

    memory.reset_memory()
    agent = LoggingAgent()
    agent.attach(fresh_bus)
    seen: list = []
    real_build = service.build_graph

    def spy_build():
        g = real_build()

        class _G:
            def invoke(self, init, **kw):
                seen.append(init.get("audit"))
                return g.invoke(init, **kw)

        return _G()

    monkeypatch.setattr(service, "build_graph", spy_build)
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: (_ for _ in ()).throw(AssertionError("LLM 호출 금지")))
    try:
        r1 = service.run_turn(None, "안녕")
        assert r1.audit is not None and r1.audit.status in ("normal", "undetermined")
        assert r1.audit.diagnosis_text is None
        r2 = service.run_turn(r1.session_id, "안녕")
    finally:
        agent.detach()
    assert seen[0] is None                              # 첫 턴엔 직전 판정 없음
    assert seen[1] is not None and seen[1]["status"] == r1.audit.status   # 메인 Agent 가 직전 판정을 참조
    assert r2.audit is not None
    types = [e.type for e in get_session_events(r1.session_id)]
    assert types.count("audit_result") == 2 and "audit_diagnosis" not in types
    end = types.index("session_end")
    assert types[end + 1] == "audit_result"            # 판정은 턴이 끝난 뒤 기록된다


def test_run_turn_survives_audit_failure(fresh_bus, monkeypatch):
    from astrowatch.agent import memory, service
    from astrowatch.logging_agent import audit as audit_mod

    memory.reset_memory()

    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_mod, "audit_events", boom)
    res = service.run_turn(None, "안녕")
    assert res.answer and res.audit is None


# ── 실제 그래프 실행 (test_graph 의 fake Tool, fake LLM) ─────

from test_graph import FLEX_MSG, R1_MSG, events, llm, world  # noqa: E402,F401  (fixture 재사용)


def _attach_logging(bus):
    from astrowatch.logging_agent.agent import LoggingAgent

    agent = LoggingAgent()
    agent.attach(bus)
    return agent


def test_real_graph_normal_turn_is_normal(world, llm, events, fresh_bus):
    from astrowatch.agent.service import run_turn
    from astrowatch.storage.run_logs import get_session_events

    agent = _attach_logging(fresh_bus)
    try:
        res = run_turn(None, R1_MSG)
    finally:
        agent.detach()
    plan_ends = [e for e in get_session_events(res.session_id) if e.type == "stage_end" and e.stage == "plan"]
    assert plan_ends and plan_ends[0].payload["planned_tools"][0] == "search_sky_events"
    assert res.audit.status == "normal" and res.audit.warning_count == 0


def test_real_graph_replan_with_cache_reuse_not_repeated(world, llm, events, fresh_bus):
    from astrowatch.agent.service import run_turn

    world.forecast = "cloudy_peak"
    agent = _attach_logging(fresh_bus)
    try:
        res = run_turn(None, FLEX_MSG)
    finally:
        agent.detach()
    assert res.replan_count == 1
    assert res.audit.status == "normal"          # 재계획에서 캐시 재사용한 호출은 반복으로 세지 않는다


def test_real_graph_replan_limit_is_anomaly_and_failed_diagnosis_kept(world, llm, events, fresh_bus):
    from astrowatch.agent.service import run_turn
    from astrowatch.storage.run_logs import get_session_events

    world.forecast = "cloudy"
    agent = _attach_logging(fresh_bus)
    try:
        res = run_turn(None, FLEX_MSG)
    finally:
        agent.detach()
    assert res.replan_count == 3 and res.answer
    assert res.audit.status == "anomaly"
    assert [f.kind for f in res.audit.findings] == ["excessive_replan"] and res.audit.warning_count == 1
    # fake LLM 은 진단 스키마를 못 만든다 → 진단 실패로 남고, 판정과 로그는 그대로
    assert res.audit.diagnosis_text.startswith("진단 실패")
    types = [e.type for e in get_session_events(res.session_id)]
    assert types[-2:] == ["audit_result", "audit_diagnosis"]
    saved = run_audits.get_latest(res.session_id)
    assert saved.status == AuditStatus.ANOMALY and saved.diagnosis.failed


def test_stored_turn_update_failure_logs_warning(monkeypatch, caplog):
    """판정 뒤 저장 턴 갱신이 실패하거나(예외) 바뀐 행이 없으면 경고만 남기고 막지 않는다."""
    import logging

    from astrowatch.agent import service
    from astrowatch.schemas.api import ChatResponse
    from astrowatch.schemas.domain import Conditions, Intent
    from astrowatch.storage import turns

    resp = ChatResponse(session_id="sess-0001", intent=Intent.CHAT, answer="ok", conditions=Conditions())
    monkeypatch.setattr(turns, "update_turn_response", lambda sid, seq, r: False)
    with caplog.at_level(logging.WARNING):
        service._update_turn("sess-0001", 3, resp)
    assert any("판정 갱신 실패" in r.getMessage() and "seq=3" in r.getMessage() for r in caplog.records)

    def boom(sid, seq, r):
        raise RuntimeError("db")

    monkeypatch.setattr(turns, "update_turn_response", boom)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        service._update_turn("sess-0001", 3, resp)          # 예외를 밖으로 내지 않는다
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
