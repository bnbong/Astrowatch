"""적경·적위 → 고도·방위, 그리고 '저녁 기준 날짜' 분 표기의 가시 구간.

astro.js 의 julianDay · localSiderealDeg · altAz 를 그대로 옮겼다 (정확도: 분 단위 관측 계획에 충분).
분 표기: 저녁 기준 날짜(date) 00:00(Asia/Seoul) 이 0, 다음 날 02:00 은 1560.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Callable

KST = dt.timezone(dt.timedelta(hours=9))   # Asia/Seoul 은 일광절약시간이 없다
DAY_START, DAY_END = 720, 2160             # 저녁 기준 날짜 12:00 ~ 다음 날 12:00
MAX_MINUTE = 2159                          # Window 상한

RaDecFn = Callable[[dt.datetime], tuple[float, float]]


def julian_day(dt_utc: dt.datetime) -> float:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=dt.timezone.utc)
    return dt_utc.timestamp() / 86400.0 + 2440587.5


def local_sidereal_deg(dt_utc: dt.datetime, lon: float) -> float:
    """지방 항성시(도)."""
    gmst = 280.46061837 + 360.98564736629 * (julian_day(dt_utc) - 2451545.0)
    return (gmst + lon) % 360.0


def alt_az(ra: float, dec: float, lat: float, lst: float) -> tuple[float, float]:
    """(고도, 방위) 도. 방위는 북 0° → 동 90°."""
    d = math.radians
    ha, de, phi = d(lst - ra), d(dec), d(lat)
    sin_alt = math.sin(de) * math.sin(phi) + math.cos(de) * math.cos(phi) * math.cos(ha)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(-math.cos(de) * math.sin(ha),
                    math.sin(de) * math.cos(phi) - math.cos(de) * math.sin(phi) * math.cos(ha))
    return math.degrees(alt), math.degrees(az) % 360.0


def minute_to_utc(date: dt.date, minute: int) -> dt.datetime:
    """저녁 기준 날짜의 분 → UTC 일시."""
    local = dt.datetime.combine(date, dt.time(0), tzinfo=KST) + dt.timedelta(minutes=minute)
    return local.astimezone(dt.timezone.utc)


def _fixed(ra: float, dec: float) -> RaDecFn:
    return lambda _t: (ra, dec)


def altitude_at(radec: RaDecFn, lat: float, lon: float, date: dt.date, minute: int) -> tuple[float, float]:
    t = minute_to_utc(date, minute)
    ra, dec = radec(t)
    return alt_az(ra, dec, lat, local_sidereal_deg(t, lon))


def visible_spans(radec: RaDecFn, lat: float, lon: float, date: dt.date,
                  min_alt: float = 20, step: int = 10) -> list[tuple[int, int]]:
    """고도 ≥ min_alt 인 [start, end) 분 구간들 (12:00 ~ 다음 날 12:00, step 분 간격 표본)."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for m in range(DAY_START, DAY_END, step):
        up = altitude_at(radec, lat, lon, date, m)[0] >= min_alt
        if up and start is None:
            start = m
        elif not up and start is not None:
            spans.append((start, m))
            start = None
    if start is not None:
        spans.append((start, MAX_MINUTE))
    return [(a, min(b, MAX_MINUTE)) for a, b in spans]


def culmination(radec: RaDecFn, lat: float, lon: float, date: dt.date) -> tuple[int, float, float]:
    """(분, 고도, 방위) — 12:00~다음 날 12:00 중 가장 높은 때. 10분 표본 뒤 1분 단위로 다듬는다."""
    best = max(range(DAY_START, DAY_END, 10), key=lambda m: altitude_at(radec, lat, lon, date, m)[0])
    lo, hi = max(DAY_START, best - 10), min(MAX_MINUTE, best + 10)
    m = max(range(lo, hi + 1), key=lambda x: altitude_at(radec, lat, lon, date, x)[0])
    alt, az = altitude_at(radec, lat, lon, date, m)
    return m, alt, az


def visibility_minutes(ra: float, dec: float, lat: float, lon: float, date: dt.date,
                       min_alt: float = 20, step: int = 10) -> list[tuple[int, int]]:
    """고정 천체(별·DSO)가 고도 min_alt 이상인 분 구간 목록."""
    return visible_spans(_fixed(ra, dec), lat, lon, date, min_alt, step)


def transit(ra: float, dec: float, lat: float, lon: float, date: dt.date) -> tuple[int, float]:
    """남중(가장 높을 때) (분, 고도)."""
    m, alt, _az = culmination(_fixed(ra, dec), lat, lon, date)
    return m, alt
