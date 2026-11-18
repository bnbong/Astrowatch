"""search_sky_events · 분류기 · 레지스트리 등록 — 네트워크 없이 samples 로 검증."""

from __future__ import annotations

import importlib
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from astrowatch.clients import http, kasi
from astrowatch.config import reset_settings_cache
from astrowatch.tools import sky_events
from astrowatch.tools.base import ErrorCode, ToolKind
from astrowatch.tools.contracts import EventKind
from astrowatch.tools.event_kind import classify


@pytest.fixture
def data_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "dummy-test-key")
    reset_settings_cache()


@pytest.fixture
def astro_sample(monkeypatch, sample, data_key):
    calls = []

    def fake(path, params):
        calls.append((path, params))
        return sample("kasi_astro_event.xml")

    monkeypatch.setattr(kasi, "request", fake)
    return calls


def test_events_parsed_from_sample(astro_sample):
    r = sky_events.search_sky_events.invoke({"year": 2026, "month": 12})
    assert r.ok, r.error
    events = r.data.events
    assert len(events) == 15  # 16건 중 월 요약 행(locdate 6자리) 제외
    assert astro_sample[0][1]["solMonth"] == "12" and astro_sample[0][1]["numOfRows"] == "100"
    gem = next(e for e in events if e.date == date(2026, 12, 14))
    assert gem.time == "23:00" and gem.kind is EventKind.METEOR
    assert gem.event_id == "20261214-1" and "쌍둥이자리" in gem.title
    assert all(e.title for e in events)
    assert r.data.requested_defaulted is False


def test_kind_filter(astro_sample):
    r = sky_events.search_sky_events.invoke({"year": 2026, "month": 12, "kind": "meteor"})
    assert r.ok and len(r.data.events) == 2
    assert {e.date.day for e in r.data.events} == {14, 23}


def test_kind_filter_no_result(astro_sample):
    r = sky_events.search_sky_events.invoke({"year": 2026, "month": 12, "kind": "eclipse"})
    assert not r.ok and r.code is ErrorCode.NO_RESULT


def test_no_key_is_auth_without_network(monkeypatch):
    monkeypatch.setattr(kasi, "request", lambda *a, **k: pytest.fail("네트워크 호출 금지"))
    r = sky_events.search_sky_events.invoke({"year": 2026, "month": 12})
    assert not r.ok and r.code is ErrorCode.AUTH


def test_empty_body_is_no_result(monkeypatch, data_key):
    empty = '<?xml version="1.0"?><response><header><resultCode>00</resultCode></header><body><items/></body></response>'
    monkeypatch.setattr(kasi, "request", lambda path, params: empty)
    r = sky_events.search_sky_events.invoke({"year": 2030, "month": 1})
    assert not r.ok and r.code is ErrorCode.NO_RESULT


def test_gateway_error_in_200_body_is_auth(monkeypatch, data_key):
    body = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
        "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
        "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    monkeypatch.setattr(http, "get", lambda *a, **k: SimpleNamespace(text=body, status_code=200))
    r = sky_events.search_sky_events.invoke({"year": 2026, "month": 12})
    assert not r.ok and r.code is ErrorCode.AUTH
    assert "dummy-test-key" not in r.error.message


def test_upstream_and_timeout_mapping(monkeypatch, data_key):
    def boom(path, params):
        raise http.UpstreamError("KASI", 500)

    monkeypatch.setattr(kasi, "request", boom)
    assert sky_events.search_sky_events.invoke({"year": 2026, "month": 12}).code is ErrorCode.UPSTREAM

    def slow(path, params):
        raise http.UpstreamTimeout("KASI 시간 초과")

    monkeypatch.setattr(kasi, "request", slow)
    assert sky_events.search_sky_events.invoke({"year": 2026, "month": 12}).code is ErrorCode.TIMEOUT


def test_result_code_not_00_is_upstream(monkeypatch, data_key):
    body = '<response><header><resultCode>22</resultCode><resultMsg>LIMITED</resultMsg></header></response>'
    monkeypatch.setattr(kasi, "request", lambda path, params: body)
    assert sky_events.search_sky_events.invoke({"year": 2026, "month": 12}).code is ErrorCode.UPSTREAM


@pytest.mark.parametrize(
    "title, kind",
    [
        ("쌍둥이자리 유성우 극대기(ZHR=150) (IMO 기준)", EventKind.METEOR),
        ("개기월식", EventKind.ECLIPSE),
        ("달의 토성 엄폐", EventKind.OCCULTATION),
        ("토성 충", EventKind.PLANET),
        ("수성 동방 최대이각", EventKind.PLANET),
        ("해왕성 유(서-동)", EventKind.PLANET),
        ("목성과 레굴루스의 근접(2.8°)", EventKind.PLANET),
        ("달과 화성의 근접(3.4°)", EventKind.MOON),
        ("달의 원지점(406,400km)", EventKind.MOON),
        ("합삭", EventKind.MOON),
        ("망", EventKind.MOON),
        ("하현", EventKind.MOON),
        ("동지", EventKind.SEASON),
        ("", EventKind.OTHER),
        ("국제 천문의 날", EventKind.OTHER),
    ],
)
def test_classify(title, kind):
    assert classify(title) is kind


def test_builtin_tools_registered():
    reg_mod = importlib.import_module("astrowatch.tools.registry")

    reg_mod.registry.clear()
    for mod in ("sky_events", "night_window", "sky_forecast", "observatory"):
        name = f"astrowatch.tools.{mod}"
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    reg = reg_mod.load_builtin_tools()
    described = {d["name"]: d for d in reg.describe()}
    expected = {"search_sky_events", "get_night_window", "get_sky_forecast", "search_observatory"}
    assert expected <= set(described)
    for name in expected:
        assert described[name]["kind"] == ToolKind.EXTERNAL.value
        assert described[name]["description"] and described[name]["input_schema"]["properties"]
