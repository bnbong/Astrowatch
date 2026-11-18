"""천문 좌표 계산 (astro.js 이식) — M31 · 북극성 · 자정 넘김 표기."""

from __future__ import annotations

import datetime as dt

from astrowatch.astro import alt_az, julian_day, local_sidereal_deg, transit, visibility_minutes
from astrowatch.astro import planets

DAEJEON = (36.3504, 127.3845)
D = dt.date(2026, 11, 18)
M31 = (10.6847, 41.2688)
POLARIS = (37.9545, 89.2641)


def test_julian_day_j2000():
    assert julian_day(dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)) == 2451545.0
    assert abs(local_sidereal_deg(dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc), 0) - 280.46061837) < 1e-6


def test_alt_az_zenith_and_pole():
    alt, _ = alt_az(ra=100, dec=36.35, lat=36.35, lst=100)       # 자오선 위 천정
    assert abs(alt - 90) < 1e-6
    alt, az = alt_az(ra=0, dec=90, lat=36.35, lst=123)            # 천구 북극 = 위도 고도, 북쪽
    assert abs(alt - 36.35) < 1e-6 and (az < 1 or az > 359)


def test_m31_daejeon_nov18():
    spans = visibility_minutes(*M31, *DAEJEON, D)
    assert len(spans) == 1
    start, end = spans[0]
    assert start < 18 * 60 + 30                    # 저녁(천문박명 종료 무렵)에 이미 20° 이상
    assert 1440 + 3 * 60 <= end <= 1440 + 4 * 60   # 새벽 3~4시에 20° 아래로
    minute, alt = transit(*M31, *DAEJEON, D)
    assert 20 * 60 <= minute <= 22 * 60            # 남중 21시 무렵
    assert 80 < alt < 90                           # 90 - |36.35 - 41.27| ≈ 85°


def test_polaris_up_all_night():
    assert visibility_minutes(*POLARIS, *DAEJEON, D) == [(720, 2159)]


def test_southern_object_never_rises():
    assert visibility_minutes(100, -70, *DAEJEON, D) == []


def test_window_crosses_midnight():
    # 새벽에 뜨는 천체(목성 근처 적경 ~148°): 구간이 24시(1440)를 넘겨 표기된다
    spans = visibility_minutes(148.0, 13.7, *DAEJEON, D)
    assert spans and all(a >= 1440 for a, _ in spans) and all(b <= 2159 for _, b in spans)


def test_planet_radec_plausible():
    t = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)
    ra, dec = planets.radec("jupiter", t)
    assert 0 <= ra < 360 and -30 < dec < 30
    assert planets.find_body("목성") == "jupiter" and planets.find_body("Jupiter") == "jupiter"
    assert planets.find_body("안드로메다") is None
