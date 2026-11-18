"""백엔드 API (F3017) — Agent · 저장소는 가짜로 바꿔 네트워크 · 키 없이 검사한다."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from astrowatch.app.api import chat as chat_api
from astrowatch.app.api import logs as logs_api
from astrowatch.app.api import meta as meta_api
from astrowatch.app.api import plans as plans_api
from astrowatch.schemas.api import ChatResponse, LogEventOut, LogSessionOut, StepOut
from astrowatch.schemas.domain import Candidate, Conditions, Intent, JudgedCandidate, Verdict, Window
from astrowatch.schemas.plan import PlanCreate, PlanOut, PlanSaveResult, PlanUpdate
from astrowatch.tools.contracts import EventKind

NOW = datetime(2026, 11, 18, 12, 0)


# ── 가짜 저장소 ───────────────────────────────────────────────

class FakePlans:
    def __init__(self) -> None:
        self.items: dict[int, PlanOut] = {}
        self.next_id = 1

    def create_plan(self, body: PlanCreate) -> PlanSaveResult:
        overlaps = [p for p in self.items.values() if p.start_dt < body.end_dt and body.start_dt < p.end_dt]
        plan = PlanOut(id=self.next_id, created_at=NOW, updated_at=NOW, **body.model_dump())
        self.items[plan.id] = plan
        self.next_id += 1
        return PlanSaveResult(plan=plan, overlaps=overlaps)

    def list_plans(self) -> list[PlanOut]:
        return list(self.items.values())

    def get_plan(self, plan_id: int) -> PlanOut | None:
        return self.items.get(plan_id)

    def update_plan(self, plan_id: int, body: PlanUpdate) -> PlanOut | None:
        plan = self.items.get(plan_id)
        if plan is None:
            return None
        data = plan.model_dump()
        data.update(body.model_dump(exclude_unset=True, exclude_none=True))
        if data["end_dt"] <= data["start_dt"]:
            raise ValueError("end_dt 는 start_dt 보다 뒤여야 한다")
        updated = PlanOut.model_validate(data)
        self.items[plan_id] = updated
        return updated

    def delete_plan(self, plan_id: int) -> bool:
        return self.items.pop(plan_id, None) is not None


class FakeRunLogs:
    def __init__(self) -> None:
        self.events = {
            "sess-001": [
                LogEventOut(session_id="sess-001", seq=2, ts=NOW, type="stage_start", stage="analyze"),
                LogEventOut(session_id="sess-001", seq=1, ts=NOW, type="session_start", payload={"message": "안녕"}),
            ]
        }

    def list_sessions(self) -> list[LogSessionOut]:
        return [
            LogSessionOut(
                session_id=sid, started_at=NOW, last_at=NOW, event_count=len(ev),
                tool_calls=0, replans=0, first_message="안녕",
            )
            for sid, ev in self.events.items()
        ]

    def get_session_events(self, session_id: str) -> list[LogEventOut]:
        return list(self.events.get(session_id, []))

    def delete_session(self, session_id: str) -> bool:
        return self.events.pop(session_id, None) is not None


def _fake_chat_response(session_id: str | None, message: str) -> ChatResponse:
    cand = Candidate(
        event_id="e1", title="쌍둥이자리 유성우 극대", kind=EventKind.METEOR,
        date=date(2026, 12, 14), region="서울", lat=37.56, lon=126.97,
    )
    judged = JudgedCandidate(
        candidate=cand, verdict=Verdict.FIT, time_window=Window(start=1330, end=1530),
        clear_minutes=200, moon_interferes=False, reasons=["맑음 3시간 20분"],
    )
    return ChatResponse(
        session_id=session_id or "new-session",
        intent=Intent.PLAN,
        answer=f"응답: {message}",
        conditions=Conditions(region="서울"),
        recommended=[judged],
        steps=[StepOut(seq=1, type="stage_start", stage="analyze", summary="의도 분석")],
    )


# ── fixture ──────────────────────────────────────────────────

@pytest.fixture
def fakes(monkeypatch):
    store = SimpleNamespace(plans=FakePlans(), logs=FakeRunLogs())
    monkeypatch.setattr(plans_api, "_store", lambda: store.plans)
    monkeypatch.setattr(logs_api, "_store", lambda: store.logs)
    monkeypatch.setattr(chat_api, "_run_turn", _fake_chat_response)
    monkeypatch.setattr(meta_api, "_graph_ascii", lambda: "analyze -> respond")
    return store


@pytest.fixture
def client(fakes, monkeypatch):
    from astrowatch.app import main

    # 실제 Tool · 로깅 Agent 로드는 이 테스트 범위 밖
    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    with TestClient(main.create_app(), raise_server_exceptions=False, headers={"X-Requested-With": "Astrowatch"}) as c:
        yield c


def _plan_body(**over):
    body = {
        "event_title": "쌍둥이자리 유성우 극대",
        "event_kind": "meteor",
        "start_dt": "2026-12-14T22:10:00",
        "end_dt": "2026-12-15T01:30:00",
        "location_label": "서울",
        "lat": 37.56,
        "lon": 126.97,
        "basis": "충족 · 맑음 200분",
    }
    body.update(over)
    return body


# ── health · meta ────────────────────────────────────────────

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert isinstance(data["llm_configured"], bool)
    assert isinstance(data["tools"], list)


def test_tools_is_list(client):
    r = client.get("/api/tools")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_graph(client):
    r = client.get("/api/graph")
    assert r.status_code == 200
    assert r.json() == {"ascii": "analyze -> respond"}


def test_graph_import_failure_is_503(client, monkeypatch):
    def boom():
        raise ImportError("no module")

    monkeypatch.setattr(meta_api, "_graph_ascii", boom)
    r = client.get("/api/graph")
    assert r.status_code == 503


# ── chat ─────────────────────────────────────────────────────

def test_chat_ok(client):
    r = client.post("/api/chat", json={"message": "12월 14일 서울 유성우"})
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "new-session"
    assert data["answer"].startswith("응답:")
    assert data["recommended"][0]["verdict"] == "fit"
    assert data["steps"][0]["type"] == "stage_start"


def test_chat_keeps_session(client):
    r = client.post("/api/chat", json={"session_id": "abc", "message": "내일은?"})
    assert r.json()["session_id"] == "abc"


def test_chat_empty_message_is_422(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_chat_whitespace_only_message_is_422(client, monkeypatch):
    called = []
    monkeypatch.setattr(chat_api, "_run_turn", lambda sid, msg: called.append(msg))
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert called == []  # 공백만이면 Agent(LLM) 를 부르지 않는다


def test_chat_message_is_trimmed_before_run_turn(client, monkeypatch):
    seen = []

    def spy(session_id, message):
        seen.append((session_id, message))
        return _fake_chat_response(session_id, message)

    monkeypatch.setattr(chat_api, "_run_turn", spy)
    r = client.post("/api/chat", json={"session_id": None, "message": "  서울 유성우 \n "})
    assert r.status_code == 200
    assert seen == [(None, "서울 유성우")]


def test_chat_exception_hides_details(client, monkeypatch):
    secret = "sk-SECRET-KEY-12345"

    def boom(session_id, message):
        raise RuntimeError(f"upstream failed https://api.example.com/?serviceKey={secret}")

    monkeypatch.setattr(chat_api, "_run_turn", boom)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 500
    assert list(r.json().keys()) == ["detail"]
    text = r.text
    assert "Traceback" not in text
    assert secret not in text
    assert "serviceKey" not in text
    assert "https://" not in text


# ── plans ────────────────────────────────────────────────────

def test_plans_crud(client):
    assert client.get("/api/plans").json() == []

    r = client.post("/api/plans", json=_plan_body())
    assert r.status_code == 201
    saved = r.json()
    assert saved["overlaps"] == []
    pid = saved["plan"]["id"]

    # 겹치는 두 번째 계획 — 저장은 되고 overlaps 로 알려 준다
    r2 = client.post("/api/plans", json=_plan_body(event_title="겹침"))
    assert r2.status_code == 201
    assert [p["id"] for p in r2.json()["overlaps"]] == [pid]

    assert len(client.get("/api/plans").json()) == 2
    assert client.get(f"/api/plans/{pid}").json()["event_title"] == "쌍둥이자리 유성우 극대"

    r = client.patch(f"/api/plans/{pid}", json={"event_title": "수정됨", "location_label": "대전"})
    assert r.status_code == 200
    assert r.json()["event_title"] == "수정됨"
    assert r.json()["location_label"] == "대전"

    assert client.delete(f"/api/plans/{pid}").status_code == 204
    assert client.get(f"/api/plans/{pid}").status_code == 404


def test_plans_not_found(client):
    assert client.get("/api/plans/999").status_code == 404
    assert client.patch("/api/plans/999", json={"event_title": "x"}).status_code == 404
    assert client.delete("/api/plans/999").status_code == 404


def test_plans_validation(client):
    # 생성: 종료 ≤ 시작 → 422 (모델 검증)
    r = client.post("/api/plans", json=_plan_body(end_dt="2026-12-14T21:00:00"))
    assert r.status_code == 422
    # 수정: 저장소 ValueError → 422
    pid = client.post("/api/plans", json=_plan_body()).json()["plan"]["id"]
    r = client.patch(f"/api/plans/{pid}", json={"end_dt": "2026-12-14T20:00:00"})
    assert r.status_code == 422
    assert "Traceback" not in r.text


# ── logs ─────────────────────────────────────────────────────

def test_logs_list_detail_delete(client):
    sessions = client.get("/api/logs").json()
    assert [s["session_id"] for s in sessions] == ["sess-001"]

    events = client.get("/api/logs/sess-001").json()
    assert [e["seq"] for e in events] == [1, 2]

    assert client.delete("/api/logs/sess-001").status_code == 204
    assert client.get("/api/logs/sess-001").status_code == 404
    assert client.delete("/api/logs/sess-001").status_code == 404


def test_logs_have_no_update_api(client):
    assert client.put("/api/logs/x", json={}).status_code == 405
    assert client.patch("/api/logs/x", json={}).status_code == 405


# ── 정적 파일 ─────────────────────────────────────────────────

def test_frontend_served(client):
    from astrowatch.app.main import FRONTEND_DIR

    if not (FRONTEND_DIR / "index.html").exists():
        pytest.skip("frontend/index.html 없음")
    r = client.get("/")
    assert r.status_code == 200
    assert "Astrowatch" in r.text
