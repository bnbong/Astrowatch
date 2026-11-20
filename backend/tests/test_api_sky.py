"""하늘 가리키기 API (/api/sky) 와 계획 저장 시 target_object 추론. 실제 저장소(임시 DB)를 쓴다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import API_HEADERS


@pytest.fixture
def client(monkeypatch):
    from astrowatch.app import main

    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    with TestClient(main.create_app(), raise_server_exceptions=False, headers=API_HEADERS) as c:
        yield c


def _plan(client, **over):
    body = {
        "event_title": "쌍둥이자리 유성우 극대기",
        "event_kind": "meteor",
        "start_dt": "2026-12-14T22:00:00",
        "end_dt": "2026-12-15T02:00:00",
        "location_label": "대전",
        "lat": 36.35,
        "lon": 127.38,
    }
    body.update(over)
    r = client.post("/api/plans", json=body)
    assert r.status_code == 201, r.text
    return r.json()["plan"]


# ── 계획 저장: target_object ─────────────────────────────────

def test_plan_create_infers_target(client):
    assert _plan(client)["target_object"] == "geminids"
    assert _plan(client, event_title="안드로메다 은하 (M31)", event_kind="deep_sky")["target_object"] == "안드로메다 은하"
    assert _plan(client, event_title="화성 최대 접근", event_kind="planet")["target_object"] == "mars"
    assert _plan(client, event_title="알 수 없는 현상", event_kind="other")["target_object"] is None


def test_plan_create_normalizes_given_target(client):
    p = _plan(client, event_title="저녁 관측", event_kind="other", target_object="M31")
    assert p["target_object"] == "안드로메다 은하"
    # 모르는 이름을 보내면 제목에서 추론, 그래도 없으면 null
    p = _plan(client, event_title="쌍둥이자리 유성우", target_object="없는 천체")
    assert p["target_object"] == "geminids"
    assert client.get(f"/api/plans/{p['id']}").json()["target_object"] == "geminids"


def test_plan_update_target(client):
    p = _plan(client, event_title="알 수 없는 현상", event_kind="other")
    r = client.patch(f"/api/plans/{p['id']}", json={"target_object": "목성"})
    assert r.status_code == 200 and r.json()["target_object"] == "jupiter"
    assert client.patch(f"/api/plans/{p['id']}", json={"target_object": "없는 천체"}).status_code == 422
    r = client.patch(f"/api/plans/{p['id']}", json={"target_object": None})
    assert r.status_code == 200 and r.json()["target_object"] is None


def test_plan_target_too_long_is_422(client):
    r = client.post("/api/plans", json={
        "event_title": "x", "start_dt": "2026-12-14T22:00:00", "end_dt": "2026-12-15T02:00:00",
        "location_label": "서울", "target_object": "가" * 101,
    })
    assert r.status_code == 422


# ── GET /api/sky/target ──────────────────────────────────────

def test_target_by_key(client):
    r = client.get("/api/sky/target", params={"key": "안드로메다 은하", "lat": 37.5665, "lon": 126.978,
                                              "at": "2026-11-20T21:00:00+09:00"})
    assert r.status_code == 200
    d = r.json()
    assert (d["key"], d["name"], d["kind"], d["subkind"]) == ("안드로메다 은하", "안드로메다 은하", "object", "galaxy")
    assert d["ra"] == pytest.approx(10.6847, abs=1e-3)
    assert d["location_source"] == "query" and d["refresh_s"] is None
    # 11월 20일 21시 서울: 안드로메다 은하는 높이 떠 있다
    assert d["above_horizon"] is True and d["alt"] > 60
    assert d["rises_at"] is None
    assert d["at"].startswith("2026-11-20T12:00:00")
    assert set(d) >= {"key", "name", "kind", "ra", "dec", "alt", "az", "above_horizon", "compass", "at", "lat", "lon"}


def test_target_default_location_is_seoul(client):
    d = client.get("/api/sky/target", params={"key": "mars"}).json()
    assert (d["lat"], d["lon"], d["location_source"]) == (37.5665, 126.978, "default")
    assert d["kind"] == "planet" and d["refresh_s"] == 60


def test_target_by_plan_uses_plan_location(client):
    p = _plan(client)
    d = client.get("/api/sky/target", params={"plan_id": p["id"]}).json()
    assert d["key"] == "geminids" and d["kind"] == "radiant"
    assert (d["lat"], d["lon"], d["location_source"]) == (36.35, 127.38, "plan")
    assert d["plan_id"] == p["id"] and d["plan_title"] == "쌍둥이자리 유성우 극대기"
    # key 를 함께 주면 대상만 바꾸고 위치는 계획 그대로
    d = client.get("/api/sky/target", params={"plan_id": p["id"], "key": "목성"}).json()
    assert d["key"] == "jupiter" and d["location_source"] == "plan"


def test_target_by_plan_without_coords_uses_region(client):
    p = _plan(client, lat=None, lon=None, location_label="강릉")
    d = client.get("/api/sky/target", params={"plan_id": p["id"]}).json()
    assert d["location_source"] == "region" and d["location_label"] == "강릉"
    assert d["lat"] == pytest.approx(37.7519)


def test_target_below_horizon_has_rise_time(client):
    # 6월 1일 밤 23시 서울: 시리우스는 해를 따라 이미 졌다
    d = client.get("/api/sky/target", params={"key": "시리우스", "at": "2026-06-01T23:00:00+09:00"}).json()
    assert d["above_horizon"] is False and d["rises_at"] is not None


def test_target_404(client):
    assert client.get("/api/sky/target", params={"key": "없는천체이름"}).status_code == 404
    assert client.get("/api/sky/target", params={"plan_id": 999}).status_code == 404
    p = _plan(client, event_title="알 수 없는 현상", event_kind="other")
    r = client.get("/api/sky/target", params={"plan_id": p["id"]})
    assert r.status_code == 404 and r.json()["detail"] == "이 계획에는 가리킬 천체가 없습니다"


@pytest.mark.parametrize("params", [
    {},                                              # key, plan_id 둘 다 없음
    {"key": "mars", "lat": 91, "lon": 0},
    {"key": "mars", "lat": 0, "lon": 181},
    {"key": "mars", "lat": 37.5},                    # lon 없이 lat 만
    {"key": "가" * 101},
    {"key": ""},
    {"plan_id": 0},
    {"key": "mars", "at": "어제"},
])
def test_target_422(client, params):
    assert client.get("/api/sky/target", params=params).status_code == 422


@pytest.mark.parametrize("at", ["0001-01-01T00:00:00", "9999-12-31T23:59:59", "1899-12-31T23:00:00+00:00"])
@pytest.mark.parametrize("key", ["moon", "시리우스"])
def test_target_at_out_of_range_is_422(client, at, key):
    """R5: 형식은 맞지만 지원 범위 밖 시각은 500 이 아니라 422 와 한국어 안내."""
    r = client.get("/api/sky/target", params={"key": key, "at": at})
    assert r.status_code == 422, r.text
    assert "1900-01-01" in r.json()["detail"]


def test_target_at_range_edges_ok(client):
    for at in ("1900-01-01T12:00:00", "2100-12-31T12:00:00"):
        assert client.get("/api/sky/target", params={"key": "moon", "at": at}).status_code == 200


# ── GET /api/sky/targets ─────────────────────────────────────

def test_targets_search(client):
    r = client.get("/api/sky/targets", params={"q": "쌍둥"})
    assert r.status_code == 200
    assert r.json()[0] == {"key": "geminids", "name": "쌍둥이자리 유성우", "kind": "radiant"}
    assert len(client.get("/api/sky/targets").json()) == 8
    assert client.get("/api/sky/targets", params={"q": "없는이름xyz"}).json() == []
    assert client.get("/api/sky/targets", params={"q": "가" * 51}).status_code == 422


def test_pointer_page_is_served(client):
    r = client.get("/pointer.html")
    assert r.status_code == 200
    assert "js/pointer.js?v=9" in r.text and "js/skymath.js?v=9" in r.text
    assert "<script>" not in r.text and "onclick" not in r.text      # CSP: 인라인 스크립트 없음
    assert "geolocation=(self)" in r.headers["permissions-policy"]
