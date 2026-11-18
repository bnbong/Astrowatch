"""get_night_window — 출몰 두 날짜 + 월령. samples 로 검증."""

from __future__ import annotations

import pytest

from astrowatch.clients import http, kasi
from astrowatch.config import reset_settings_cache
from astrowatch.tools import night_window
from astrowatch.tools.base import ErrorCode

ARGS = {"date": "2026-12-14", "lat": 37.5665, "lon": 126.9780}


@pytest.fixture
def data_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "dummy-test-key")
    reset_settings_cache()


def _fake(sample, *, riseset=None, lunage=None, lun_error=None):
    calls = []

    def fake(path, params):
        calls.append((path, params))
        if path == kasi.RISE_SET:
            return riseset if riseset is not None else sample("kasi_riseset_seoul_20261214.xml")
        if path == kasi.LUN_PHASE:
            if lun_error:
                raise lun_error
            return lunage if lunage is not None else sample("kasi_lunage_20261214.xml")
        raise AssertionError(path)

    return fake, calls


def test_window_from_sample(monkeypatch, sample, data_key):
    fake, calls = _fake(sample)
    monkeypatch.setattr(kasi, "request", fake)
    r = night_window.get_night_window.invoke(ARGS)
    assert r.ok, r.error
    d = r.data
    assert (d.sunset, d.astro_dusk_end, d.astro_dawn_start, d.sunrise_next) == ("17:14", "18:48", "06:05", "07:39")
    assert d.moonrise == "11:11" and d.moonset == "21:52"
    assert (d.moonrise_next, d.moonset_next) == ("11:11", "21:52")  # fake 는 같은 샘플을 두 번 돌려줌
    assert d.dark_minutes == (24 * 60 + 6 * 60 + 5) - (18 * 60 + 48) == 677
    assert d.lun_age == 5.5 and r.note is None
    dates = [p["locdate"] for path, p in calls if path == kasi.RISE_SET]
    assert dates == ["20261214", "20261215"]  # 오늘 + 다음 날
    rs = next(p for path, p in calls if path == kasi.RISE_SET)
    assert rs["dnYn"] == "Y"


def test_lunage_failure_is_partial_success(monkeypatch, sample, data_key):
    fake, _ = _fake(sample, lun_error=http.UpstreamError("KASI", 500))
    monkeypatch.setattr(kasi, "request", fake)
    r = night_window.get_night_window.invoke(ARGS)
    assert r.ok and r.data.lun_age is None and r.note


def test_blank_twilight_is_no_window(monkeypatch, sample, data_key):
    blank = sample("kasi_riseset_seoul_20261214.xml").replace("<aste>1848  </aste>", "<aste>----  </aste>")
    fake, _ = _fake(sample, riseset=blank)
    monkeypatch.setattr(kasi, "request", fake)
    r = night_window.get_night_window.invoke(ARGS)
    assert not r.ok and r.code is ErrorCode.NO_WINDOW


def test_no_key_is_auth(monkeypatch):
    monkeypatch.setattr(kasi, "request", lambda *a, **k: pytest.fail("네트워크 호출 금지"))
    r = night_window.get_night_window.invoke(ARGS)
    assert not r.ok and r.code is ErrorCode.AUTH


def test_timeout_mapping(monkeypatch, data_key):
    def slow(path, params):
        raise http.UpstreamTimeout("x")

    monkeypatch.setattr(kasi, "request", slow)
    assert night_window.get_night_window.invoke(ARGS).code is ErrorCode.TIMEOUT


def test_hhmm_parsing():
    assert night_window.hhmm_minutes("0605  ") == 365
    assert night_window.hhmm_minutes("--") is None
    assert night_window.hhmm_minutes("    ") is None
