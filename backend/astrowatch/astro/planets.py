"""행성·달의 그날 적경·적위 (ephemeris.js 이식).

| 천체 | 방법 | 정확도 |
| 행성 | JPL "Approximate Positions of the Planets" 케플러 궤도 요소 (1800~2050년) | 수 분각 |
| 달   | 천문 연감 저정밀 식 + J2000 세차 보정 (지구 중심 — 일주시차 최대 약 1° 무시) | 경도 약 0.3° |
"""

from __future__ import annotations

import datetime as dt
import math

from astrowatch.astro.altaz import julian_day

DEG = math.pi / 180
AU_KM = 149597870.7
EARTH_RADIUS_KM = 6378.14
OBLIQUITY = 23.43928 * DEG

# [a(AU), e, I, L, 근일점 경도, 승교점 경도] 와 세기당 변화 — 황도 J2000
ELEMENTS: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "mercury": ((0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
                (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    "venus": ((0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    "earth": ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    "mars": ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    "jupiter": ((5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    "saturn": ((9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
               (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
    "uranus": ((19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
               (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589)),
    "neptune": ((30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
                (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664)),
}

# 키 → (한국어, 영어, 대략의 겉보기 등급)
BODIES = {
    "moon": ("달", "Moon", -12.0),
    "mercury": ("수성", "Mercury", 0.0),
    "venus": ("금성", "Venus", -4.2),
    "mars": ("화성", "Mars", 0.5),
    "jupiter": ("목성", "Jupiter", -2.4),
    "saturn": ("토성", "Saturn", 0.6),
    "uranus": ("천왕성", "Uranus", 5.7),
    "neptune": ("해왕성", "Neptune", 7.8),
}
PLANETS = ("mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune")


def centuries_j2000(t_utc: dt.datetime) -> float:
    return (julian_day(t_utc) - 2451545.0) / 36525


def _ecl_to_eq(v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    c, s = math.cos(OBLIQUITY), math.sin(OBLIQUITY)
    return x, y * c - z * s, y * s + z * c


def heliocentric(key: str, T: float) -> tuple[float, float, float]:
    """행성의 태양 중심 위치 (적도 J2000, AU)."""
    base, rate = ELEMENTS[key]
    a, e, inc, L, peri, node = (b + r * T for b, r in zip(base, rate))
    w = peri - node
    M = (L - peri) % 360
    if M > 180:
        M -= 360
    e_deg = e / DEG
    E = M + e_deg * math.sin(M * DEG)
    for _ in range(8):   # 케플러 방정식 (도 단위)
        dE = (M - (E - e_deg * math.sin(E * DEG))) / (1 - e * math.cos(E * DEG))
        E += dE
        if abs(dE) < 1e-7:
            break
    xp, yp = a * (math.cos(E * DEG) - e), a * math.sqrt(1 - e * e) * math.sin(E * DEG)
    cw, sw, cO, sO = math.cos(w * DEG), math.sin(w * DEG), math.cos(node * DEG), math.sin(node * DEG)
    cI, sI = math.cos(inc * DEG), math.sin(inc * DEG)
    return _ecl_to_eq((
        (cw * cO - sw * sO * cI) * xp + (-sw * cO - cw * sO * cI) * yp,
        (cw * sO + sw * cO * cI) * xp + (-sw * sO + cw * cO * cI) * yp,
        (sw * sI) * xp + (cw * sI) * yp,
    ))


def moon_geocentric(T: float) -> tuple[float, float, float]:
    """달의 지구 중심 위치 (적도, AU)."""
    def s(a: float, b: float) -> float:
        return math.sin((a + b * T) * DEG)

    def c(a: float, b: float) -> float:
        return math.cos((a + b * T) * DEG)

    lon = (218.32 + 481267.881 * T + 6.29 * s(135.0, 477198.87) - 1.27 * s(259.3, -413335.36)
           + 0.66 * s(235.7, 890534.22) + 0.21 * s(269.9, 954397.74) - 0.19 * s(357.5, 35999.05)
           - 0.11 * s(186.5, 966404.03))
    lat = (5.13 * s(93.3, 483202.02) + 0.28 * s(228.2, 960400.89) - 0.28 * s(318.3, 6003.15)
           - 0.17 * s(217.6, -407332.21))
    hp = (0.9508 + 0.0518 * c(135.0, 477198.87) + 0.0095 * c(259.3, -413335.36)
          + 0.0078 * c(235.7, 890534.22) + 0.0028 * c(269.9, 954397.74))
    r = (EARTH_RADIUS_KM / math.sin(hp * DEG)) / AU_KM
    l, b = (lon - 1.396971 * T) * DEG, lat * DEG   # 그날 황도 → J2000 (세차 보정)
    return _ecl_to_eq((r * math.cos(b) * math.cos(l), r * math.cos(b) * math.sin(l), r * math.sin(b)))


def radec(key: str, t_utc: dt.datetime) -> tuple[float, float]:
    """그 시각 지구 중심 (적경, 적위) 도."""
    T = centuries_j2000(t_utc)
    if key == "moon":
        v = moon_geocentric(T)
    else:
        h, earth = heliocentric(key, T), heliocentric("earth", T)
        v = (h[0] - earth[0], h[1] - earth[1], h[2] - earth[2])
    n = math.sqrt(sum(x * x for x in v))
    x, y, z = (x / n for x in v)
    return math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(max(-1.0, min(1.0, z))))


def find_body(name: str) -> str | None:
    """'목성'·'Jupiter' → 'jupiter'. 행성만 (달은 위상·월출이 박명 Tool 에 있으므로 조회 대상에서 뺀다)."""
    q = "".join((name or "").split()).lower()
    for key in PLANETS:
        ko, en, _ = BODIES[key]
        if q in (ko, en.lower(), key):
            return key
    return None
