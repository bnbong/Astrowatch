"""get_sky_forecast — 공급자 선택 · 단기/중기/OWM 변환 · 범위·키 실패. samples 로 검증."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from astrowatch.clients import kma, owm
from astrowatch.config import reset_settings_cache
from astrowatch.data.regions import latlon_to_grid, nearest_region
from astrowatch.tools import sky_forecast
from astrowatch.tools.base import ErrorCode
from astrowatch.tools.contracts import SkyCondition

SEOUL = {"lat": 37.5665, "lon": 126.9780}
DAEJEON = {"lat": 36.3504, "lon": 127.3845}
NOW = dt.datetime(2026, 11, 18, 12, 0)  # samples 수집 시각 기준


@pytest.fixture
def kma_env(monkeypatch):
    monkeypatch.setenv("WEATHER_PROVIDER", "kma")
    monkeypatch.setenv("DATA_GO_KR_KEY", "dummy-test-key")
    reset_settings_cache()
    monkeypatch.setattr(sky_forecast, "now_local", lambda: NOW)


@pytest.fixture
def kma_calls(monkeypatch, sample, kma_env):
    calls = []

    def fake(url, params):
        calls.append((url, params))
        if url == kma.VILAGE_URL:
            return json.loads(sample("kma_vilage_fcst_seoul.json"))
        if url == kma.MID_LAND_URL:
            return json.loads(sample("kma_mid_land_fcst.json"))
        raise AssertionError(url)

    monkeypatch.setattr(kma, "request", fake)
    return calls


def test_mock_provider_default():
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-12-14", **SEOUL})
    assert r.ok and r.data.source == "mock" and r.data.coverage == "full"
    assert [h.hour for h in r.data.hourly] == list(range(18, 30))
    assert all(h.sky is SkyCondition.CLOUDY and h.pop == 80 for h in r.data.hourly)


def test_kma_short_full(kma_calls):
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-18", **SEOUL})
    assert r.ok, r.error
    d = r.data
    assert d.source == "kma_short" and d.coverage == "full" and r.note is None
    assert d.base_time == "20261118 1100" and d.grid == latlon_to_grid(**SEOUL) == (60, 127)
    params = kma_calls[0][1]
    assert params["base_time"] == "1100" and (params["nx"], params["ny"]) == ("60", "127")
    items = json.loads(open(sky_forecast.get_settings().samples_dir / "kma_vilage_fcst_seoul.json", encoding="utf-8").read())
    raw = {
        (i["fcstDate"], i["fcstTime"], i["category"]): i["fcstValue"]
        for i in items["response"]["body"]["items"]["item"]
    }
    h24 = next(h for h in d.hourly if h.hour == 24)  # 다음 날 0시
    sky_code = raw[("20261119", "0000", "SKY")]
    assert h24.sky is {"1": SkyCondition.CLEAR, "3": SkyCondition.PARTLY, "4": SkyCondition.CLOUDY}[sky_code]
    assert h24.pop == int(raw[("20261119", "0000", "POP")])


def test_kma_short_partial(kma_calls):
    # 11/21 은 3시간 간격, 11/22 03시 이후 값 없음 → partial
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-21", **SEOUL})
    assert r.ok and r.data.coverage == "partial" and "예보 없음" in r.note
    by_hour = {h.hour: h for h in r.data.hourly}
    assert by_hour[19].sky is not SkyCondition.UNKNOWN  # 18시 슬롯 값
    assert by_hour[27].sky is SkyCondition.UNKNOWN


def test_kma_mid(kma_calls):
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-23", **SEOUL, "hours": [20, 22, 25]})
    assert r.ok, r.error
    d = r.data
    assert d.source == "kma_mid" and d.base_time == "20261118 0600" and d.coverage == "full"
    by_hour = {h.hour: h for h in d.hourly}
    assert by_hour[20].sky is SkyCondition.CLOUDY and by_hour[20].pop == 60   # wf5Pm '흐리고 비'
    assert by_hour[22].sky is SkyCondition.CLOUDY
    assert by_hour[25].sky is SkyCondition.PARTLY and by_hour[25].pop == 20   # 다음 날 wf6Am '구름많음'
    params = kma_calls[0][1]
    assert params["regId"] == nearest_region(**SEOUL).mid_land_reg_id == "11B00000"
    assert params["tmFc"] == "202611180600"
    assert "중기예보" in r.note


def test_kma_mid_clear(kma_calls):
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-22", **SEOUL})
    assert r.ok and r.data.source == "kma_mid"
    for h in r.data.hourly:
        if h.hour < 24:
            assert h.sky is SkyCondition.CLEAR and h.pop == 10      # wf4Pm '맑음'
        else:
            assert h.sky is SkyCondition.CLOUDY and h.pop == 30     # 다음 날 wf5Am '흐림'


def test_kma_mid_beyond_coverage_is_unknown(kma_calls):
    # 11/28 은 wf10(하루 값) 이 있지만 11/29 (hour 25) 는 필드가 없다 → unknown, partial
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-28", **SEOUL, "hours": [22, 25]})
    assert r.ok and r.data.coverage == "partial"
    by_hour = {h.hour: h for h in r.data.hourly}
    assert by_hour[22].sky is SkyCondition.CLEAR and by_hour[25].sky is SkyCondition.UNKNOWN


def test_owm_preferred_over_mid(monkeypatch, sample, kma_env):
    monkeypatch.setenv("OPENWEATHER_KEY", "dummy-owm-key")
    reset_settings_cache()
    monkeypatch.setattr(owm, "request", lambda params: json.loads(sample("owm_forecast_daejeon.json")))
    monkeypatch.setattr(kma, "request", lambda *a: pytest.fail("중기예보를 부르면 안 된다"))
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-22", **DAEJEON})
    assert r.ok, r.error
    assert r.data.source == "owm" and r.data.coverage == "full"
    payload = json.loads(sample("owm_forecast_daejeon.json"))
    # 현지 11/22 18시 = UTC 09시 슬롯
    slot = next(s for s in payload["list"] if s["dt_txt"] == "2026-11-22 09:00:00")
    h18 = r.data.hourly[0]
    assert h18.hour == 18 and h18.cloud_pct == slot["clouds"]["all"]
    assert h18.pop == round(slot["pop"] * 100)
    assert h18.sky is sky_forecast.cloud_to_sky(slot["clouds"]["all"])


def test_out_of_range(kma_calls):
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-29", **SEOUL})
    assert not r.ok and r.code is ErrorCode.OUT_OF_RANGE
    assert "2026-11-28" in r.error.message
    assert kma_calls == []


def test_past_date_out_of_range(kma_calls):
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-10", **SEOUL})
    assert r.code is ErrorCode.OUT_OF_RANGE


def test_no_key_is_auth(monkeypatch):
    monkeypatch.setenv("WEATHER_PROVIDER", "kma")
    reset_settings_cache()
    monkeypatch.setattr(sky_forecast, "now_local", lambda: NOW)
    monkeypatch.setattr(kma, "request", lambda *a: pytest.fail("네트워크 호출 금지"))
    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-11-19", **SEOUL})
    assert not r.ok and r.code is ErrorCode.AUTH


def test_bad_hours(kma_env):
    assert sky_forecast.get_sky_forecast.invoke({"date": "2026-11-19", **SEOUL, "hours": [30]}).code is ErrorCode.BAD_INPUT


@pytest.mark.parametrize(
    "now, expected",
    [
        (dt.datetime(2026, 11, 18, 0, 5), ("20261117", "2300")),
        (dt.datetime(2026, 11, 18, 2, 9), ("20261117", "2300")),
        (dt.datetime(2026, 11, 18, 2, 10), ("20261118", "0200")),
        (dt.datetime(2026, 11, 18, 12, 0), ("20261118", "1100")),
        (dt.datetime(2026, 11, 18, 23, 59), ("20261118", "2300")),
    ],
)
def test_vilage_base(now, expected):
    assert kma.vilage_base(now) == expected


def test_mid_tmfc():
    assert kma.mid_tmfc(dt.datetime(2026, 11, 18, 5, 0)) == dt.datetime(2026, 11, 17, 18, 0)
    assert kma.mid_tmfc(dt.datetime(2026, 11, 18, 12, 0)) == dt.datetime(2026, 11, 18, 6, 0)
    assert kma.mid_tmfc(dt.datetime(2026, 11, 18, 19, 0)) == dt.datetime(2026, 11, 18, 18, 0)


def test_mid_sky_text():
    assert sky_forecast.mid_sky("맑음") is SkyCondition.CLEAR
    assert sky_forecast.mid_sky("구름많음") is SkyCondition.PARTLY
    assert sky_forecast.mid_sky("구름많고 비") is SkyCondition.CLOUDY
    assert sky_forecast.mid_sky("흐리고 비") is SkyCondition.CLOUDY
    assert sky_forecast.mid_sky(None) is SkyCondition.UNKNOWN


def test_kma_pty_to_precip():
    from astrowatch.tools.contracts import Precip

    items = []
    for t, sky, pty in (("2100", "4", "3"), ("2200", "4", "1"), ("2300", "1", "0")):
        items += [{"category": "SKY", "fcstDate": "20261118", "fcstTime": t, "fcstValue": sky},
                  {"category": "POP", "fcstDate": "20261118", "fcstTime": t, "fcstValue": "60"},
                  {"category": "PTY", "fcstDate": "20261118", "fcstTime": t, "fcstValue": pty}]
    slots = sky_forecast.short_slots(items, sky_forecast.target_times(dt.date(2026, 11, 18), [21, 22, 23]))
    assert [slots[h][3] for h in (21, 22, 23)] == [Precip.SNOW, Precip.RAIN, Precip.NONE]


def test_owm_weather_to_precip():
    from astrowatch.tools.contracts import Precip

    base = int(dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc).timestamp())   # 21:00 KST
    payload = {"city": {"timezone": 32400}, "list": [
        {"dt": base, "clouds": {"all": 90}, "pop": 0.8, "weather": [{"main": "Snow"}]},
        {"dt": base + 10800, "clouds": {"all": 10}, "pop": 0.0, "weather": [{"main": "Clear"}]},
    ]}
    slots = sky_forecast.owm_slots(payload, sky_forecast.target_times(dt.date(2026, 11, 18), [21, 24]))
    assert slots[21][3] is Precip.SNOW and slots[24][3] is Precip.NONE


def test_mock_precip_none():
    from astrowatch.tools.contracts import Precip

    r = sky_forecast.get_sky_forecast.invoke({"date": "2026-12-14", **SEOUL})
    assert all(h.precip is Precip.NONE for h in r.data.hourly)
