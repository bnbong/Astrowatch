"""하늘 가리키기 대상 (astro/targets.py): 이름 해석, 제목 추론, 고도 방위."""

from __future__ import annotations

import datetime as dt

import pytest

from astrowatch.astro import targets as T
from astrowatch.astro.altaz import local_sidereal_deg

UTC = dt.timezone.utc
AT = dt.datetime(2026, 12, 14, 13, 0, tzinfo=UTC)   # 12월 14일 22:00 KST


# ── resolve_target ───────────────────────────────────────────

@pytest.mark.parametrize("name", ["안드로메다 은하", "M31", "m31", "Andromeda Galaxy", "안드로메다은하"])
def test_resolve_catalog_object(name):
    t = T.resolve_target(name)
    assert t is not None
    assert (t.key, t.kind, t.subkind) == ("안드로메다 은하", "object", "galaxy")
    assert t.ra == pytest.approx(10.68471) and t.dec == pytest.approx(41.26875)
    assert not t.moving


@pytest.mark.parametrize("name", ["mars", "화성", "Mars"])
def test_resolve_planet_uses_time(name):
    t = T.resolve_target(name, AT)
    assert t is not None and (t.key, t.kind, t.name) == ("mars", "planet", "화성")
    assert t.moving and t.at == AT
    later = T.resolve_target(name, AT + dt.timedelta(days=30))
    assert (later.ra, later.dec) != (t.ra, t.dec)          # 행성은 시각마다 좌표가 바뀐다
    assert 0 <= t.ra < 360 and -30 < t.dec < 30            # 황도 근처


def test_resolve_moon():
    t = T.resolve_target("달", AT)
    assert t is not None and (t.key, t.kind) == ("moon", "moon")


@pytest.mark.parametrize("name", ["geminids", "쌍둥이자리 유성우", "Geminids", "GEM"])
def test_resolve_radiant(name):
    t = T.resolve_target(name)
    assert t is not None and (t.key, t.kind, t.subkind) == ("geminids", "radiant", "GEM")
    assert (t.ra, t.dec) == (112.0, 33.0)


def test_constellation_is_not_the_shower():
    t = T.resolve_target("쌍둥이자리")
    assert t is not None and t.kind == "object" and t.subkind == "constellation"


@pytest.mark.parametrize("name", ["", "   ", "없는천체이름", "xyz123"])
def test_resolve_unknown(name):
    assert T.resolve_target(name) is None


def test_radiant_table_has_major_showers():
    keys = {s["key"] for s in T.radiants()}
    assert {"quadrantids", "lyrids", "eta-aquariids", "southern-delta-aquariids", "perseids", "draconids",
            "orionids", "southern-taurids", "northern-taurids", "leonids", "geminids", "ursids"} <= keys
    for s in T.radiants():
        assert 0 <= s["ra_deg"] < 360 and -90 <= s["dec_deg"] <= 90
        assert s["name_ko"].endswith("유성우") and s["name_imo"] and len(s["active"]) == 2


# ── infer_target ─────────────────────────────────────────────

@pytest.mark.parametrize(("title", "kind", "expected"), [
    ("안드로메다 은하 (M31)", "deep_sky", "안드로메다 은하"),
    ("화성 최대 접근", "planet", "mars"),
    ("쌍둥이자리 유성우 극대기", "meteor", "geminids"),
    ("쌍둥이자리 유성우 극대", None, "geminids"),
    ("페르세우스자리 유성우", "meteor", "perseids"),
    ("물병자리 에타 유성우", "meteor", "eta-aquariids"),
    ("달과 화성의 근접", "moon", "mars"),        # 함께 보이는 행성 쪽
    ("개기월식", "eclipse", "moon"),
    ("목성과 토성 근접", "planet", "jupiter"),
])
def test_infer_target(title, kind, expected):
    assert T.infer_target(title, kind) == expected


@pytest.mark.parametrize(("title", "kind"), [
    ("알 수 없는 천문 현상", None),
    ("부분일식", "eclipse"),          # 태양은 가리키지 않는다
    ("추분", "season"),
    ("황소자리 유성우", "meteor"),     # 남쪽/북쪽이 애매하면 고르지 않는다
    ("기린자리 유성우", "meteor"),     # 표에 없는 유성우를 별자리로 바꾸지 않는다
    ("", None),
])
def test_infer_target_returns_none_instead_of_guessing(title, kind):
    assert T.infer_target(title, kind) is None


def test_canonical_key():
    assert T.canonical_key("M31") == "안드로메다 은하"
    assert T.canonical_key("쌍둥이자리 유성우 극대기") == "geminids"
    assert T.canonical_key("화성") == "mars"
    assert T.canonical_key("모르는 것") is None
    assert T.canonical_key(None) is None


