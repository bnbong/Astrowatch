"""하늘 가리키기 대상: 계획 제목에서 천체를 찾고, 그 천체의 지금 고도와 방위를 구한다.

| kind    | 출처                                      | 좌표                                   |
| object  | 카탈로그 (data/sky_objects.json)          | J2000 적경, 적위 (고정)                |
| planet  | astro/planets.py                          | 그 시각에 planets.radec 로 계산        |
| moon    | astro/planets.py                          | 그 시각 지구 중심 (일주시차 약 1도 무시) |
| radiant | 유성우 복사점 (data/meteor_radiants.json) | 극대일 근처 대략값 (하루 약 1도 이동 무시) |

키 규칙: 카탈로그는 한국어 이름(예: "안드로메다 은하"), 행성은 영어 키("mars"), 달은 "moon",
유성우는 복사점 키("geminids"). resolve_target 은 이 키 말고도 한국어, 영어 이름과 별칭을 받는다.
infer_target 은 맞는 것이 하나뿐일 때만 키를 돌려주고, 애매하면 None 이다 (추측하지 않는다).
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrowatch.astro import catalog, planets
from astrowatch.astro.altaz import alt_az, local_sidereal_deg

RADIANTS_PATH = Path(__file__).resolve().parents[1] / "data" / "meteor_radiants.json"

KIND_OBJECT, KIND_PLANET, KIND_MOON, KIND_RADIANT = "object", "planet", "moon", "radiant"
MOVING_KINDS = frozenset({KIND_PLANET, KIND_MOON})
MOON_KEY = "moon"
MOON_NAMES = ("달", "moon", "보름달", "슈퍼문")
_MOON_LEAD = re.compile(r"^(달|보름달|슈퍼문)(과|와|의|이|\s|$)")

_DIRS = ("북", "북동", "동", "남동", "남", "남서", "서", "북서")


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    kind: str                 # object | planet | moon | radiant
    ra: float                 # 도. 행성, 달은 at 시각 값
    dec: float
    subkind: str | None = None  # 카탈로그 kind(galaxy, star ...) 또는 유성우 IMO 코드
    at: dt.datetime | None = None  # 행성, 달 좌표를 계산한 시각 (UTC)

    @property
    def moving(self) -> bool:
        return self.kind in MOVING_KINDS


def compass(az: float) -> str:
    """방위(도) → 8방위 한국어."""
    return _DIRS[int(((az % 360) + 22.5) // 45) % 8]


def _utc(t: dt.datetime | None) -> dt.datetime:
    if t is None:
        return dt.datetime.now(dt.timezone.utc)
    if t.tzinfo is None:
        return t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


# ── 유성우 복사점 표 ─────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def radiants() -> list[dict[str, Any]]:
    data = json.loads(RADIANTS_PATH.read_text(encoding="utf-8"))
    return list(data.get("showers") or [])


def _shower_names(s: dict[str, Any]) -> set[str]:
    """정확 일치용 이름들 (키, 한국어 전체 이름, IMO 이름, IMO 코드)."""
    names = {s["key"], s["name_ko"], s["name_imo"], s.get("imo_code") or ""}
    return {catalog.norm(n) for n in names if n}


def _find_radiant(key: str) -> dict[str, Any] | None:
    q = catalog.norm(key)
    for s in radiants():
        if q in _shower_names(s):
            return s
    return None


def _match_shower(title: str, kind_hint: bool) -> dict[str, Any] | None:
    """제목 안에 유성우 이름이 들어 있으면 그 유성우. 같은 길이로 둘 이상 맞으면 None."""
    q = catalog.norm(title).replace("(", "").replace(")", "")
    meteorish = kind_hint or "유성" in q
    best: list[tuple[int, dict[str, Any]]] = []
    for s in radiants():
        top = 0
        for alias in [*s.get("aliases", []), s["name_ko"], s["name_imo"]]:
            a = catalog.norm(alias).replace("(", "").replace(")", "")
            if not a or a not in q:
                continue
            # 한국어 별칭('사자자리')만 맞으면 유성우라는 표시가 있어야 한다: 별자리 관측과 헷갈리지 않게
            if not meteorish and not a.isascii() and not a.endswith("유성우"):
                continue
            top = max(top, len(a))
        if top:
            best.append((top, s))
    if not best:
        return None
    best.sort(key=lambda x: -x[0])
    winners = {s["key"] for n, s in best if n == best[0][0]}
    return best[0][1] if len(winners) == 1 else None


def _radiant_target(s: dict[str, Any]) -> Target:
    return Target(key=s["key"], name=s["name_ko"], kind=KIND_RADIANT,
                  ra=float(s["ra_deg"]), dec=float(s["dec_deg"]), subkind=s.get("imo_code"))


# ── 행성, 달 ────────────────────────────────────────────────

def _body_key(name: str) -> str | None:
    q = catalog.norm(name)
    if q in {catalog.norm(n) for n in MOON_NAMES}:
        return MOON_KEY
    return planets.find_body(name)


def _body_target(key: str, at_utc: dt.datetime | None) -> Target:
    t = _utc(at_utc)
    ra, dec = planets.radec(key, t)
    ko = planets.BODIES[key][0]
    kind = KIND_MOON if key == MOON_KEY else KIND_PLANET
    return Target(key=key, name=ko, kind=kind, ra=ra, dec=dec, at=t)


# ── 카탈로그 ────────────────────────────────────────────────

def _catalog_exact(key: str) -> dict[str, Any] | None:
    q = catalog.norm(key)
    if not q:
        return None
    for o in catalog.catalog():
        names = set(o.get("aliases") or []) | {catalog.norm(o.get("name_ko") or ""),
                                                catalog.norm(o.get("name_en") or ""),
                                                catalog.norm(o.get("id") or "")}
        if q in names:
            return o
    return None


def _object_target(o: dict[str, Any]) -> Target | None:
    if o.get("ra_deg") is None or o.get("dec_deg") is None:
        return None
    return Target(key=o["name_ko"], name=o["name_ko"], kind=KIND_OBJECT,
                  ra=float(o["ra_deg"]), dec=float(o["dec_deg"]), subkind=o.get("kind"))


# ── 공개 함수 ───────────────────────────────────────────────

def resolve_target(key: str, at_utc: dt.datetime | None = None) -> Target | None:
    """키 또는 이름 → Target. 행성, 달은 at_utc(기본 지금) 좌표. 모르면 None.

    순서: 행성, 달 → 유성우 복사점(정확한 이름) → 카탈로그 정확 일치 → 카탈로그 유일 일치.
    """
    if not key or not key.strip():
        return None
    body = _body_key(key)
    if body:
        return _body_target(body, at_utc)
    shower = _find_radiant(key)
    if shower:
        return _radiant_target(shower)
    obj = _catalog_exact(key) or catalog.find_object(key)
    return _object_target(obj) if obj else None


def infer_target(event_title: str, event_kind: str | None = None) -> str | None:
    """계획 제목 → 대상 키. 맞는 것이 없거나 애매하면 None.

    '쌍둥이자리 유성우 극대기' → 'geminids', '화성 최대 접근' → 'mars', '안드로메다 은하 (M31)' → '안드로메다 은하'.
    """
    title = (event_title or "").strip()
    kind = (event_kind or "").strip().lower() or None
    if not title or kind == "season":
        return None
    q = catalog.norm(title)

    # 1) 유성우: 제목에 유성우가 있으면 복사점 표에서만 찾는다 (없는 유성우를 별자리로 바꾸지 않는다)
    if kind == "meteor" or "유성" in q:
        shower = _match_shower(title, kind_hint=kind == "meteor")
        return shower["key"] if shower else None
    if any(catalog.norm(s["name_imo"]) in q for s in radiants()):
        shower = _match_shower(title, kind_hint=True)
        if shower:
            return shower["key"]

    # 2) 행성, 달: 제목에 든 이름. 둘 이상이면 근접(합) 현상일 때만 처음 나온 행성
    hits: list[tuple[int, str]] = []
    for key in planets.PLANETS:
        ko, en, _ = planets.BODIES[key]
        for n in (ko, en.lower()):
            i = q.find(catalog.norm(n))
            if i >= 0:
                hits.append((i, key))
                break
    moonish = (kind == "moon" or "월식" in q or "보름달" in q or "슈퍼문" in q
               or _MOON_LEAD.match(title.strip()) is not None)
    if "일식" in q:
        return None                      # 태양은 가리키지 않는다 (눈 보호)
    hits.sort()
    planet_keys = list(dict.fromkeys(k for _, k in hits))
    if len(planet_keys) == 1:
        return planet_keys[0]
    if len(planet_keys) > 1:
        close = any(w in q for w in ("근접", "합", "만남", "나란히"))
        return planet_keys[0] if close else None
    if moonish:
        return MOON_KEY

    # 3) 카탈로그: catalog.match_object 가 모호하면 None 을 준다
    obj, _ambiguous = catalog.match_object(title)
    if obj is not None and obj.get("ra_deg") is not None:
        return str(obj["name_ko"])
    return None


def canonical_key(value: str | None) -> str | None:
    """사용자가 보낸 대상(이름, 별칭, 제목) → 저장할 키. 모르면 None."""
    if not value or not value.strip():
        return None
    target = resolve_target(value)
    if target is not None:
        return target.key
    return infer_target(value)


def position(target: Target, lat: float, lon: float, at_utc: dt.datetime | None = None) -> dict[str, Any]:
    """그 시각 관측지에서의 {alt, az, above_horizon, compass, ra, dec}. 행성, 달은 좌표를 다시 계산한다."""
    t = _utc(at_utc)
    ra, dec = (planets.radec(target.key, t) if target.moving else (target.ra, target.dec))
    alt, az = alt_az(ra, dec, lat, local_sidereal_deg(t, lon))
    return {"alt": round(alt, 2), "az": round(az, 2), "above_horizon": alt > 0,
            "compass": compass(az), "ra": round(ra, 4), "dec": round(dec, 4)}


def next_rise(target: Target, lat: float, lon: float, at_utc: dt.datetime | None = None,
              hours: int = 24, step_min: int = 10, horizon: float = 0.0) -> dt.datetime | None:
    """지금 지평선 아래라면 다음에 떠오르는 대략의 시각(UTC, step_min 분 단위). 이미 떠 있거나 24시간 안에 안 뜨면 None.

    행성, 달은 걸음마다 적경, 적위를 다시 계산한다 (달은 하루 약 13도 움직여 고정하면 40분 가까이 어긋난다).
    카탈로그 천체, 복사점은 고정 좌표. 지원 범위 밖 시각이라 datetime 이 넘치면 None.
    """
    t0 = _utc(at_utc)

    def alt(t: dt.datetime) -> float:
        ra, dec = planets.radec(target.key, t) if target.moving else (target.ra, target.dec)
        return alt_az(ra, dec, lat, local_sidereal_deg(t, lon))[0]

    try:
        if alt(t0) > horizon:
            return None
        for i in range(1, hours * 60 // step_min + 1):
            t = t0 + dt.timedelta(minutes=i * step_min)
            if alt(t) > horizon:
                return t
    except OverflowError:
        return None
    return None


_FEATURED = ("moon", "jupiter", "saturn", "mars", "venus", "안드로메다 은하", "플레이아데스 성단", "오리온 성운")


def _suggestion(t: Target) -> dict[str, str]:
    return {"key": t.key, "name": t.name, "kind": t.kind}


def search_targets(q: str, n: int = 8) -> list[dict[str, str]]:
    """고르기 목록: 이름 일부로 찾는다. 정확 > 앞부분 > 포함, 같으면 달, 행성 > 유성우 > 메시에 > 별 > 별자리."""
    query = catalog.norm(q or "")
    if not query:
        out = []
        for key in _FEATURED:
            t = resolve_target(key)
            if t is not None:
                out.append(_suggestion(t))
        return out[:n]

    scored: list[tuple[tuple[int, int, int], dict[str, str]]] = []

    def score(names: list[str]) -> int | None:
        best: int | None = None
        for raw in names:
            nm = catalog.norm(raw)
            if not nm:
                continue
            s = 0 if nm == query else 1 if nm.startswith(query) else 2 if query in nm else None
            if s is not None and (best is None or s < best):
                best = s
        return best

    for key in (MOON_KEY, *planets.PLANETS):
        ko, en, _ = planets.BODIES[key]
        s = score([ko, en, key])
        if s is not None:
            kind = KIND_MOON if key == MOON_KEY else KIND_PLANET
            scored.append(((s, 0, len(ko)), {"key": key, "name": ko, "kind": kind}))
    for sh in radiants():
        s = score([sh["name_ko"], sh["name_imo"], sh["key"], *sh.get("aliases", [])])
        if s is not None:
            scored.append(((s, 1, len(sh["name_ko"])), {"key": sh["key"], "name": sh["name_ko"], "kind": KIND_RADIANT}))
    group_rank = {"messier": 2, "milky_way": 3, "star": 4, "constellation": 5}
    for o in catalog.catalog():
        if o.get("ra_deg") is None:
            continue
        s = score([o.get("name_ko") or "", o.get("name_en") or "", o.get("id") or "", *(o.get("aliases") or [])])
        if s is not None:
            rank = group_rank.get(o.get("group", ""), 6)
            scored.append(((s, rank, len(o["name_ko"])), {"key": o["name_ko"], "name": o["name_ko"], "kind": KIND_OBJECT}))
    scored.sort(key=lambda x: x[0])
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for _k, item in scored:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        out.append(item)
        if len(out) >= n:
            break
    return out
