"""search_observatory — 카카오 키워드 검색 samples 로 검증."""

from __future__ import annotations

import json

import pytest

from astrowatch.clients import http, kakao
from astrowatch.config import reset_settings_cache
from astrowatch.tools import observatory
from astrowatch.tools.base import ErrorCode
from astrowatch.tools.contracts import ObservatoryOutput

ARGS = {"lat": 36.3504, "lon": 127.3845, "radius_km": 20}


@pytest.fixture
def kakao_key(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_KEY", "dummy-kakao-key")
    reset_settings_cache()


def test_places_from_sample(monkeypatch, sample, kakao_key):
    calls = []

    def fake(params):
        calls.append(params)
        return json.loads(sample("kakao_keyword_observatory_daejeon.json"))

    monkeypatch.setattr(kakao, "request", fake)
    r = observatory.search_observatory.invoke(ARGS)
    assert r.ok, r.error
    first = r.data.places[0]
    assert first.name == "대전시민천문대" and first.distance_m == 4431 and first.place_id == "8263441"
    assert first.address == "대전 유성구 과학로 213-48" and first.url.startswith("http://place.map.kakao.com/")
    assert first.category == "관광명소"
    assert any(p.category is None for p in r.data.places)  # 빈 category_group_name → None
    assert r.data.note == ObservatoryOutput.model_fields["note"].default
    p = calls[0]
    assert p["radius"] == "20000" and p["sort"] == "distance" and p["size"] == "15" and p["query"] == "천문대"
    assert p["x"].startswith("127.38") and p["y"].startswith("36.35")


def test_no_result(monkeypatch, kakao_key):
    monkeypatch.setattr(kakao, "request", lambda params: {"documents": [], "meta": {"total_count": 0}})
    r = observatory.search_observatory.invoke({**ARGS, "query": "없는장소xyz"})
    assert not r.ok and r.code is ErrorCode.NO_RESULT


def test_no_key_is_auth(monkeypatch):
    monkeypatch.setattr(kakao, "request", lambda *a, **k: pytest.fail("네트워크 호출 금지"))
    r = observatory.search_observatory.invoke(ARGS)
    assert not r.ok and r.code is ErrorCode.AUTH


def test_rejected_key_is_auth(monkeypatch, kakao_key):
    def denied(params):
        raise http.AuthError("Kakao 인증 실패 (HTTP 401)")

    monkeypatch.setattr(kakao, "request", denied)
    r = observatory.search_observatory.invoke(ARGS)
    assert r.code is ErrorCode.AUTH and "dummy-kakao-key" not in r.error.message


def test_radius_over_limit_is_bad_input(kakao_key):
    assert observatory.search_observatory.invoke({**ARGS, "radius_km": 30}).code is ErrorCode.BAD_INPUT
