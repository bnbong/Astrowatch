"""천체 카탈로그 조회 (astrowatch/data/sky_objects.json — scripts/build_sky_objects.py 로 생성).

Tool 등록과 분리해 두어, 실행 흐름(nodes)이 부작용 없이 이름만 확인할 수 있다.
"""

from __future__ import annotations

import difflib
import functools
import json
from pathlib import Path
from typing import Any

from astrowatch.astro import planets

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "sky_objects.json"

# 같은 점수면 이 순서로 고른다 — '안드로메다' 는 별자리보다 은하
_GROUP_RANK = {"messier": 0, "milky_way": 1, "star": 2, "constellation": 3}


def norm(text: str) -> str:
    return "".join((text or "").split()).lower()


@functools.lru_cache(maxsize=1)
def catalog() -> list[dict[str, Any]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return list(data.get("objects") or [])


# 이름 일부가 겹쳐도 천체가 아니라 '현상'을 가리키는 말
_PHENOMENON_WORDS = ("유성", "식", "엄폐", "근접", "충")


def _phenomenon(rest: str) -> bool:
    return any(w in rest for w in _PHENOMENON_WORDS)


MIN_PARTIAL = 3          # 부분 일치는 3글자 이상만


def _bounded(text: str, part: str) -> bool:
    """text 안의 part 가 식별자 경계를 지키는가 — 'm31' 은 'm310' 안에서 불일치 (숫자·영문이 이어지면 다른 이름)."""
    i = text.find(part)
    while i >= 0:
        before = text[i - 1] if i > 0 else ""
        after = text[i + len(part)] if i + len(part) < len(text) else ""
        ok_after = not (part[-1].isdigit() and after.isdigit())
        ok_before = not (part[0].isascii() and part[0].isalnum() and before.isascii() and before.isalnum())
        if ok_after and ok_before:
            return True
        i = text.find(part, i + 1)
    return False


def _match_len(q: str, names: set[str]) -> tuple[int, bool] | None:
    """(일치 길이, 정확 일치 여부). 일치하지 않으면 None."""
    if q in names:
        return len(q), True
    best = 0
    for a in names:
        if len(a) >= MIN_PARTIAL and a in q and _bounded(q, a) and not _phenomenon(q.replace(a, "")):
            best = max(best, len(a))           # 'm31안드로메다' ⊃ 'm31' ('쌍둥이자리 유성우' 는 제외)
        elif len(q) >= MIN_PARTIAL and q in a and _bounded(a, q):
            best = max(best, len(q))           # '안드로메다' ⊂ '안드로메다은하'
    return (best, False) if best else None


def match_object(name: str) -> tuple[dict[str, Any] | None, list[str]]:
    """이름 → (카탈로그 항목, 모호할 때 후보 이름들).

    가장 길게 일치한 이름 우선 → 정확 일치 우선 → 같은 길이면 메시에 > 은하수 > 별 > 별자리.
    정확 일치가 없고 같은 그룹에서 길이가 비슷한(±1) 후보가 둘 이상이면 모호함으로 본다.
    """
    q = norm(name)
    if not q:
        return None, []
    scored: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for o in catalog():
        names = set(o.get("aliases") or []) | {norm(o.get("name_ko") or ""), norm(o.get("name_en") or "")}
        names.discard("")
        m = _match_len(q, names)
        if m is None:
            continue
        length, exact = m
        rank = _GROUP_RANK.get(o.get("group", ""), 9)
        scored.append(((-length, 0 if exact else 1, rank, len(norm(o.get("name_ko") or ""))), o))
    if not scored:
        return None, []
    scored.sort(key=lambda x: x[0])
    top_key, top = scored[0]
    if top_key[1] == 1:
        rivals = [o for k, o in scored[1:] if k[2] == top_key[2] and abs(k[0] - top_key[0]) <= 1]
        if rivals:
            return None, list(dict.fromkeys(o["name_ko"] for o in [top, *rivals]))[:3]
    return top, []


def find_object(name: str) -> dict[str, Any] | None:
    """모호하거나 없으면 None."""
    return match_object(name)[0]


def similar_names(name: str, n: int = 3) -> list[str]:
    pool: dict[str, str] = {}
    for o in catalog():
        for label in (o.get("name_ko"), o.get("name_en")):
            if label:
                pool.setdefault(norm(label), o["name_ko"])
    for key in planets.PLANETS:
        pool.setdefault(planets.BODIES[key][0], planets.BODIES[key][0])
    hits = difflib.get_close_matches(norm(name), list(pool), n=n * 2, cutoff=0.3)
    return list(dict.fromkeys(pool[h] for h in hits))[:n]
