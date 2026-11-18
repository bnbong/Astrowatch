"""천문현상 제목 → EventKind 규칙 분류기. LLM 판단이 필요 없는 변환이라 코드로 한다.

규칙 순서가 의미를 가진다: '달과 화성의 근접'은 행성명이 있어도 달 현상, '유성우'의 '유'는 행성 유가 아니다.
"""

from __future__ import annotations

import re

from astrowatch.tools.contracts import EventKind

PLANETS = ("수성", "금성", "화성", "목성", "토성", "천왕성", "해왕성", "명왕성")
SEASONS = ("동지", "하지", "춘분", "추분")
MOON_PHASES = ("합삭", "망", "상현", "하현", "삭")

_PLANET_TERMS = re.compile(r"(충|최대이각|유\s*\(|근접|정지)")
_MOON_TERMS = re.compile(r"(원지점|근지점)")


def classify(title: str) -> EventKind:
    t = (title or "").strip()
    if not t:
        return EventKind.OTHER
    if "유성우" in t:
        return EventKind.METEOR
    if "엄폐" in t:
        return EventKind.OCCULTATION
    if "식" in t:
        return EventKind.ECLIPSE
    if t.startswith("달과") or t.startswith("달의") or _MOON_TERMS.search(t) or t in MOON_PHASES:
        return EventKind.MOON
    if any(p in t for p in PLANETS) and _PLANET_TERMS.search(t):
        return EventKind.PLANET
    if "최대이각" in t:
        return EventKind.PLANET
    if any(s in t for s in SEASONS):
        return EventKind.SEASON
    return EventKind.OTHER


# 특정 날짜의 '현상'이 아니라 언제든 볼 수 있는 천체 (은하·성운·성단 등)
DEEP_SKY_TERMS = ("은하", "성운", "성단", "안드로메다", "플레이아데스", "좀생이", "오리온 대성운", "은하수")
_MESSIER = re.compile(r"(?<![A-Za-z])M\s?\d{1,3}(?!\d)|NGC\s?\d+", re.IGNORECASE)


def classify_query(text: str) -> EventKind:
    """사용자가 지목한 관측 대상 이름 → DEEP_SKY 또는 OTHER (KASI 현상 목록에 없을 때 쓴다)."""
    t = (text or "").strip()
    if not t:
        return EventKind.OTHER
    if any(k in t for k in DEEP_SKY_TERMS) or _MESSIER.search(t):
        return EventKind.DEEP_SKY
    return EventKind.OTHER