# ── position ─────────────────────────────────────────────────

def _transit_lon(ra: float, at: dt.datetime) -> float:
    """그 시각에 적경 ra 가 남중하는 경도 (지방 항성시 = ra)."""
    lon = (ra - local_sidereal_deg(at, 0.0)) % 360
    return lon - 360 if lon > 180 else lon


@pytest.mark.parametrize("name", ["시리우스", "안드로메다 은하", "geminids"])
def test_position_at_transit(name):
    t = T.resolve_target(name)
    lat = 37.5665
    lon = _transit_lon(t.ra, AT)
    pos = T.position(t, lat, lon, AT)
    assert pos["alt"] == pytest.approx(90 - abs(lat - t.dec), abs=0.5)
    assert pos["above_horizon"] is True
    if t.dec < lat - 5:
        assert pos["az"] == pytest.approx(180, abs=1) and pos["compass"] == "남"


def test_position_below_horizon_and_rise():
    t = T.resolve_target("시리우스")
    lat = 37.5665
    lon = (_transit_lon(t.ra, AT) + 180 + 180) % 360 - 180   # 12시간 반대편 = 하위 남중
    pos = T.position(t, lat, lon, AT)
    assert pos["above_horizon"] is False and pos["alt"] < -30
    rise = T.next_rise(t, lat, lon, AT)
    assert rise is not None and AT < rise < AT + dt.timedelta(hours=12)
    assert T.position(t, lat, lon, rise)["alt"] > 0
    # 이미 떠 있으면 None
    assert T.next_rise(t, lat, _transit_lon(t.ra, AT), AT) is None


def test_circumpolar_never_rises_below():
    t = T.resolve_target("작은곰자리 유성우")     # 적위 +76: 서울에서 지지 않는다
    for h in range(0, 24, 3):
        assert T.position(t, 37.5665, 126.978, AT + dt.timedelta(hours=h))["above_horizon"]


def test_compass():
    assert [T.compass(a) for a in (0, 44, 46, 90, 180, 270, 337, 338, 359)] == ["북", "북동", "북동", "동", "남", "서", "북서", "북", "북"]


# ── search_targets ───────────────────────────────────────────

def test_search_targets():
    hits = T.search_targets("쌍둥")
    assert hits[0] == {"key": "geminids", "name": "쌍둥이자리 유성우", "kind": "radiant"}
    assert any(h["key"] == "쌍둥이자리" for h in hits)
    assert T.search_targets("목성")[0] == {"key": "jupiter", "name": "목성", "kind": "planet"}
    assert T.search_targets("m31")[0]["key"] == "안드로메다 은하"
    assert len(T.search_targets("자리", n=5)) == 5
    assert T.search_targets("없는이름xyz") == []
    featured = T.search_targets("")
    assert featured and featured[0]["key"] == "moon"
    for h in featured:
        assert T.resolve_target(h["key"]) is not None


def _brute_rise(t, lat, lon, t0, step_min=1, hours=24):
    """position() 을 걸음마다 불러 찾은 떠오르는 시각 (기준값)."""
    for i in range(1, hours * 60 // step_min + 1):
        ti = t0 + dt.timedelta(minutes=i * step_min)
        if T.position(t, lat, lon, ti)["alt"] > 0:
            return ti
    return None


def test_moon_next_rise_recomputes_position_each_step():
    """R4: 달은 하루 약 13도 움직인다. 걸음마다 좌표를 다시 계산해야 position() 기준과 10분 안에 맞는다."""
    t0 = dt.datetime(2026, 11, 4, 6, 0, tzinfo=UTC)
    lat, lon = 37.5665, 126.978
    moon = T.resolve_target("moon", t0)
    assert T.position(moon, lat, lon, t0)["above_horizon"] is False
    rise = T.next_rise(moon, lat, lon, t0)
    expected = _brute_rise(moon, lat, lon, t0)
    assert rise is not None and expected is not None
    assert abs((rise - expected).total_seconds()) <= 10 * 60


def test_next_rise_overflow_returns_none():
    """R5: datetime 범위 끝에서 넘쳐도 예외 대신 None."""
    t = T.resolve_target("시리우스")
    end = dt.datetime(9999, 12, 31, 20, 0, tzinfo=UTC)
    lon = (_transit_lon(t.ra, end) + 180 + 180) % 360 - 180      # 하위 남중: 지평선 아래에서 걸음을 시작
    assert T.position(t, 37.5665, lon, end)["above_horizon"] is False
    assert T.next_rise(t, 37.5665, lon, end) is None
