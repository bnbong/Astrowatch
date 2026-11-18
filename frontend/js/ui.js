// 공통 UI: inline SVG 아이콘 · 판정 배지 · 밤 타임바 · 레일 연결 상태.
// 이모지는 쓰지 않는다. 아이콘은 모두 stroke SVG 문자열이며 aria-hidden 이다.
(function () {
  "use strict";

  const esc = window.Fmt.escapeHtml;
  const ml = window.Fmt.minuteLabel;

  // ── 아이콘 ──
  function svg(size, body, extra) {
    return '<svg class="svg-i" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="' + ((extra && extra.sw) || 2) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + "</svg>";
  }
  const Icons = {
    mark(size) {
      const s = size || 30;
      return '<svg class="svg-i" width="' + s + '" height="' + s + '" viewBox="0 0 30 30" aria-hidden="true">' +
        '<circle cx="15" cy="15" r="13" fill="none" stroke="#1B2A4A" stroke-width="' + (s < 20 ? 2 : 1.5) + '" stroke-dasharray="3 4"></circle>' +
        '<circle cx="15" cy="15" r="5.5" fill="#1B2A4A"></circle>' +
        (s >= 20 ? '<circle cx="24" cy="8" r="2" fill="#B8731B"></circle>' : "") + "</svg>";
    },
    check: (s) => svg(s || 12, '<path d="M20 6 9 17l-5-5"></path>', { sw: 3 }),
    x: (s) => svg(s || 12, '<path d="M18 6 6 18M6 6l12 12"></path>', { sw: 3 }),
    triangle(s) {
      const n = s || 12;
      return '<svg class="svg-i" width="' + n + '" height="' + n + '" viewBox="0 0 12 12" aria-hidden="true">' +
        '<path d="M6 1 L11 10 H1 Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"></path></svg>';
    },
    question: (s) => svg(s || 12, '<circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01"></path>', { sw: 2.4 }),
    info: (s) => svg(s || 16, '<circle cx="12" cy="12" r="10"></circle><path d="M12 8h.01M11 12h1v5h1"></path>'),
    alert: (s) => svg(s || 16, '<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path>'),
    horizon: (s) => svg(s || 16, '<path d="M3 20h18M5 20V10l7-6 7 6v10"></path>'),
    lock: (s) => svg(s || 16, '<rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path>'),
    loop: (s) => svg(s || 16, '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"></path><path d="M21 3v5h-5"></path>'),
    close: (s) => svg(s || 18, '<path d="M18 6 6 18M6 6l12 12"></path>'),
    chevronUp: (s) => svg(s || 16, '<path d="m18 15-6-6-6 6"></path>'),
    send: (s) => svg(s || 20, '<path d="M5 12h14M13 6l6 6-6 6"></path>', { sw: 2.2 }),
    calendar: (s) => svg(s || 16, '<rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M16 2v4M8 2v4M3 10h18"></path>'),
    pin: (s) => svg(s || 16, '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"></path><circle cx="12" cy="10" r="2.5"></circle>'),
    external: (s) => svg(s || 12, '<path d="M14 4h6v6M20 4 10 14M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"></path>'),
    plus: (s) => svg(s || 16, '<path d="M12 5v14M5 12h14"></path>'),
    clock: (s) => svg(s || 14, '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>'),
    refresh: (s) => svg(s || 16, '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"></path><path d="M21 3v5h-5"></path>'),
  };

  // ── 판정 배지 (색 + 모양) ──
  const VERDICT = {
    fit: { text: "충족", short: "충족", cls: "badge-fit", icon: () => Icons.check(11) },
    conditional: { text: "조건부 충족", short: "조건부", cls: "badge-cond", icon: () => Icons.triangle(11) },
    unfit: { text: "불충족", short: "불충족", cls: "badge-unfit", icon: () => Icons.x(11) },
    unjudged: { text: "판단 안 함", short: "판단 안 함", cls: "badge-unjudged", icon: () => Icons.question(11) },
  };
  function verdictBadge(verdict) {
    const v = VERDICT[verdict] || { text: String(verdict || "-"), cls: "badge-unjudged", icon: () => "" };
    return '<span class="badge ' + v.cls + '">' + v.icon() + esc(v.text) + "</span>";
  }
  function verdictText(verdict) {
    return (VERDICT[verdict] || { text: String(verdict || "-") }).text;
  }

  // ── 밤 타임바 ──
  // 트랙은 18:00(1080) → 06:00+1(1800) 고정. 분은 저녁 기준 날짜 00:00 부터(24시 이후 1440+).
  const T0 = 1080;
  const T1 = 1800;
  const SPAN = T1 - T0;
  const TICKS = ["18:00", "21:00", "00:00", "03:00", "06:00"];
  const TICKS_SHORT = ["18", "21", "00", "03", "06"];
  const BAD_PRECIP = { rain: 1, snow: 1, sleet: 1, shower: 1 };

  function pct(m) {
    return Math.max(0, Math.min(100, ((m - T0) / SPAN) * 100));
  }
  // 범위 밖은 잘라 낸다. 겹치는 부분이 없으면 null.
  function clip(win) {
    if (!win || win.start == null || win.end == null) return null;
    if (win.end <= T0 || win.start >= T1 || win.end <= win.start) return null;
    // 최소 폭(0.4%)을 주되 left + width 가 100% 를 넘지 않게 클램프한다
    const MIN_W = 0.4;
    const r = pct(win.end);
    const l = Math.min(pct(win.start), 100 - MIN_W);
    const width = Math.min(Math.max(r - l, MIN_W), 100 - l);
    return { left: l, width };
  }
  function box(cls, c) {
    return '<div class="' + cls + '" style="left:' + c.left.toFixed(2) + "%;width:" + c.width.toFixed(2) + '%"></div>';
  }
  function range(w) {
    return ml(w.start) + "–" + ml(w.end);
  }
  function skyClass(h) {
    if (h.precip && BAD_PRECIP[h.precip]) return "sky-bad";
    if (h.sky === "clear") return "sky-clear";
    if (h.sky === "partly") return "sky-partly";
    if (h.sky === "cloudy") return "sky-bad";
    return "sky-unknown";
  }
  const SKY_TEXT = { clear: "맑음", partly: "구름많음", cloudy: "흐림", unknown: "예보 없음" };

  function arr(v) {
    return Array.isArray(v) ? v : [];
  }

  // 판정 결과(JudgedCandidate) 하나 → 타임바 HTML. 그릴 것이 없으면 "".
  // 행(lane) 분리형: 한 트랙에 겹치지 않고 하늘 · 달 · 날씨 · 천체 · 관측 창을 각각 한 줄씩 그린다.
  function nightBar(j) {
    const c = j.candidate || {};
    const dark = clip(j.dark_window);
    const win = j.time_window && j.time_window.end > j.time_window.start ? clip(j.time_window) : null;
    const user = j.user_window && j.user_window.end > j.user_window.start ? clip(j.user_window) : null;
    // 자정에서 쪼개진 인접 구간(…–00:00, 00:00–…)은 하나로 이어 붙인다
    function mergeAdjacent(list) {
      const out = [];
      list.slice().sort((a, b) => a.start - b.start).forEach((w) => {
        const last = out[out.length - 1];
        if (last && w.start <= last.end) last.end = Math.max(last.end, w.end);
        else out.push({ start: w.start, end: w.end });
      });
      return out;
    }
    const moonUp = mergeAdjacent(arr(j.moon_up)).map((w) => ({ w, c: clip(w) })).filter((x) => x.c);
    const moonUnknown = mergeAdjacent(arr(j.moon_unknown)).map((w) => ({ w, c: clip(w) })).filter((x) => x.c);
    const obj = clip(j.object_window);
    const hourly = arr(j.hourly_sky)
      .map((h) => ({ h, c: clip({ start: h.hour * 60, end: h.hour * 60 + 60 }) }))
      .filter((x) => x.c);
    const ev = c.event_minute != null && c.event_minute >= T0 && c.event_minute <= T1 ? c.event_minute : null;

    if (!dark && !win && !moonUp.length && !obj && !hourly.length) return "";

    const label = [];
    const lanes = [];
    function lane(name, cls, inner, first) {
      const html = '<div class="nb-lane ' + cls + '"><span class="nb-lab">' + name + '</span><div class="nb-row">' + inner + "</div></div>";
      if (first) lanes.unshift(html); else lanes.push(html);
    }

    // 1. 하늘: 밝은 바탕 위에 어두운 시간
    if (dark) {
      let inner = box("nb-dark", dark);
      if (dark.width >= 22) inner += '<span class="nb-txt" style="left:' + (dark.left + 1).toFixed(2) + '%">어두움 ' + esc(range(j.dark_window)) + "</span>";
      lane("하늘", "nb-lane-sky", inner);
      label.push("어두운 시간 " + range(j.dark_window));
    }
    // 2. 달
    if (moonUp.length || moonUnknown.length) {
      let inner = "";
      moonUp.forEach((x) => { inner += box("nb-moon", x.c); });
      moonUnknown.forEach((x) => { inner += box("nb-moon-unknown", x.c); });
      lane("달", "nb-lane-moon", inner);
      if (moonUp.length) label.push("달 떠 있음 " + moonUp.map((x) => range(x.w)).join(", "));
      if (moonUnknown.length) label.push("달 정보 없음 " + moonUnknown.map((x) => range(x.w)).join(", "));
    }
    // 3. 날씨(시간별)
    if (hourly.length) {
      let inner = "";
      hourly.forEach((x) => { inner += box("nb-sky " + skyClass(x.h), x.c); });
      lane("날씨", "nb-lane-weather", inner);
      const counts = {};
      hourly.forEach((x) => {
        const k = x.h.precip && BAD_PRECIP[x.h.precip] ? "강수" : (SKY_TEXT[x.h.sky] || x.h.sky);
        counts[k] = (counts[k] || 0) + 1;
      });
      label.push("시간별 하늘 " + Object.keys(counts).map((k) => k + " " + counts[k] + "시간").join(", "));
    }
    // 4. 천체 고도
    if (obj) {
      lane("천체", "nb-lane-obj", box("nb-obj", obj));
      label.push("천체 떠 있음(고도 20° 이상) " + range(j.object_window));
    }
    // 5. 관측 창: 내 시간은 점선, 최종 창은 채움 + 시각
    if (win || user) {
      let inner = "";
      if (user) inner += box("nb-user", user);
      if (win) {
        inner += box("nb-win", win);
        const txt = esc(range(j.time_window));
        if (win.width >= 24) inner += '<span class="nb-txt nb-txt-win" style="left:' + (win.left + win.width / 2).toFixed(2) + '%">' + txt + "</span>";
        else if (win.left + win.width < 70) inner += '<span class="nb-txt nb-txt-out" style="left:' + (win.left + win.width).toFixed(2) + '%">' + txt + "</span>";
        else inner += '<span class="nb-txt nb-txt-out nb-txt-before" style="left:' + win.left.toFixed(2) + '%">' + txt + "</span>";
      }
      lane("관측 창", "nb-lane-win", inner, true); // 답이므로 맨 위에, 가장 두껍게
      if (win) label.push("관측 창 " + range(j.time_window));
      else label.push("내 시간 " + range(j.user_window));
    }
    // 관측 창 구간을 아래 근거 행들에도 연한 세로 띠로 비춰 준다
    const hl = win ? '<div class="nb-hl" style="--l:' + (win.left / 100).toFixed(4) + ";--w:" + (win.width / 100).toFixed(4) + '"></div>' : "";

    // 극대·현상 시각: 모든 행을 가로지르는 세로선 + 라벨
    let marker = "";
    if (ev != null) {
      const evName = c.kind === "meteor" ? "극대" : "현상";
      const p = pct(ev);
      marker = '<div class="nb-marker" style="--p:' + (p / 100).toFixed(4) + '"><span class="nb-marker-lab' + (p > 85 ? " flip" : "") + '">' + evName + " " + esc(ml(ev)) + "</span></div>";
      label.push(evName + " " + ml(ev));
    }

    const note = hourly.length
      ? '<div class="nb-note"><span class="sw sky-clear"></span>맑음 <span class="sw sky-partly"></span>구름많음 <span class="sw sky-bad"></span>흐림·강수 <span class="sw sky-unknown"></span>예보 없음</div>'
      : '<div class="nb-note">날씨: 예보 범위 밖 (D-3부터 확인 가능)</div>';

    return '<div class="nightbar" role="img" aria-label="' + esc("밤 타임바(18:00~06:00): " + label.join(", ")) + '">' +
      '<div class="nb-lane nb-lane-ticks" aria-hidden="true"><span class="nb-lab"></span><div class="nb-ticks">' + TICKS.map((t) => "<span>" + t + "</span>").join("") + "</div></div>" +
      '<div class="nb-lanes" aria-hidden="true">' + hl + lanes.join("") + marker + "</div>" +
      note + "</div>";
  }

  // 계획 서랍용 축소 타임바: 저장된 시작·종료(분)만 그린다.
  function miniBar(startMin, endMin) {
    const c = clip({ start: startMin, end: endMin });
    const text = "관측 창 " + ml(startMin) + "부터 " + ml(endMin) + (c ? "" : " (18:00~06:00 범위 밖)");
    return '<div class="nb-mini">' +
      '<div class="nb-track" role="img" aria-label="' + esc(text) + '">' + (c ? box("nb-win", c) : "") + "</div>" +
      '<div class="nb-ticks" aria-hidden="true">' + TICKS_SHORT.map((t) => "<span>" + t + "</span>").join("") + "</div></div>";
  }

  // 달 구간에서 트랙 안의 월출·월몰 시각을 찾는다 (응답 필드만 사용).
  // 경계가 트랙(1080~1800) 안쪽이고 데이터 상한(0·2159)에 잘린 값이 아닐 때만 월출·월몰로 부른다.
  // 그 외(트랙 밖에서 잘렸거나 상한에 걸린 구간)는 "달 떠 있음 HH:MM–HH:MM" 로 표기한다.
  const MINUTE_MAX = 2159;
  function moonEdges(j) {
    const out = [];
    arr(j.moon_up).forEach((w) => {
      if (w.end <= T0 || w.start >= T1) return; // 트랙과 겹치지 않는 구간은 생략
      const realRise = w.start > T0 && w.start < T1 && w.start > 0;
      const realSet = w.end > T0 && w.end < T1 && w.end < MINUTE_MAX;
      if (realRise) out.push("월출 " + ml(w.start));
      if (realSet) out.push("월몰 " + ml(w.end));
      if (!realRise && !realSet) out.push("달 떠 있음 " + ml(w.start) + "–" + ml(w.end));
    });
    return out;
  }

  // ── 레일: 연결 상태 · 계획 수 ──
  async function initShell() {
    const box = document.getElementById("health");
    if (box) {
      const row = (dotCls, text, value) =>
        '<div class="health-row"><span class="dot ' + dotCls + '" aria-hidden="true"></span><span>' + esc(text) + "</span>" +
        (value != null ? '<span class="num">' + esc(value) + "</span>" : "") + "</div>";
      try {
        const h = await window.Api.get("/api/health");
        const tools = Array.isArray(h.tools) ? h.tools.length : 0;
        box.innerHTML = '<div class="rail-card-title">연결 상태</div>' +
          row(h.status === "ok" ? "dot-ok" : "dot-cond", h.status === "ok" ? "서버 연결됨" : "서버 응답 이상", null) +
          row(h.llm_configured ? "dot-ok" : "dot-cond", h.llm_configured ? "LLM 키 설정됨" : "LLM 키 없음", null) +
          row(tools ? "dot-ok" : "dot-cond", "Tool", tools + "개");
      } catch (_) {
        box.innerHTML = '<div class="rail-card-title">연결 상태</div>' +
          row("dot-bad", "서버에 연결할 수 없음", null) + row("", "LLM 확인 불가", null) + row("", "Tool 확인 불가", null);
      }
    }
    const count = document.getElementById("rail-plan-count");
    if (count) {
      try {
        const plans = await window.Api.get("/api/plans");
        count.textContent = String(plans.length);
        count.hidden = false;
      } catch (_) { /* 표시만 생략 */ }
    }
  }

  window.UI = { Icons, VERDICT, verdictBadge, verdictText, nightBar, miniBar, moonEdges, initShell, T0, T1 };
  document.addEventListener("DOMContentLoaded", initShell);
})();
