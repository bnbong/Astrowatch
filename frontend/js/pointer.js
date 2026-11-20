// 하늘 가리키기 화면: 저장한 계획의 천체 쪽으로 휴대폰을 안내한다.
// VastHorizon 2회차 web/js/pointerview.js 에서 이식 (센서 수신, iOS 권한, 화면 회전, 센서 없음 판정, 화면 꺼짐 방지).
// 계산은 skymath.js (VastHorizon web/js/pointer.js, astro.js 이식). 이 파일은 상태, 센서, 그리기만 맡는다.
//
// - 대상: /api/sky/target 으로 적경 적위를 한 번 받고, 고도 방위는 1초마다 여기서 다시 계산한다.
//   행성과 달만 60초마다 좌표를 다시 받는다 (refresh_s).
//   요청마다 세대 번호(SkyMath.requestGate)를 붙이고, 지금 세대가 아닌 늦은 응답은 버린다.
//   주기 갱신은 전역 key 를 바꾸지 않고 그때 대상의 키를 직접 넘긴다 (이름, 키, 좌표가 늘 같은 대상).
// - 센서: deviceorientationabsolute(Android Chrome, 북쪽 기준)를 먼저, 없으면 deviceorientation.
//   iOS 13+ 는 "센서 시작" 클릭 안에서 DeviceOrientationEvent.requestPermission 을 먼저 부른다.
//   iOS 는 alpha 가 시작 기준이라 webkitCompassHeading 으로 기준점을 천천히 따라간다 (시상수 2초).
//   나침반은 자북 기준이라 한국 주변은 자기 편각(SkyMath.declinationDeg)을 더한다.
// - 1.5초 안에 센서 값이 없으면 "미지원"으로 보고 시뮬레이션 모드를 켠다 (데스크톱).
// - 시뮬레이션: 조준경을 끌거나 방향키로 가리키는 방향을 바꾼다. URL 의 az, alt, at 으로 처음 상태를 정할 수 있다.
(function () {
  "use strict";

  const M = window.SkyMath;
  const esc = window.Fmt.escapeHtml;
  const DEG = M.DEG;
  const FIELD_R = 150;          // SVG 단위. 시야 반지름
  const FOV = 45;               // 시야 반지름이 나타내는 각도
  const C = 200;                // SVG 가운데
  const NO_SENSOR_MS = 1500;
  const COMPASS_TAU_MS = 2000;
  const LIVE_MIN_MS = 2000;     // 화면 읽기 프로그램에 안내를 다시 읽히는 최소 간격
  const SEOUL = { lat: 37.5665, lon: 126.978 };
  // 출처별 자기 편각 보정. iOS webkitCompassHeading 과 Android 절대 alpha 모두 자북 기준으로 본다 (VastHorizon 과 같은 가정)
  const DECLINATION_FOR = { ios: true, absolute: true };

  const KIND_LABEL = { object: "천체", planet: "행성", moon: "달", radiant: "유성우 복사점" };
  const SUBKIND_LABEL = {
    galaxy: "은하", star: "별", cluster: "성단", nebula: "성운", constellation: "별자리",
    milky_way: "은하수", dso: "딥스카이",
  };
  const LOC_LABEL = { plan: "계획 좌표", region: "계획 지역", default: "기본값", device: "현재 위치", query: "지정 좌표" };

  const $ = (id) => document.getElementById(id);
  const els = {
    name: $("pt-name"), kind: $("pt-kind"), plan: $("pt-plan"), msg: $("pt-msg"),
    guide: $("pt-guide"), live: $("pt-live"), stage: $("pt-stage"), hint: $("pt-hint"),
    ring: $("pt-ring"), ground: $("pt-ground"), horizon: $("pt-horizon"), horizonLabel: $("pt-horizon-label"),
    arrow: $("pt-arrow"), target: $("pt-target"), targetLabel: $("pt-target-label"),
    below: $("pt-below"), tAlt: $("pt-t-alt"), tAz: $("pt-t-az"), pAlt: $("pt-p-alt"), pAz: $("pt-p-az"),
    sep: $("pt-sep"), delta: $("pt-delta"),
    sensor: $("pt-sensor"), sensorDot: $("pt-sensor-dot"), loc: $("pt-loc"), locDot: $("pt-loc-dot"),
    sim: $("pt-sim"), simText: $("pt-sim-text"),
    sensorBtn: $("pt-sensor-btn"), geoBtn: $("pt-geo-btn"), pickBtn: $("pt-pick-btn"),
    picker: $("pt-picker"), q: $("pt-q"), list: $("pt-pick-list"), note: $("pt-note"), kindNote: $("pt-kind-note"),
  };

  // ── URL ──
  const params = new URLSearchParams(location.search);
  const planId = /^\d{1,9}$/.test(params.get("plan_id") || "") ? params.get("plan_id") : null;
  let key = (params.get("key") || "").slice(0, 100) || null;
  const atParam = params.get("at");
  const atBase = atParam && !isNaN(new Date(atParam)) ? new Date(atParam).getTime() : null;
  const startedAt = Date.now();
  // at 이 있으면 그 시각부터 흐르는 시계 (데모, 화면 캡처용)
  const now = () => new Date(atBase != null ? atBase + (Date.now() - startedAt) : Date.now());
  const num = (v, lo, hi, d) => { const n = Number(v); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : d; };

  // ── 상태 ──
  const state = {
    target: null,             // {key, name, kind, subkind, ra, dec, refresh_s, rises_at}
    pos: null,                // 지금 {alt, az}
    loc: null,                // {lat, lon, source, label}
    sim: false,
    simAz: num(params.get("az"), 0, 360, 180),
    simAlt: num(params.get("alt"), -30, 89, 30),
    sensor: "idle",           // idle | on | unsupported | denied
    source: null,             // ios | absolute | relative
    basis: null,              // 센서가 준 화면 축
    locked: false,
    liveText: "", liveAt: 0,
  };
  let lastStamp = 0, iosOffset = null, gotAbsolute = false, received = false;
  let noSensorTimer = 0, refreshTimer = 0, raf = 0, wakeLock = null, listening = false;
  const gate = M.requestGate();   // 대상 요청 세대 (늦은 응답 무시)

  // ── 표시 도우미 ──
  const fmtAngle = (v) => (v == null ? "-" : Math.round(v) + "°");
  const fmtAz = (az) => Math.round(M.mod360(az)) + "° " + M.compass(az);
  const hhmm = (d) => String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  function flash(text, kind) {
    if (!text) { els.msg.hidden = true; return; }
    els.msg.textContent = text;
    els.msg.className = "banner " + (kind === "bad" ? "banner-bad" : kind === "ok" ? "banner-ok" : "banner-cond");
    els.msg.hidden = false;
  }
  function setDot(el, cls) { el.className = "dot" + (cls ? " " + cls : ""); }

  // ── 나침반 고리 (정적 눈금, 방위 좌표로 그리고 그룹 전체를 돌린다) ──
  const NS = "http://www.w3.org/2000/svg";
  const ringLabels = [];
  let ringTarget = null;
  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach((k) => e.setAttribute(k, attrs[k]));
    if (parent) parent.appendChild(e);
    return e;
  }
  function buildRing() {
    for (let b = 0; b < 360; b += 10) {
      const major = b % 90 === 0, mid = b % 30 === 0;
      const r1 = 186, r2 = major ? 172 : mid ? 176 : 180;
      const a = (b - 90) * DEG;
      svgEl("line", {
        x1: (C + r1 * Math.cos(a)).toFixed(1), y1: (C + r1 * Math.sin(a)).toFixed(1),
        x2: (C + r2 * Math.cos(a)).toFixed(1), y2: (C + r2 * Math.sin(a)).toFixed(1),
        class: "pt-tick" + (major ? " is-major" : ""),
      }, els.ring);
    }
    [["북", 0], ["동", 90], ["남", 180], ["서", 270], ["북동", 45], ["남동", 135], ["남서", 225], ["북서", 315]].forEach(([t, b]) => {
      const a = (b - 90) * DEG, r = b % 90 === 0 ? 163 : 166;
      const x = C + r * Math.cos(a), y = C + r * Math.sin(a);
      const text = svgEl("text", { x: x.toFixed(1), y: y.toFixed(1), class: "pt-ring-label" + (b % 90 ? " is-minor" : "") + (b === 0 ? " is-north" : ""), "text-anchor": "middle", "dominant-baseline": "central" }, els.ring);
      text.textContent = t;
      ringLabels.push({ el: text, x, y });
    });
    ringTarget = svgEl("path", { d: "M200 30 L191 14 L209 14 Z", class: "pt-ring-target" }, els.ring);
  }

  // ── 지금 가리키는 방향 ──
  function currentBasis() {
    if (state.sim || !state.basis) return M.basisFromAltAz(state.simAlt, state.simAz);
    return state.basis;
  }
  const azKnown = () => state.sim || !state.basis || state.source === "ios" || state.source === "absolute";

  // ── 대상 위치 (1초마다) ──
  function recompute() {
    if (!state.target || !state.loc) return;
    state.pos = M.targetAltAz(state.target.ra, state.target.dec, state.loc.lat, state.loc.lon, now());
    kick();
  }

  // ── 그리기 ──
  function kick() { if (!raf) raf = requestAnimationFrame(draw); }

  // 지평선(고도 0 원)을 시야에 투영하고, 땅(고도 0 아래) 영역을 시야 가장자리 호로 닫는다.
  // 보는 방위의 반대편에서 한 바퀴 돌며 표본을 잡아, 시야 안 부분이 한 덩어리로 이어지게 한다
  function horizonPath(basis, me) {
    const pts = [];
    for (let i = 0; i <= 120; i++) {
      const az = me.az + 180 + i * 3;
      const p = M.fieldPoint(basis, M.altAzToEnu(0, az), FIELD_R, FOV);
      if (p.inside) pts.push(p);
      else if (pts.length) break;
    }
    if (pts.length < 2) {
      return { line: "", ground: me.alt < 0 ? FULL_FIELD : "", labelAt: null };
    }
    const xy = (p) => (C + p.x).toFixed(1) + " " + (C + p.y).toFixed(1);
    const line = "M" + pts.map(xy).join(" L");
    // 양 끝을 시야 가장자리로 밀어 낸 뒤, 천저 쪽으로 도는 호로 닫는다
    const edge = (p) => { const r = Math.hypot(p.x, p.y) || 1; return { x: p.x * FIELD_R / r, y: p.y * FIELD_R / r }; };
    const S = edge(pts[0]), E = edge(pts[pts.length - 1]);
    const nadir = M.fieldPoint(basis, [0, 0, -1], FIELD_R, FOV);
    const TAU = Math.PI * 2;
    const ang = (p) => Math.atan2(p.y, p.x);
    const span = ((ang(S) - ang(E)) % TAU + TAU) % TAU;
    const toN = ((ang(nadir) - ang(E)) % TAU + TAU) % TAU;
    const sweep = toN <= span ? 1 : 0;
    const arcSpan = sweep ? span : TAU - span;
    const ground = "M" + xy(S) + " L" + pts.map(xy).join(" L") + " L" + xy(E) +
      " A" + FIELD_R + " " + FIELD_R + " 0 " + (arcSpan > Math.PI ? 1 : 0) + " " + sweep + " " + xy(S) + " Z";
    let labelAt = pts[0];
    pts.forEach((p) => { if (Math.abs(p.x - 95) < Math.abs(labelAt.x - 95)) labelAt = p; });   // 가운데 조준과 겹치지 않게 오른쪽에
    return { line, ground, labelAt };
  }
  const FULL_FIELD = "M50 200 A150 150 0 1 0 350 200 A150 150 0 1 0 50 200 Z";

  function draw() {
    raf = 0;
    const basis = currentBasis();
    const me = M.enuToAltAz(basis.forward);
    // 고리: 내가 보는 방위가 위로
    const heading = me.az;
    els.ring.setAttribute("transform", "rotate(" + (-heading).toFixed(2) + " " + C + " " + C + ")");
    ringLabels.forEach((l) => l.el.setAttribute("transform", "rotate(" + heading.toFixed(2) + " " + l.x.toFixed(1) + " " + l.y.toFixed(1) + ")"));

    // 지평선과 땅 (시야 아래쪽을 옅게)
    const hz = horizonPath(basis, me);
    els.horizon.setAttribute("d", hz.line);
    if (hz.labelAt) {
      els.horizonLabel.style.display = "";
      els.horizonLabel.setAttribute("x", (C + hz.labelAt.x + 6).toFixed(1));
      els.horizonLabel.setAttribute("y", (C + hz.labelAt.y - 6).toFixed(1));
    } else {
      els.horizonLabel.style.display = "none";
    }
    els.ground.setAttribute("d", hz.ground);

    els.pAlt.textContent = "고도 " + fmtAngle(me.alt);
    els.pAz.textContent = "방위 " + fmtAz(me.az);

    const pos = state.pos;
    if (!pos) {
      els.arrow.style.display = "none";
      els.target.style.display = "none";
      ringTarget.style.display = "none";
      els.stage.classList.remove("is-locked");
      return;
    }
    ringTarget.style.display = azKnown() ? "" : "none";
    ringTarget.setAttribute("transform", "rotate(" + M.mod360(pos.az).toFixed(2) + " " + C + " " + C + ")");

    const enu = M.altAzToEnu(pos.alt, pos.az);
    const known = azKnown();
    const p = M.fieldPoint(basis, enu, FIELD_R, FOV);
    const d = M.deltas(pos, me);
    // 방위 기준이 없으면(relative) 위아래만 안내한다
    const sep = known ? p.sep : Math.abs(d.dAlt);
    state.locked = M.lockStep(state.locked, sep);
    const locked = state.locked;

    // 대상 점: 시야 안일 때만
    if (known && p.inside) {
      els.target.style.display = "";
      els.target.setAttribute("transform", "translate(" + (C + p.x).toFixed(1) + " " + (C + p.y).toFixed(1) + ")");
      els.targetLabel.setAttribute("x", p.x > 60 ? "-14" : "14");
      els.targetLabel.setAttribute("text-anchor", p.x > 60 ? "end" : "start");
    } else {
      els.target.style.display = "none";
    }
    // 화살표: 가운데에서 대상 쪽. 시야 안이면 점 바로 앞까지만
    const angle = known ? p.angle : (d.dAlt >= 0 ? 90 : -90);
    const dist = known && p.inside ? Math.hypot(p.x, p.y) : FIELD_R;
    const len = Math.min(100, dist - 24);
    if (!locked && len > 20) {
      els.arrow.style.display = "";
      els.arrow.setAttribute("transform", "translate(" + C + " " + C + ") rotate(" + (-angle).toFixed(1) + ") scale(" + (len / 100).toFixed(3) + " 1)");
    } else {
      els.arrow.style.display = "none";
    }
    els.stage.classList.toggle("is-locked", locked && pos.alt > 0);
    els.stage.classList.toggle("is-locked-below", locked && pos.alt <= 0);

    // 글 안내
    let text;
    if (!known) text = M.guideText(0, d.dAlt) + (locked ? "" : ", 방위는 알 수 없어요");
    else if (me.alt > 80 && !locked) text = "휴대폰을 조금 내려 화살표 쪽으로 기울여 보세요";
    else text = M.guideText(d.dAz, d.dAlt);
    if (locked && pos.alt <= 0) text = "방향은 맞았지만 지금은 지평선 아래예요";
    if (els.guide.textContent !== text) els.guide.textContent = text;
    els.guide.classList.toggle("is-locked", locked && pos.alt > 0);
    announce(state.target.name + ": " + text);

    els.sep.textContent = known ? fmtAngle(p.sep) : "-";
    els.delta.textContent = known ? "방위 " + signed(d.dAz) + ", 고도 " + signed(d.dAlt) : "고도 " + signed(d.dAlt);
    els.tAlt.textContent = "고도 " + fmtAngle(pos.alt);
    els.tAz.textContent = "방위 " + fmtAz(pos.az);
    renderBelow(pos);
  }
  const signed = (v) => (v >= 0 ? "+" : "−") + Math.abs(Math.round(v)) + "°";

  function announce(text) {
    const t = Date.now();
    if (text === state.liveText || t - state.liveAt < LIVE_MIN_MS) return;
    state.liveText = text;
    state.liveAt = t;
    els.live.textContent = text;
  }

  function renderBelow(pos) {
    if (pos.alt > 0) { els.below.hidden = true; return; }
    const r = state.target.rises_at ? new Date(state.target.rises_at) : null;
    const text = r && r > now() ? "지금은 지평선 아래, " + hhmm(r) + " 이후 떠오름" : "지금은 지평선 아래예요";
    if (els.below.textContent !== text) els.below.textContent = text;
    els.below.hidden = false;
  }

  // ── 대상 불러오기 ──
  function targetUrl(withTime, targetKey) {
    const q = new URLSearchParams();
    if (targetKey) q.set("key", targetKey);
    if (planId) q.set("plan_id", planId);
    if (state.loc && (state.loc.source === "device" || state.loc.source === "query")) {
      q.set("lat", state.loc.lat.toFixed(4));
      q.set("lon", state.loc.lon.toFixed(4));
    }
    if (withTime && atBase != null) q.set("at", now().toISOString());
    return "/api/sky/target?" + q.toString();
  }

  async function loadTarget() {
    const token = gate.next();          // 앞 요청과 진행 중인 갱신은 이제 지난 세대
    clearInterval(refreshTimer);
    if (!key && !planId) {
      showNoTarget("가리킬 천체를 고르세요.");
      return;
    }
    try {
      const t = await window.Api.get(targetUrl(true, key));
      if (!gate.isCurrent(token)) return;
      applyTarget(t);
    } catch (e) {
      if (!gate.isCurrent(token)) return;
      if (e.status === 404) showNoTarget(planId && !key ? "이 계획에는 가리킬 천체가 없어요. 아래에서 천체를 골라 주세요." : "그 천체를 찾지 못했어요. 다른 이름으로 찾아 보세요.");
      else flash(e.message, "bad");
    }
  }

  function applyTarget(t) {
    state.target = t;
    if (!state.loc || (state.loc.source !== "device" && state.loc.source !== "query")) {
      state.loc = { lat: t.lat, lon: t.lon, source: t.location_source, label: t.location_label };
    }
    flash("");
    els.name.textContent = t.name;
    document.title = "Astrowatch | " + t.name + " 가리키기";
    els.kind.textContent = (t.kind === "object" && SUBKIND_LABEL[t.subkind]) || KIND_LABEL[t.kind] || t.kind;
    els.kind.hidden = false;
    els.targetLabel.textContent = t.name;
    els.plan.textContent = t.plan_title ? "계획: " + t.plan_title : "직접 고른 천체";
    els.kindNote.hidden = t.kind !== "radiant" && t.kind !== "moon";
    els.kindNote.textContent = t.kind === "radiant"
      ? "유성은 복사점에서 사방으로 퍼져 나가요. 복사점 둘레의 넓은 하늘을 함께 보세요. 복사점 좌표는 극대일 근처의 대략값입니다."
      : t.kind === "moon" ? "달 위치는 지구 중심 기준이라 최대 1° 정도 어긋날 수 있어요." : "";
    renderLoc();
    clearInterval(refreshTimer);
    if (t.refresh_s) refreshTimer = setInterval(refreshMoving, t.refresh_s * 1000);
    recompute();
  }

  async function refreshMoving() {
    const cur = state.target;
    if (!cur) return;
    const token = gate.current();
    try {
      const t = await window.Api.get(targetUrl(true, cur.key));
      // 그 사이 대상이 바뀌었으면(새 세대, 다른 대상, 다른 키) 버린다
      if (state.target !== cur || !M.acceptResponse(gate, token, cur.key, t.key)) return;
      cur.ra = t.ra;
      cur.dec = t.dec;
      cur.rises_at = t.rises_at;
      recompute();
    } catch (_) { /* 다음 주기에 다시 */ }
  }

  function showNoTarget(text) {
    state.target = null;
    state.pos = null;
    els.name.textContent = "하늘 가리키기";
    els.kind.hidden = true;
    els.plan.textContent = text;
    els.guide.textContent = "가리킬 천체가 없어요";
    els.below.hidden = true;
    ["tAlt", "tAz", "sep", "delta"].forEach((k) => { els[k].textContent = "-"; });
    openPicker(true);
    kick();
  }

  function renderLoc() {
    const l = state.loc;
    if (!l) return;
    const base = LOC_LABEL[l.source] || l.source;
    const extra = l.source === "default" ? " (서울)" : l.label ? " (" + l.label + ")" : "";
    els.loc.textContent = base + extra + " " + l.lat.toFixed(2) + ", " + l.lon.toFixed(2);
    setDot(els.locDot, l.source === "device" || l.source === "plan" ? "dot-ok" : "dot-cond");
  }

  // ── 시뮬레이션 ──
  function setSim(on) {
    state.sim = !!on;
    els.sim.setAttribute("aria-checked", String(state.sim));
    els.simText.textContent = state.sim ? "켜짐" : "꺼짐";
    els.stage.classList.toggle("is-sim", state.sim);
    els.hint.textContent = state.sim
      ? "시뮬레이션 모드: 조준경을 끌거나 방향키로 돌려 보세요 (Shift 는 크게)"
      : state.sensor === "on" ? "화면을 보면서 휴대폰을 하늘로 들어 올리세요" : "\"센서 시작\"을 누르면 휴대폰 방향으로 안내합니다";
    kick();
  }

  function turn(dAz, dAlt) {
    state.simAz = M.mod360(state.simAz + dAz);
    state.simAlt = Math.max(-30, Math.min(89, state.simAlt + dAlt));
    kick();
  }

  let drag = null;
  els.stage.addEventListener("pointerdown", (e) => {
    if (!state.sim) return;
    drag = { x: e.clientX, y: e.clientY, id: e.pointerId };
    els.stage.setPointerCapture(e.pointerId);
    els.stage.classList.add("is-dragging");
  });
  els.stage.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    // 하늘을 손으로 끄는 느낌: 시야 반지름(px) 만큼 끌면 FOV 만큼 돈다
    const radiusPx = els.stage.clientWidth * (FIELD_R / 400);
    const k = FOV / Math.max(60, radiusPx);
    turn(-(e.clientX - drag.x) * k, (e.clientY - drag.y) * k);
    drag.x = e.clientX;
    drag.y = e.clientY;
  });
  const endDrag = () => { drag = null; els.stage.classList.remove("is-dragging"); };
  els.stage.addEventListener("pointerup", endDrag);
  els.stage.addEventListener("pointercancel", endDrag);

  const KEYS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
  document.addEventListener("keydown", (e) => {
    if (!state.sim || !KEYS[e.key]) return;
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    const step = e.shiftKey ? 10 : 2;
    turn(KEYS[e.key][0] * step, KEYS[e.key][1] * step);
  });
  els.sim.addEventListener("click", () => setSim(!state.sim));

  // ── 센서 ──
  const screenAngle = () => (screen.orientation && typeof screen.orientation.angle === "number"
    ? screen.orientation.angle : typeof window.orientation === "number" ? window.orientation : 0);

  function onOrientation(e) {
    if (e.alpha == null || e.beta == null || e.gamma == null) return;
    const src = M.compassSource(e);
    if (src.kind === "absolute") gotAbsolute = true;
    else if (gotAbsolute) return;               // 절대 방위가 오면 상대 이벤트는 쓰지 않는다
    const stamp = e.timeStamp || performance.now();
    const dt = lastStamp ? Math.max(0, stamp - lastStamp) : 0;
    lastStamp = stamp;
    if (!received) {
      received = true;
      clearTimeout(noSensorTimer);
      setSensor("on");
      setSim(false);
    }
    let corr = 0;
    if (src.kind === "ios") {
      iosOffset = M.followOffset(iosOffset, M.mod360(e.alpha - M.alphaFromCompass(src.heading)), dt, COMPASS_TAU_MS);
      corr = iosOffset;
    }
    const loc = state.loc || SEOUL;
    const decl = DECLINATION_FOR[src.kind] ? M.declinationDeg(loc.lat, loc.lon) : null;
    corr += decl || 0;
    if (state.source !== src.kind) {
      state.source = src.kind;
      setSensor("on");
    }
    const next = M.rotateAz(M.screenBasis(M.deviceBasis(e.alpha, e.beta, e.gamma), screenAngle()), corr);
    state.basis = M.smoothBasis(state.basis, next, dt);
    if (!state.sim) kick();
  }

  function setSensor(s) {
    state.sensor = s;
    const text = {
      idle: "대기",
      on: state.source === "relative" ? "켜짐 (방위 기준 없음, 위아래만 안내)" : state.source === "ios" ? "켜짐 (나침반)" : "켜짐",
      unsupported: "미지원",
      denied: "권한 거부",
    }[s];
    els.sensor.textContent = text;
    setDot(els.sensorDot, s === "on" ? (state.source === "relative" ? "dot-cond" : "dot-ok") : s === "idle" ? "" : "dot-bad");
    els.sensorBtn.textContent = s === "on" ? "센서 켜짐" : "센서 시작";
    els.sensorBtn.disabled = s === "on";
  }

  const EVENTS = ["deviceorientationabsolute", "deviceorientation"];
  function listen() {
    if (listening) return;
    listening = true;
    received = false;
    lastStamp = 0;
    EVENTS.forEach((ev) => window.addEventListener(ev, onOrientation));
    clearTimeout(noSensorTimer);
    noSensorTimer = setTimeout(() => {
      if (received) return;
      unlisten();
      setSensor("unsupported");
      els.note.textContent = "이 기기에서는 방향 센서 값을 받지 못했어요. 시뮬레이션 모드로 방향을 바꿔 볼 수 있습니다.";
      setSim(true);
    }, NO_SENSOR_MS);
    try {
      if (navigator.wakeLock && navigator.wakeLock.request) {
        navigator.wakeLock.request("screen").then((l) => { wakeLock = l; }).catch(() => {});
      }
    } catch (_) { /* 화면 꺼짐 방지는 선택 */ }
  }
  function unlisten() {
    listening = false;
    EVENTS.forEach((ev) => window.removeEventListener(ev, onOrientation));
    clearTimeout(noSensorTimer);
    if (wakeLock && wakeLock.release) wakeLock.release().catch(() => {});
    wakeLock = null;
  }

  // iOS 13+: 사용자 제스처 안의 첫 비동기 호출이어야 권한 창이 뜬다. 다른 브라우저는 묻지 않는다
  function requestOrientation() {
    const DOE = window.DeviceOrientationEvent;
    if (!DOE || typeof DOE.requestPermission !== "function") return Promise.resolve("granted");
    try { return DOE.requestPermission().catch(() => "denied"); } catch (_) { return Promise.resolve("denied"); }
  }

  els.sensorBtn.addEventListener("click", () => {
    const permission = requestOrientation();   // 클릭 핸들러에서 곧바로
    if (!window.isSecureContext) {
      setSensor("unsupported");
      els.note.textContent = "방향 센서와 현재 위치는 HTTPS 또는 localhost 에서만 쓸 수 있어요. docs/SETUP.md 의 \"휴대폰에서 하늘 가리키기 테스트\"를 참고하세요.";
      setSim(true);
      return;
    }
    if (!("DeviceOrientationEvent" in window)) {
      setSensor("unsupported");
      setSim(true);
      return;
    }
    permission.then((p) => {
      if (p === "granted") listen();
      else {
        setSensor("denied");
        els.note.textContent = "동작 및 방향 접근이 허용되지 않았어요. 브라우저 설정에서 이 사이트의 동작 및 방향 접근을 허용한 뒤 다시 눌러 주세요.";
        setSim(true);
      }
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { if (listening) { unlisten(); state.pausedSensor = true; } }
    else if (state.pausedSensor) { state.pausedSensor = false; listen(); }
  });
  window.addEventListener("resize", kick);

  // ── 현재 위치 ──
  els.geoBtn.addEventListener("click", () => {
    if (!navigator.geolocation || !window.isSecureContext) {
      flash("이 환경에서는 현재 위치를 쓸 수 없어 계획 위치로 계산합니다. (HTTPS 또는 localhost 필요)");
      return;
    }
    els.geoBtn.disabled = true;
    els.geoBtn.textContent = "위치 확인 중…";
    navigator.geolocation.getCurrentPosition((p) => {
      els.geoBtn.disabled = false;
      els.geoBtn.textContent = "현재 위치 사용";
      state.loc = { lat: p.coords.latitude, lon: p.coords.longitude, source: "device", label: null };
      renderLoc();
      flash("현재 위치로 계산합니다.", "ok");
      if (state.target) loadTarget(); else recompute();
    }, () => {
      els.geoBtn.disabled = false;
      els.geoBtn.textContent = "현재 위치 사용";
      flash("현재 위치를 받지 못했어요. " + (state.loc ? LOC_LABEL[state.loc.source] + "로 계속 계산합니다." : ""));
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
  });

  // ── 다른 천체 ──
  let searchTimer = 0, searchSeq = 0;
  function openPicker(open) {
    els.picker.hidden = !open;
    els.pickBtn.setAttribute("aria-expanded", String(open));
    if (open) { search(els.q.value); }
  }
  async function search(q) {
    const seq = ++searchSeq;
    try {
      const list = await window.Api.get("/api/sky/targets?q=" + encodeURIComponent(String(q || "").slice(0, 50)));
      if (seq !== searchSeq) return;
      els.list.innerHTML = list.length
        ? list.map((s) => '<li><button type="button" class="pt-pick" data-key="' + esc(s.key) + '"><span>' + esc(s.name) +
            '</span><small class="muted">' + esc(KIND_LABEL[s.kind] || s.kind) + "</small></button></li>").join("")
        : '<li class="muted pt-pick-empty">찾은 천체가 없어요.</li>';
    } catch (e) {
      els.list.innerHTML = '<li class="muted pt-pick-empty">' + esc(e.message) + "</li>";
    }
  }
  els.pickBtn.addEventListener("click", () => {
    const open = els.picker.hidden;
    openPicker(open);
    if (open) els.q.focus();
  });
  els.q.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => search(els.q.value), 200);
  });
  els.picker.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.target) { openPicker(false); els.pickBtn.focus(); }
  });
  els.list.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-key]");
    if (!b) return;
    key = b.dataset.key;
    const u = new URL(location.href);
    u.searchParams.set("key", key);
    history.replaceState(null, "", u.pathname + u.search);
    state.locked = false;
    openPicker(false);
    loadTarget();
  });

  // ── 시작 ──
  buildRing();
  const handheld = window.matchMedia("(pointer: coarse)").matches && "DeviceOrientationEvent" in window;
  if (!("DeviceOrientationEvent" in window)) setSensor("unsupported"); else setSensor("idle");
  // 데스크톱(센서 없음)은 시뮬레이션으로 시작. 휴대폰은 "센서 시작"을 기다린다
  setSim(!handheld || params.get("sim") === "1");
  if (!window.isSecureContext) {
    els.note.textContent = "지금 주소는 보안 연결이 아니라 휴대폰 센서와 현재 위치를 쓸 수 없어요. HTTPS 또는 localhost 로 열어 주세요 (docs/SETUP.md 참고).";
  }
  loadTarget();
  setInterval(recompute, 1000);
  kick();

  // 수동 확인용
  window.PointerView = { state, turn, setSim };
})();
