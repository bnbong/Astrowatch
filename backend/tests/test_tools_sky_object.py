"""lookup_sky_object — 카탈로그 매칭 · 고도 구간 · NOT_FOUND · never_rises."""

from __future__ import annotations

from astrowatch.astro import catalog
from astrowatch.tools import sky_object
from astrowatch.tools.base import ErrorCode, ToolKind

DAEJEON = {"lat": 36.3504, "lon": 127.3845, "date": "2026-11-18"}


def call(name: str, **kw):
    return sky_object.lookup_sky_object.invoke({"name": name, **DAEJEON, **kw})


def test_spec_is_local_tool():
    assert sky_object.lookup_sky_object.kind is ToolKind.LOCAL
    assert "언제 떠 있는지" in sky_object.lookup_sky_object.description


def test_andromeda_prefers_galaxy_over_constellation():
    for name in ("안드로메다", "M31", "andromeda galaxy", "안드로메다은하", "m 31"):
        r = call(name)
        assert r.ok, (name, r.error)
        assert r.data.object.id == "M31" and r.data.object.kind == "galaxy", name
    assert call("안드로메다자리").data.object.kind == "constellation"


def test_m31_windows_and_transit():
    d = call("안드로메다 은하").data
    assert not d.never_rises
    [w] = d.visible_windows
    assert w.start < 18 * 60 + 30 and 1440 + 180 <= w.end <= 1440 + 240
    assert 20 * 60 <= d.transit_minute <= 22 * 60 and d.transit_alt > 80
    assert d.object.constellation_ko == "안드로메다자리" and d.object.naked_eye is True


def test_polaris_alias():
    d = call("북극성").data
    assert d.object.kind == "star" and [(w.start, w.end) for w in d.visible_windows] == [(720, 2159)]


def test_not_found_with_suggestions():
    r = call("안드로매다 갤럭시")
    assert not r.ok and r.code is ErrorCode.NOT_FOUND
    assert "비슷한 이름" in r.error.message
    assert "갤럭시" not in r.error.message          # 입력 원문은 메시지에 넣지 않는다
    r2 = call("쌍둥이자리 유성우")           # 현상은 천체로 오인하지 않는다
    assert not r2.ok and r2.code is ErrorCode.NOT_FOUND


def test_southern_object_never_rises(monkeypatch):
    fake = [{"id": "X1", "group": "messier", "name_ko": "남쪽 시험 성단", "name_en": "South Test", "aliases": ["남쪽시험성단"],
             "kind": "cluster", "ra_deg": 100.0, "dec_deg": -70.0, "mag": 5.0, "naked_eye": True, "constellation_ko": None}]
    monkeypatch.setattr(catalog, "catalog", lambda: fake)
    r = call("남쪽 시험 성단")
    assert r.ok and r.data.never_rises and r.data.visible_windows == []
    assert "뜨지 않음" in r.note


def test_planet_lookup():
    r = call("목성")
    assert r.ok and r.data.object.kind == "planet" and r.data.object.name_en == "Jupiter"
    assert "저정밀" in r.note


def test_catalog_built_shape():
    objs = catalog.catalog()
    assert len(objs) > 250
    groups = {o["group"] for o in objs}
    assert groups == {"messier", "star", "constellation", "milky_way"}
    assert all(o["ra_deg"] is not None and o["dec_deg"] is not None for o in objs)


def test_name_matching_edge_cases():
    assert catalog.find_object("M310") is None                       # 식별자 경계: 뒤에 숫자가 이어지면 불일치
    assert catalog.find_object("안드로메다자리 관측")["kind"] == "constellation"   # 더 긴 일치 우선
    assert catalog.find_object("안드로메다")["id"] == "M31"
    assert catalog.find_object("안드로메다 은하")["id"] == "M31"
    assert catalog.find_object("은하") is None                       # 부분 일치는 3글자 이상


def test_ambiguous_name():
    obj, cands = catalog.match_object("메시에")
    assert obj is None and len(cands) == 3
    r = call("메시에")
    assert not r.ok and r.code is ErrorCode.NOT_FOUND and "모호" in r.error.message
