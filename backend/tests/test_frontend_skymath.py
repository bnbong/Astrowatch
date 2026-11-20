"""frontend/js/skymath.js (하늘 가리키기 계산): node 로 실행해 서버 식과 같은지, 센서 각도와 안내가 맞는지 본다."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from astrowatch.astro.altaz import alt_az, local_sidereal_deg

SKYMATH_JS = Path(__file__).resolve().parents[2] / "frontend" / "js" / "skymath.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 없어 프론트 계산 검사를 건너뜀")


def _js(expr: str):
    """skymath.js 를 M 으로 불러 expr 의 값을 JSON 으로 돌려받는다."""
    script = f"const M = require(process.argv[1]); process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script, str(SKYMATH_JS)], capture_output=True, text=True,
                         encoding="utf-8", timeout=20, check=True)
    return json.loads(out.stdout)


def test_alt_az_matches_server():
    t = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
    cases = [(10.68471, 41.26875, 37.5665, 126.978), (101.287, -16.716, 35.18, 129.07), (230.0, 49.0, 33.5, 126.5)]
    js = _js("[" + ",".join(
        f"M.targetAltAz({ra},{dec},{lat},{lon},new Date('{t.isoformat()}'))" for ra, dec, lat, lon in cases) + "]")
    for (ra, dec, lat, lon), got in zip(cases, js):
        alt, az = alt_az(ra, dec, lat, local_sidereal_deg(t, lon))
        assert got["alt"] == pytest.approx(alt, abs=1e-6)
        assert got["az"] == pytest.approx(az, abs=1e-6)


@pytest.mark.parametrize(("alpha", "beta", "gamma", "screen", "alt", "az"), [
    (0, 90, 0, 0, 0, 0),          # 세로로 세운 휴대폰, 뒷면이 북쪽 지평선
    (0, 135, 0, 0, 45, 0),        # 위로 45도 들어 올림
    (90, 90, 0, 0, 0, 270),       # alpha 90 = 반시계 90도 = 서쪽
    (270, 120, 0, 0, 30, 90),     # 동쪽 위 30도
    (0, 90, 0, 90, 0, 0),         # 가로 화면이어도 가리키는 방향(뒷면)은 같다
])
def test_device_orientation_to_pointing(alpha, beta, gamma, screen, alt, az):
    got = _js(f"M.enuToAltAz(M.screenBasis(M.deviceBasis({alpha},{beta},{gamma}),{screen}).forward)")
    assert got["alt"] == pytest.approx(alt, abs=1e-6)
    assert (got["az"] - az + 180) % 360 - 180 == pytest.approx(0, abs=1e-6)


def test_screen_rotation_turns_up_vector():
    # 세로: 화면 위쪽 = 하늘. 가로(90): 화면 위쪽은 기기 x 축 방향
    up0 = _js("M.screenBasis(M.deviceBasis(0,90,0),0).up")
    up90 = _js("M.screenBasis(M.deviceBasis(0,90,0),90).up")
    assert up0 == pytest.approx([0, 0, 1], abs=1e-9)
    assert up90[2] == pytest.approx(0, abs=1e-9) and abs(up90[0]) == pytest.approx(1, abs=1e-9)


def test_ios_compass_heading_and_declination():
    assert _js("M.alphaFromCompass(90)") == 270
    assert _js("M.compassSource({webkitCompassHeading: 12, webkitCompassAccuracy: 5}).kind") == "ios"
    assert _js("M.compassSource({type: 'deviceorientationabsolute'}).kind") == "absolute"
    assert _js("M.compassSource({type: 'deviceorientation', absolute: false}).kind") == "relative"
    assert _js("M.declinationDeg(37.5, 127)") == pytest.approx(-8.9, abs=0.2)   # 서울 부근 서편 약 9도
    assert _js("M.declinationDeg(10, 10)") is None
    # 0/360 경계에서 평균이 180 으로 튀지 않는다
    assert _js("M.followOffset(359, 1, 1e9)") == pytest.approx(1, abs=1e-6)
    # 방위 보정은 방위만 더한다
    got = _js("M.enuToAltAz(M.rotateAz(M.basisFromAltAz(20, 10), 15).forward)")
    assert got["alt"] == pytest.approx(20) and got["az"] == pytest.approx(25)


def test_pointing_deltas_and_text():
    assert _js("M.deltas({alt: 40, az: 10}, {alt: 28, az: 350})") == pytest.approx({"dAz": 20, "dAlt": 12})
    assert _js("M.guideText(-35, 12)") == "왼쪽으로 35°, 위로 12°"
    assert _js("M.guideText(20, -7.6)") == "오른쪽으로 20°, 아래로 8°"
    assert _js("M.guideText(3, 30)") == "위로 30°"
    assert _js("M.guideText(3.9, -3.9)") == "여기예요"


def test_screen_arrow_direction():
    # 북쪽 지평선을 볼 때: 동쪽 목표는 오른쪽(0도), 위쪽 목표는 위(90도), 서쪽은 왼쪽(180도)
    right, up, left = _js("[[0,30],[30,0],[0,-30]].map(([alt, daz]) => M.guide(M.basisFromAltAz(0, 0), M.altAzToEnu(alt, M.mod360(daz))))")
    assert right["angle"] == pytest.approx(0, abs=1e-6) and right["sep"] == pytest.approx(30)
    assert up["angle"] == pytest.approx(90, abs=1e-6)
    assert abs(left["angle"]) == pytest.approx(180, abs=1e-6)
    assert _js("M.guide(M.basisFromAltAz(0, 0), M.altAzToEnu(0, 180)).behind") is True


def test_lock_hysteresis():
    # 4도 안에서 잠기고, 6도 밖으로 나가야 풀린다
    assert _js("[M.lockStep(false, 4.5), M.lockStep(false, 3.9), M.lockStep(true, 5.5), M.lockStep(true, 6.1)]") == [
        False, True, True, False]


def test_field_point_clamps_to_edge():
    inside = _js("M.fieldPoint(M.basisFromAltAz(0, 0), M.altAzToEnu(0, 22.5), 150, 45)")
    assert inside["inside"] and inside["x"] == pytest.approx(75) and inside["y"] == pytest.approx(0, abs=1e-9)
    above = _js("M.fieldPoint(M.basisFromAltAz(0, 0), M.altAzToEnu(30, 0), 150, 45)")
    assert above["y"] == pytest.approx(-100) and above["x"] == pytest.approx(0, abs=1e-9)   # SVG 는 y 가 아래로
    outside = _js("M.fieldPoint(M.basisFromAltAz(0, 0), M.altAzToEnu(0, 90), 150, 45)")
    assert not outside["inside"] and outside["x"] == pytest.approx(150)


def test_request_gate_ignores_stale_target_response():
    """R3: 달을 보다가 다른 천체로 바꾼 뒤 달의 늦은 응답(앞 세대)은 버리고, 지금 대상의 응답만 쓴다."""
    got = _js("""(() => {
        const g = M.requestGate();
        const moonLoad = g.next();
        const moonRefresh = g.current();          // 달 주기 갱신이 출발
        const marsLoad = g.next();                // 사용자가 화성으로 바꿈
        return {
            staleLoad: g.isCurrent(moonLoad),
            staleRefresh: M.acceptResponse(g, moonRefresh, "moon", "moon"),
            current: M.acceptResponse(g, marsLoad, null, "mars"),
            wrongKey: M.acceptResponse(g, g.current(), "mars", "moon"),
            refreshOk: M.acceptResponse(g, g.current(), "mars", "mars"),
        };
    })()""")
    assert got == {"staleLoad": False, "staleRefresh": False, "current": True, "wrongKey": False, "refreshOk": True}


def test_pointer_refresh_does_not_mutate_global_key():
    """R3: 주기 갱신이 전역 key 를 잠시 바꿨다 되돌리던 코드가 없어야 한다 (대상 키를 직접 넘긴다)."""
    src = (SKYMATH_JS.parent / "pointer.js").read_text(encoding="utf-8")
    body = src.split("async function refreshMoving()", 1)[1].split("\n  }\n", 1)[0]
    assert "key = " not in body and "targetUrl(true, cur.key)" in body
    assert "gate.isCurrent(token)" in src.split("async function loadTarget()", 1)[1].split("\n  }\n", 1)[0]
