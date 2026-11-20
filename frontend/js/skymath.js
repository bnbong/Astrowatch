// 하늘 가리키기 계산 (DOM 없음, node 로 시험: backend/tests/test_frontend_skymath.py)
// VastHorizon 2회차 web/js/astro.js (항성시, 적경 적위 → 고도 방위) 와
// web/js/pointer.js (방향 센서 → 가리키는 방향, iOS webkitCompassHeading, 화면 회전, 평활화, 자기 편각) 에서 이식.
// 서버 astro/altaz.py 와 같은 식이다.
//
// 좌표계 (W3C DeviceOrientation)
//   지구: X 동쪽, Y 북쪽, Z 위쪽 (ENU). 벡터는 [동, 북, 위] 배열
//   기기: 화면을 세로로 둔 상태에서 x 오른쪽, y 위쪽, z 화면에서 사용자 쪽
//   회전: Z-X'-Y'' 순서, R = Rz(alpha) Rx(beta) Ry(gamma). R 의 열이 기기 x, y, z 축을 ENU 로 나타낸 벡터
// 가리키는 방향은 휴대폰 뒷면이 향하는 쪽(기기 -z). 화면을 보면서 휴대폰을 하늘로 들어 올리는 자세다.
(function (root) {
  "use strict";

  const DEG = Math.PI / 180;
  const LOCK_ENTER = 4;     // 이 각도 안이면 "여기예요"
  const LOCK_EXIT = 6;      // 잠긴 뒤에는 이 각도 밖으로 나가야 풀린다 (손떨림에 깜빡이지 않게)
  const SMOOTH_TAU_MS = 150;

  const mod360 = (x) => ((x % 360) + 360) % 360;
  const wrap180 = (x) => mod360(x + 180) - 180;
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const scale = (a, k) => a.map((v) => v * k);
  const add = (a, b) => a.map((v, i) => v + b[i]);
  const sub = (a, b) => a.map((v, i) => v - b[i]);
  const unit = (a) => { const n = Math.hypot(a[0], a[1], a[2]); return n ? scale(a, 1 / n) : a; };

  // ── 천문 (astro.js) ──
  function julianDay(date) {
    return date.getTime() / 86400000 + 2440587.5;
  }

  // 지방 항성시 (도)
  function localSiderealDeg(date, lonDeg) {
    const gmst = 280.46061837 + 360.98564736629 * (julianDay(date) - 2451545.0);
    return mod360(gmst + lonDeg);
  }

  // 적경 적위 → 고도 방위 (방위: 북 0도 → 동 90도)
  function altAz(raDeg, decDeg, latDeg, lstDeg) {
    const ha = (lstDeg - raDeg) * DEG, dec = decDeg * DEG, phi = latDeg * DEG;
    const sinAlt = Math.sin(dec) * Math.sin(phi) + Math.cos(dec) * Math.cos(phi) * Math.cos(ha);
    const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
    const az = Math.atan2(-Math.cos(dec) * Math.sin(ha),
      Math.sin(dec) * Math.cos(phi) - Math.cos(dec) * Math.sin(phi) * Math.cos(ha));
    return { alt: alt / DEG, az: mod360(az / DEG) };
  }

  // 그 시각, 그 장소에서의 고도 방위
  function targetAltAz(raDeg, decDeg, lat, lon, date) {
    return altAz(raDeg, decDeg, lat, localSiderealDeg(date, lon));
  }

  const DIRS = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"];
  function compass(azDeg) {
    return DIRS[Math.floor(mod360(azDeg + 22.5) / 45) % 8];
  }

  // ── 방향 센서 (pointer.js) ──
  // 기기 축의 ENU 벡터 { x, y, z }: 회전 행렬 R 의 세 열
  function deviceBasis(alphaDeg, betaDeg, gammaDeg) {
    const ca = Math.cos(alphaDeg * DEG), sa = Math.sin(alphaDeg * DEG);
    const cb = Math.cos(betaDeg * DEG), sb = Math.sin(betaDeg * DEG);
    const cg = Math.cos(gammaDeg * DEG), sg = Math.sin(gammaDeg * DEG);
    return {
      x: [ca * cg - sa * sb * sg, sa * cg + ca * sb * sg, -cb * sg],
      y: [-sa * cb, ca * cb, sb],
      z: [ca * sg + sa * sb * cg, sa * sg - ca * sb * cg, cb * cg],
    };
  }

  // 화면 기준 축 { right, up, forward }: 화면 회전(screen.orientation.angle)을 반영한다. forward 는 회전과 상관없다
  function screenBasis(basis, screenAngleDeg) {
    const th = (screenAngleDeg || 0) * DEG;
    const c = Math.cos(th), s = Math.sin(th);
    return {
      right: add(scale(basis.x, c), scale(basis.y, -s)),
      up: add(scale(basis.y, c), scale(basis.x, s)),
      forward: scale(basis.z, -1),
    };
  }

  // ENU 단위 벡터 → { alt, az }
  function enuToAltAz(v) {
    return { alt: Math.asin(Math.max(-1, Math.min(1, v[2]))) / DEG, az: mod360(Math.atan2(v[0], v[1]) / DEG) };
  }

  function altAzToEnu(alt, az) {
    const a = alt * DEG, z = az * DEG;
    return [Math.cos(a) * Math.sin(z), Math.cos(a) * Math.cos(z), Math.sin(a)];
  }

  // 시뮬레이션: 고도 방위만으로 화면 축을 만든다 (기울임 없음). right = forward x up
  function basisFromAltAz(alt, az) {
    const forward = altAzToEnu(alt, az);
    const up = altAzToEnu(alt + 90, az);
    return { right: cross(forward, up), up, forward };
  }

  // iOS webkitCompassHeading(자북 기준 시계 방향) → 북쪽 기준 alpha
  function alphaFromCompass(headingDeg) {
    return mod360(360 - headingDeg);
  }

  // 원형 저역 필터: 0/360 경계에서 튀지 않게 차이를 -180~180 으로 접어 따라간다
  function followOffset(prevDeg, measuredDeg, dtMs, tauMs) {
    if (prevDeg == null || !Number.isFinite(prevDeg)) return mod360(measuredDeg);
    const k = 1 - Math.exp(-Math.max(0, dtMs) / (tauMs || 2000));
    return mod360(prevDeg + k * wrap180(measuredDeg - prevDeg));
  }

  // 벡터 평활화: 가리키는 방향과 위쪽을 따로 따라가고, 위쪽을 다시 가리키는 방향에 수직으로 맞춘다
  function smoothBasis(prev, next, dtMs, tauMs) {
    if (!prev) return next;
    const k = 1 - Math.exp(-Math.max(0, dtMs) / (tauMs || SMOOTH_TAU_MS));
    const forward = unit(add(prev.forward, scale(sub(next.forward, prev.forward), k)));
    let up = unit(add(prev.up, scale(sub(next.up, prev.up), k)));
    up = unit(sub(up, scale(forward, dot(up, forward))));
    return { right: cross(forward, up), up, forward };
  }

  // 방위 보정: 모든 축을 위쪽 둘레로 돌려 방위만 deg 만큼 더한다 (iOS 기준점, 자기 편각)
  function rotateAz(basis, deg) {
    if (!deg) return basis;
    const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG);
    const rot = (v) => [v[0] * c + v[1] * s, -v[0] * s + v[1] * c, v[2]];
    const out = {};
    Object.keys(basis).forEach((k) => { out[k] = rot(basis[k]); });
    return out;
  }

  // 이벤트의 방위 출처: ios(webkitCompassHeading) | absolute(deviceorientationabsolute 또는 absolute: true) | relative(기준 없음)
  function compassSource(e) {
    const h = e && e.webkitCompassHeading;
    if (Number.isFinite(h) && h >= 0) {
      const acc = e.webkitCompassAccuracy;
      return { kind: "ios", heading: h, accuracy: Number.isFinite(acc) && acc >= 0 ? acc : null };
    }
    if (e && (e.type === "deviceorientationabsolute" || e.absolute === true)) return { kind: "absolute", heading: null, accuracy: null };
    return { kind: "relative", heading: null, accuracy: null };
  }

  // 자기 편각 (도, 동편 +): 한국, 일본 주변(위도 30~46, 경도 122~146)만 격자 값을 쌍선형 보간한다. 밖이면 null
  // 출처: NOAA NCEI Magnetic Field Calculators WMM-2025, 2026-10-27 기준 (VastHorizon pointer.js 와 같은 표)
  const DECL_LATS = [30, 34, 38, 42, 46], DECL_LONS = [122, 128, 134, 140, 146];
  const DECL_GRID = [
    [-6.3, -6.9, -6.8, -6.0, -4.7],
    [-7.4, -8.1, -8.1, -7.3, -6.0],
    [-8.4, -9.2, -9.3, -8.6, -7.4],
    [-9.5, -10.4, -10.5, -9.9, -8.6],
    [-10.7, -11.6, -11.7, -11.0, -9.8],
  ];
  function declinationDeg(lat, lon) {
    const at = (axis, v) => {
      if (!(v >= axis[0] && v <= axis[axis.length - 1])) return null;
      let i = 0;
      while (i < axis.length - 2 && v > axis[i + 1]) i++;
      return { i, t: (v - axis[i]) / (axis[i + 1] - axis[i]) };
    };
    const a = at(DECL_LATS, lat), b = at(DECL_LONS, lon);
    if (!a || !b) return null;
    const g = DECL_GRID;
    const top = g[a.i][b.i] * (1 - b.t) + g[a.i][b.i + 1] * b.t;
    const bottom = g[a.i + 1][b.i] * (1 - b.t) + g[a.i + 1][b.i + 1] * b.t;
    return top * (1 - a.t) + bottom * a.t;
  }

  // ── 안내 ──
  // 목표까지: 각거리 sep(도), 화면 화살표 각도 angle(도, 오른쪽 0, 위 90), 등 뒤인가
  function guide(basis, targetEnu) {
    const tx = dot(targetEnu, basis.right), ty = dot(targetEnu, basis.up), tz = dot(targetEnu, basis.forward);
    return { sep: Math.acos(Math.max(-1, Math.min(1, tz))) / DEG, angle: Math.atan2(ty, tx) / DEG, behind: tz < 0 };
  }

  // 방위 차이 (목표 - 지금, -180~180). + 면 오른쪽으로 돌아야 한다
  function azDelta(targetAz, nowAz) {
    return wrap180(targetAz - nowAz);
  }

  function deltas(target, now) {
    return { dAz: azDelta(target.az, now.az), dAlt: target.alt - now.alt };
  }

  // "왼쪽으로 35°, 위로 12°". LOCK_ENTER 미만인 축은 생략하고, 둘 다 생략되면 "여기예요"
  function guideText(dAz, dAlt, minDeg) {
    const m = minDeg == null ? LOCK_ENTER : minDeg;
    const parts = [];
    if (Math.abs(dAz) >= m) parts.push((dAz > 0 ? "오른쪽" : "왼쪽") + "으로 " + Math.round(Math.abs(dAz)) + "°");
    if (Math.abs(dAlt) >= m) parts.push((dAlt > 0 ? "위" : "아래") + "로 " + Math.round(Math.abs(dAlt)) + "°");
    return parts.length ? parts.join(", ") : "여기예요";
  }

  // 잠김 판정 (이력 현상): 각거리 LOCK_ENTER 안에 들어오면 잠기고, LOCK_EXIT 밖으로 나가야 풀린다
  function lockStep(locked, sep) {
    return locked ? sep <= LOCK_EXIT : sep < LOCK_ENTER;
  }

  // 원형 시야 안의 점 (방위 등거리 투영): 가운데가 가리키는 방향, 반지름 radiusPx 가 fovDeg.
  // 시야 밖이면 가장자리에 붙이고 inside: false
  function fieldPoint(basis, enu, radiusPx, fovDeg) {
    const g = guide(basis, enu);
    const r = Math.min(g.sep / fovDeg, 1) * radiusPx;
    return { x: r * Math.cos(g.angle * DEG), y: -r * Math.sin(g.angle * DEG), inside: g.sep <= fovDeg, sep: g.sep, angle: g.angle };
  }

  // 대상 요청의 세대 번호. 대상을 바꿀 때마다 next(), 응답이 왔을 때 그 요청의 번호가 지금 번호일 때만 쓴다.
  // 앞 대상(예: 달)의 늦은 응답이 지금 대상의 좌표를 덮어쓰지 않게 한다.
  function requestGate() {
    let gen = 0;
    return {
      next: () => ++gen,
      current: () => gen,
      isCurrent: (token) => token === gen,
    };
  }

  // 받은 응답을 쓸 것인가: 요청 세대가 지금이고, 기대한 키가 있으면 응답 키도 같아야 한다 (주기 갱신용)
  function acceptResponse(gate, token, expectedKey, gotKey) {
    if (!gate.isCurrent(token)) return false;
    return expectedKey == null || expectedKey === gotKey;
  }

  const api = {
    DEG, LOCK_ENTER, LOCK_EXIT, mod360, wrap180,
    julianDay, localSiderealDeg, altAz, targetAltAz, compass,
    deviceBasis, screenBasis, enuToAltAz, altAzToEnu, basisFromAltAz,
    alphaFromCompass, followOffset, smoothBasis, rotateAz, compassSource, declinationDeg,
    guide, azDelta, deltas, guideText, lockStep, fieldPoint,
    requestGate, acceptResponse,
  };
  root.SkyMath = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
