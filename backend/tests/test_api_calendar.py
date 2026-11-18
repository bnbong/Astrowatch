"""캘린더 API (심화 F3023) — 임시 DB + local 구현체. Google 은 토큰 없음(503)만, 네트워크 없음."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from astrowatch.app.api import calendar as calendar_api
from astrowatch.config import reset_settings_cache
from astrowatch.tools.base import ErrorCode, fail


@pytest.fixture
def client(monkeypatch):
    from astrowatch.app import main

    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    with TestClient(main.create_app(), raise_server_exceptions=False) as c:
        yield c


def _create_plan(client: TestClient) -> int:
    res = client.post(
        "/api/plans",
        json={
            "event_title": "쌍둥이자리 유성우 극대",
            "event_kind": "meteor",
            "start_dt": "2026-12-14T22:10:00",
            "end_dt": "2026-12-15T01:30:00",
            "location_label": "서울",
            "basis": "적합",
        },
    )
    assert res.status_code == 201
    return res.json()["plan"]["id"]


def test_register_then_query_local(client):
    pid = _create_plan(client)
    res = client.post(f"/api/plans/{pid}/calendar")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["provider"] == "local" and data["already_registered"] is False
    assert data["title"] == "[Astrowatch] 쌍둥이자리 유성우 극대 관측 — 서울"
    assert client.get(f"/api/plans/{pid}").json()["calendar_event_id"] == data["event_id"]

    again = client.post(f"/api/plans/{pid}/calendar").json()
    assert again["data"]["already_registered"] is True and again["data"]["event_id"] == data["event_id"]

    q = client.get("/api/calendar", params={"date_from": "2026-12-14", "date_to": "2026-12-15"})
    assert q.status_code == 200
    events = q.json()["data"]["events"]
    assert len(events) == 1 and events[0]["plan_id"] == pid


def test_register_missing_plan_404(client):
    res = client.post("/api/plans/999/calendar")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_google_without_token_503(client, tmp_path, monkeypatch):
    pid = _create_plan(client)
    monkeypatch.setenv("CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "missing.json"))
    reset_settings_cache()
    res = client.post(f"/api/plans/{pid}/calendar")
    assert res.status_code == 503
    body = res.json()
    assert body["ok"] is False and body["error"]["code"] == "AUTH"
    assert "missing.json" not in res.text and "google_auth.py" not in res.text  # 상세 노출 금지
    assert client.get(f"/api/plans/{pid}").json()["calendar_event_id"] is None


def test_upstream_failure_502(client, monkeypatch):
    class FakeTool:
        def invoke(self, raw):
            return fail(ErrorCode.UPSTREAM, "내부 상세 메시지")

    monkeypatch.setattr(calendar_api, "_tools", lambda: (FakeTool(), FakeTool()))
    res = client.post("/api/plans/1/calendar")
    assert res.status_code == 502 and "내부 상세" not in res.text
    res = client.get("/api/calendar", params={"date_from": "2026-12-14", "date_to": "2026-12-15"})
    assert res.status_code == 502


def test_query_validation(client):
    assert client.get("/api/calendar", params={"date_from": "2026-12-14"}).status_code == 422
    res = client.get("/api/calendar", params={"date_from": "2026-12-15", "date_to": "2026-12-01"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "BAD_INPUT"
