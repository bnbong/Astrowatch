// 대화 화면 (F3018): 조건 스트립 · 메시지 송수신 · 추천 카드(밤 타임바) · 계획 저장 · 실행 패널 · 최근 대화 목록/복원.
(function () {
  "use strict";

  const esc = window.Fmt.escapeHtml;
  const md = window.Markdown.render; // 전부 이스케이프한 뒤 안전한 부분집합만 변환
  const mdInline = window.Markdown.renderInline;
  const ml = window.Fmt.minuteLabel;
  const UI = window.UI;
  const I = UI.Icons;
  const SESSION_KEY = "astrowatch.session_id";

  const $messages = document.getElementById("messages");
  const $form = document.getElementById("chat-form");
  const $input = document.getElementById("chat-input");
  const $send = document.getElementById("send-btn");
  const $status = document.getElementById("status");
  const $newChat = document.getElementById("new-chat");
  const $timeline = document.getElementById("timeline");
  const $evidence = document.getElementById("evidence");
  const $sessionLabel = document.getElementById("session-label");
  const $cond = document.getElementById("cond-strip");
  const $panel = document.getElementById("exec-panel");
  const $handle = document.getElementById("sheet-handle");
  const $sheetSummary = document.getElementById("sheet-summary");
  const $sumTools = document.getElementById("sum-tools");
  const $sumReplan = document.getElementById("sum-replan");
  const $sumLog = document.getElementById("sum-log");
  const $convoList = document.getElementById("convo-list");     // 레일 (넓은 화면)
  const $convoListM = document.getElementById("convo-list-m");  // 상단 드롭다운 (모바일)
  const $convoHeading = document.getElementById("convo-heading");
  const $convoToggle = document.getElementById("convo-toggle");
  const $convoSheet = document.getElementById("convo-sheet");
  const $convoClose = document.getElementById("convo-close");

  const KIND = {
    meteor: "유성우", planet: "행성", moon: "달", eclipse: "식", occultation: "엄폐",
    season: "절기", deep_sky: "딥스카이", other: "기타",
  };
  const SKY = { clear: "맑음", partly: "구름많음", cloudy: "흐림", unknown: "예보 없음" };

  // ── 세션 ──
  // 전송에 쓰는 세션은 메모리 값(currentSession)이 기준이다. localStorage 는 새로고침 복원용일 뿐이라,
  // 다른 탭이 저장소에 옛 세션을 다시 써도 "새 대화" 이후 첫 메시지는 session_id: null 로 나간다.
  let currentSession = null;
  function readStoredSession() {
    try { return localStorage.getItem(SESSION_KEY); } catch (_) { return null; }
  }
  function getSession() {
    return currentSession;
  }
  function setSession(id) {
    currentSession = id || null;
    try {
      if (currentSession) localStorage.setItem(SESSION_KEY, currentSession);
      else localStorage.removeItem(SESSION_KEY);
    } catch (_) { /* 저장소 불가 시 무시 */ }
    $sessionLabel.textContent = currentSession ? "세션 " + currentSession.slice(0, 8) : "새 대화";
    if (currentSession) {
      $sumLog.href = "logs.html#" + encodeURIComponent(currentSession);
      $sumLog.hidden = false;
    } else {
      $sumLog.hidden = true;
    }
    renderConvos(); // 현재 세션 강조 갱신
  }

  // ── 조건 스트립 ──
  function fmtMonthDay(iso) {
    if (!iso) return null;
    const p = String(iso).split("-").map(Number);
    if (p.length < 3 || p.some(isNaN)) return String(iso);
    return p[1] + "월 " + p[2] + "일";
  }
  function dateText(c) {
    if (!c.date_from) return null;
    if (!c.date_to || c.date_to === c.date_from) return fmtMonthDay(c.date_from);
    const a = c.date_from.split("-").map(Number);
    const b = c.date_to.split("-").map(Number);
    return a[1] === b[1] ? a[1] + "월 " + a[2] + "–" + b[2] + "일" : fmtMonthDay(c.date_from) + " – " + fmtMonthDay(c.date_to);
  }
  function timeText(c) {
    if (c.start_minute != null && c.end_minute != null) return ml(c.start_minute) + " – " + ml(c.end_minute);
    if (c.start_minute != null) return ml(c.start_minute) + "부터";
    if (c.end_minute != null) return ml(c.end_minute) + "까지";
    return null;
  }
  function placeText(c) {
    const parts = [];
    if (c.place_query) parts.push(c.place_query);
    if (c.max_distance_km != null) parts.push(c.max_distance_km + "km 이내");
    return parts.length ? parts.join(" · ") : null;
  }

  const COND_FIELDS = [
    { label: "날짜", keys: ["date_from", "date_to", "date"], value: dateText, num: false },
    { label: "지역", keys: ["region"], value: (c) => c.region || null },
    { label: "시간", keys: ["start_minute", "end_minute", "time"], value: timeText, num: true },
    { label: "현상", keys: ["event_query", "event_kind", "event"], value: (c) => c.event_query || (c.event_kind ? KIND[c.event_kind] || c.event_kind : null) },
    { label: "장소", keys: ["place_query", "max_distance_km", "place"], value: placeText },
  ];

  function renderConditions(cond, needs) {
    const c = cond || {};
    const need = new Set(needs || []);
    const fixed = new Set(c.fixed || []);
    let html = '<span class="cond-label">조건</span>';
    COND_FIELDS.forEach((f) => {
      const v = f.value(c);
      const isNeeded = f.keys.some((k) => need.has(k));
      const isFixed = f.keys.some((k) => fixed.has(k));
      const lock = isFixed ? '<span class="fixed" title="고정한 조건 — 재계획에서 바꾸지 않음">' + I.lock(11) + '<span class="sr-only">(고정)</span></span>' : "";
      if (v == null) {
        html += '<span class="cond-chip ' + (isNeeded ? "is-needed" : "is-empty") + '">' + esc(f.label) + " · " +
          (isNeeded ? "알려 주세요" : "미지정") + "</span>";
      } else {
        html += '<span class="cond-chip' + (isNeeded ? " is-needed" : "") + '"><span class="k">' + esc(f.label) + " </span>" +
          "<b" + (f.num ? ' class="num"' : "") + ">" + esc(v) + "</b>" + lock + "</span>";
      }
    });
    if (c.allow_partly_cloudy) html += '<span class="cond-chip"><span class="k">하늘 </span><b>구름많음 허용</b></span>';
    $cond.innerHTML = html;
  }

  // ── 말풍선 ──
  let restoring = false; // 복원 중에는 말풍선마다 부드러운 스크롤을 하지 않고 마지막에 한 번만 내린다
  function scrollToEnd(el) {
    if (restoring) return;
    el.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  function addUser(text) {
    const div = document.createElement("div");
    div.className = "msg-user";
    div.innerHTML = esc(text).replace(/\n/g, "<br>"); // 사용자 입력은 서식 없이 그대로
    $messages.appendChild(div);
    scrollToEnd(div);
  }
  function addBot(metaText) {
    const div = document.createElement("div");
    div.className = "msg-bot";
    div.innerHTML = '<div class="bot-meta">' + I.mark(16) + "<span>" + esc(metaText || "Astrowatch") + "</span></div>";
    $messages.appendChild(div);
    return div;
  }
  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  // ── 시간 창 → datetime (자정 넘김은 다음 날) ──
  function windowToRange(dateStr, win) {
    const [y, mo, d] = dateStr.split("-").map(Number);
    const base = new Date(y, mo - 1, d, 0, 0, 0);
    const start = new Date(base.getTime() + win.start * 60000);
    const end = new Date(base.getTime() + win.end * 60000);
    return { start, end };
  }

  function bestWindow(j) {
    return j.time_window || j.user_window || j.dark_window || null;
  }

  // "02:00+1" → 02:00<sup>+1</sup>
  function timeHtml(minute) {
    const t = ml(minute);
    return t.endsWith("+1") ? esc(t.slice(0, -2)) + '<sup class="plus1">+1</sup>' : esc(t);
  }
  function durText(min) {
    if (min == null) return "—";
    const h = Math.floor(min / 60);
    const m = min % 60;
    return h ? h + "시간" + (m ? " " + m + "분" : "") : m + "분";
  }

  function statsHtml(j) {
    const tw = j.time_window && j.time_window.end > j.time_window.start ? j.time_window : null;
    const stat = (k, vHtml, vCls, sub) =>
      '<div class="stat"><div class="stat-k">' + esc(k) + '</div><div class="stat-v ' + (vCls || "") + '">' + vHtml + "</div>" +
      (sub ? '<div class="stat-s">' + esc(sub) + "</div>" : "") + "</div>";

    // 관측 창은 카드의 답이므로 수치 칸이 아니라 헤드라인으로 크게 보여 준다
    let hero;
    if (tw) {
      const chips = ['<span class="hero-chip num">' + esc(durText(tw.end - tw.start)) + "</span>"];
      if (j.latest_arrival_minute != null) chips.push('<span class="hero-chip">늦어도 <b class="num">' + timeHtml(j.latest_arrival_minute) + "</b> 도착</span>");
      if (j.verdict === "conditional") chips.push('<span class="hero-chip is-cond">날씨 미확인</span>');
      hero = '<div class="hero-window' + (j.verdict === "fit" ? " is-fit" : "") + '">' +
        '<div class="hero-k">' + I.clock(14) + "관측 가능 시각</div>" +
        '<div class="hero-v num">' + timeHtml(tw.start) + '<span class="hero-dash">–</span>' + timeHtml(tw.end) + "</div>" +
        '<div class="hero-chips">' + chips.join("") + "</div></div>";
    } else {
      hero = '<div class="hero-window is-none"><div class="hero-k">' + I.clock(14) + "관측 가능 시각</div>" +
        '<div class="hero-v num is-none">—</div><div class="hero-chips"><span class="hero-chip">가능한 시간 창 없음</span></div></div>';
    }

    let clear;
    if (j.clear_minutes == null) {
      clear = stat("맑은 시간", "—", "is-none", "예보 D-3부터");
    } else {
      const sub = [];
      if (j.worst_sky) sub.push("가장 나쁜 하늘 " + (SKY[j.worst_sky] || j.worst_sky));
      if (j.max_pop != null) sub.push("강수 " + j.max_pop + "%");
      clear = stat("맑은 시간", esc(durText(j.clear_minutes)), j.clear_minutes > 0 ? "is-ok" : "is-bad", sub.join(" · "));
    }

    const edges = UI.moonEdges(j);
    const effect = j.moon_interferes === true ? "영향 있음" : j.moon_interferes === false ? "영향 적음" : "영향 판단 안 함";
    const moonSub = edges.concat([effect]).join(" · ");
    const moon = j.lun_age != null
      ? stat("달", "월령 " + esc(j.lun_age), j.moon_interferes === true ? "is-bad" : j.moon_interferes === false ? "is-ok" : "", moonSub)
      : stat("달", "—", "is-none", effect);

    const arrive = j.latest_arrival_minute != null
      ? stat("늦어도 도착", timeHtml(j.latest_arrival_minute), "", "최소 관측 시간 확보")
      : stat("늦어도 도착", "—", "is-none", "역산할 창 없음");

    return hero + '<div class="stats stats-3">' + clear + moon + arrive + "</div>";
  }

  // 겹치는 계획 안내 (저장 응답의 overlaps)
  function overlapHtml(overlaps) {
    const F = window.Fmt;
    return '<div class="banner banner-cond">' + I.alert(16) + "<span>같은 시간대에 겹치는 계획 " + overlaps.length + "건: " +
      overlaps.map((p) => "<b>" + esc(p.event_title) + "</b>(" + esc(F.fmtTime(p.start_dt) + "–" + F.fmtTime(p.end_dt)) + ")").join(", ") +
      ". 저장은 막지 않았습니다.</span></div>";
  }

  // ── 추천 카드 ──
  function renderCard(j, sessionId) {
    const c = j.candidate;
    const win = bestWindow(j);
    const reasons = j.reasons || [];
    const card = el("article", "card");
    card.innerHTML =
      '<div class="card-head"><h2 class="card-title">' + esc(c.title) + "</h2>" +
      '<span class="card-sub num">' + esc(c.date) + " · " + esc(c.region) + "</span>" + UI.verdictBadge(j.verdict) + "</div>" +
      statsHtml(j) +
      UI.nightBar(j) +
      '<div class="card-foot">' +
      (reasons.length
        ? "<details><summary>판단 근거 " + reasons.length + "건</summary><ul class=\"reasons\">" +
          reasons.map((r) => "<li>" + mdInline(r) + "</li>").join("") + "</ul></details>"
        : '<span class="muted no-reasons">판단 근거 없음</span>') +
      '<button type="button" class="btn btn-primary save-btn">계획으로 저장</button>' +
      '<div class="save-result" aria-live="polite"></div>' +
      "</div>";

    const btn = card.querySelector(".save-btn");
    const out = card.querySelector(".save-result");
    if (!win || win.end <= win.start) {
      btn.disabled = true;
      out.innerHTML = '<span class="save-msg muted">관측 가능한 시간 창이 없어 저장할 수 없습니다.</span>';
    }
    btn.addEventListener("click", async () => {
      const range = windowToRange(c.date, win);
      const body = {
        event_title: c.title,
        event_kind: c.kind,
        start_dt: window.Fmt.toLocalIso(range.start),
        end_dt: window.Fmt.toLocalIso(range.end),
        location_label: c.region,
        lat: c.lat,
        lon: c.lon,
        target_alt: j.target_alt,
        target_az: j.target_az,
        basis: reasons.join(" / ") || null,
        session_id: sessionId || null,
      };
      btn.disabled = true;
      out.innerHTML = '<span class="save-msg">저장 중…</span>';
      try {
        const res = await window.Api.post("/api/plans", body);
        const overlaps = res.overlaps || [];
        out.innerHTML = '<span class="save-msg ok">' + I.check(12) + ' 저장했습니다. <a href="plans.html">계획 보기</a></span>' +
          (overlaps.length ? overlapHtml(overlaps) : "");
        btn.textContent = "저장됨";
      } catch (e) {
        btn.disabled = false;
        out.innerHTML = '<span class="save-msg err">' + esc(e.message) + "</span>";
      }
    });
    return card;
  }

  // ── 종료 사유 스트립 (stop_reason 이 없으면 없음) ──
  // 설정된 상한(replan_limit)과 실제 수행 횟수(replan_count)를 섞지 않는다.
  function stopStrip(res) {
    const count = res.replan_count || 0;
    const limit = res.replan_limit;
    const ratio = '<span class="stop-count">' + esc(count) + (limit != null ? "/" + esc(limit) : "") + "</span>";
    let icon;
    let text;
    let fatal = false;
    switch (res.stop_reason) {
      case "limit_reached":
        icon = ratio;
        text = "재계획 상한 " + (limit != null ? limit + "회" : "") + "에 도달했습니다 (수행 " + count + "회) — 날짜 범위나 지역을 넓혀 볼까요?";
        break;
      case "no_alternative":
        icon = ratio;
        text = count + "회 재계획했지만 더 시도할 대안이 없습니다.";
        break;
      case "target_unobservable":
        icon = I.horizon(16);
        text = "요청한 대상은 이 지역·날짜에 관측할 수 없어 재계획하지 않았습니다.";
        break;
      case "fixed_constraints":
        icon = I.lock(16);
        text = "고정하신 조건 때문에 바꿀 수 있는 대안이 없습니다.";
        break;
      case "fatal_error":
        icon = I.alert(16);
        text = "API 키·인증 문제로 중단했습니다 — 설정을 확인하세요.";
        fatal = true;
        break;
      default:
        return null;
    }
    return el("div", "stop-strip" + (fatal ? " is-fatal" : ""), icon + "<span>" + esc(text) + "</span>");
  }

  function renderResponse(res) {
    const st = window.Timeline.stats(res.steps);
    const meta = "Astrowatch · Tool " + st.calls + "회" + (st.failed ? " (실패 " + st.failed + ")" : "") +
      (res.replan_count ? " · 재계획 " + res.replan_count + "회" : "");
    const wrap = addBot(meta);

    (res.warnings || []).forEach((w) => {
      wrap.appendChild(el("div", "banner banner-cond", I.alert(16) + "<span>" + esc(w) + "</span>"));
    });
    const stop = stopStrip(res);
    if (stop) wrap.appendChild(stop);

    wrap.appendChild(el("div", "answer", md(res.answer)));

    if (res.recommended && res.recommended.length) {
      const grid = el("div", "cards");
      res.recommended.forEach((j) => grid.appendChild(renderCard(j, res.session_id)));
      wrap.appendChild(grid);
    }

    const aux = el("div", "aux-row");
    if (res.excluded && res.excluded.length) {
      const det = el("details", "soft-box");
      det.innerHTML = "<summary>제외한 후보 " + res.excluded.length + "건</summary><ul>" +
        res.excluded.map((j) => '<li class="excl-item"><b>' + esc(j.candidate.title) + '</b> <span class="num">' + esc(j.candidate.date) + "</span> · " +
          esc(UI.verdictText(j.verdict)) +
          ((j.reasons || []).length ? "<small>" + (j.reasons || []).map(mdInline).join(" / ") + "</small>" : "") + "</li>").join("") + "</ul>";
      aux.appendChild(det);
    }
    if (res.places && res.places.length) {
      const box = el("div", "soft-box");
      box.innerHTML = "<h4>주변 관측 장소</h4><ul class=\"place-list\">" + res.places.map((p) => {
        const km = (p.distance_m / 1000).toFixed(1);
        const safeUrl = /^https?:\/\//.test(p.url) ? p.url : "#";
        return '<li><a href="' + esc(safeUrl) + '" target="_blank" rel="noopener">' + esc(p.name) + "</a> · <span class=\"num\">" + esc(km) + " km</span>" +
          (p.address ? '<br><small class="muted">' + esc(p.address) + "</small>" : "") + "</li>";
      }).join("") + "</ul>";
      aux.appendChild(box);
    }
    if (res.safety_note) {
      aux.appendChild(el("div", "soft-box with-icon", I.info(16) + "<span>" + esc(res.safety_note) + "</span>"));
    }
    if (aux.children.length) wrap.appendChild(aux);
    scrollToEnd(wrap);
  }

  // ── 실행 패널 ──
  function renderEvidence(res) {
    const sec = [];
    const judged = (list, title) => {
      if (!list || !list.length) return;
      sec.push("<section><h3>" + esc(title) + '</h3><ul class="ev-list">' + list.map((j) =>
        '<li class="ev-item"><div class="ev-title">' + esc(j.candidate.title) + ' <span class="num muted">' + esc(j.candidate.date) + "</span>" +
        UI.verdictBadge(j.verdict) + "</div>" +
        ((j.reasons || []).length ? "<ul>" + j.reasons.map((r) => "<li>" + mdInline(r) + "</li>").join("") + "</ul>" : '<span class="muted">근거 없음</span>') +
        "</li>").join("") + "</ul></section>");
    };
    judged(res.recommended, "추천 후보 근거");
    judged(res.excluded, "제외 사유");
    const results = (res.steps || []).filter((s) => s.type === "tool_result").sort((a, b) => a.seq - b.seq);
    if (results.length) {
      sec.push('<section><h3>Tool 응답 요약</h3><ul class="ev-list">' + results.map((s) =>
        '<li class="ev-item' + (s.ok === false ? " is-fail" : "") + '"><div class="ev-title"><code>' + esc(s.tool || "-") + "</code>" +
        (s.duration_ms != null ? ' <span class="tl-ms">' + esc(s.duration_ms) + "ms</span>" : "") +
        (s.replan_round ? ' <span class="tl-tag is-replan">재계획 ' + esc(s.replan_round) + "</span>" : "") + "</div>" +
        '<div class="ev-sum">' + esc(s.summary || "") + "</div></li>").join("") + "</ul></section>");
    }
    $evidence.innerHTML = sec.length ? '<div class="evidence">' + sec.join("") + "</div>" : '<p class="empty">표시할 근거 데이터가 없습니다.</p>';
  }

  function renderSummary(res) {
    if (!res) {
      $sumTools.textContent = "—";
      $sumReplan.textContent = "—";
      $sheetSummary.textContent = "아직 실행 없음";
      return;
    }
    const st = window.Timeline.stats(res.steps);
    $sumTools.textContent = st.calls + " · 실패 " + st.failed;
    const replan = (res.replan_count || 0) + (res.replan_limit != null ? " / " + res.replan_limit : "");
    $sumReplan.textContent = replan;
    $sheetSummary.textContent = "Tool " + st.calls + " · 실패 " + st.failed + " · 재계획 " + replan.replace(/ /g, "");
  }

  function resetPanel() {
    $timeline.innerHTML = '<p class="empty">메시지를 보내면 실행 단계가 여기에 표시됩니다.</p>';
    $evidence.innerHTML = '<p class="empty">추천·제외 후보의 판단 근거와 Tool 응답 요약이 여기에 표시됩니다.</p>';
    renderSummary(null);
  }

  // 탭
  const tabs = [document.getElementById("tab-timeline"), document.getElementById("tab-evidence")];
  function selectTab(tab) {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t));
    t.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(next);
        next.focus();
      }
    });
  });

  // 좁은 화면: 실행 패널을 아래 시트로 연다
  function setSheet(open) {
    $panel.classList.toggle("is-open", open);
    $handle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) document.getElementById("tab-timeline").focus();
  }
  $handle.addEventListener("click", () => setSheet(!$panel.classList.contains("is-open")));
  document.getElementById("exec-close").addEventListener("click", () => { setSheet(false); $handle.focus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $panel.classList.contains("is-open")) { setSheet(false); $handle.focus(); }
  });

  function setBusy(busy) {
    $send.disabled = busy;
    $input.disabled = busy;
    $status.hidden = !busy;
  }

  // "새 대화" 를 누르면 세대가 바뀐다. 이전 세대의 응답은 버려 옛 세션이 되살아나지 않게 한다.
  let generation = 0;

  $form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const message = $input.value.trim();
    if (!message) return; // 공백만이면 전송하지 않는다 (서버도 trim 후 422)
    addUser(message);
    $input.value = "";
    setBusy(true);
    const myGen = ++generation; // 진행 중인 복원이 있으면 그 결과는 버린다
    try {
      const res = await window.Api.post("/api/chat", { session_id: getSession(), message });
      if (myGen !== generation) return;
      setSession(res.session_id);
      noteTurn(res.session_id, message);
      renderConditions(res.conditions, res.needs_clarification);
      renderResponse(res);
      window.Timeline.render($timeline, res.steps);
      renderEvidence(res);
      renderSummary(res);
    } catch (e) {
      if (myGen !== generation) return;
      const wrap = addBot("Astrowatch");
      wrap.appendChild(el("div", "banner banner-bad", I.alert(16) + "<span>" + esc(e.message) + "</span>"));
      scrollToEnd(wrap);
    } finally {
      setBusy(false);
      $input.focus();
    }
  });

  // Enter 전송, Shift+Enter 줄바꿈
  $input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      $form.requestSubmit();
    }
  });

  function startNewChat() {
    generation += 1;
    confirmingId = null;
    setSession(null); // 목록 강조도 해제된다
    $messages.innerHTML = "";
    resetPanel();
    renderConditions(null, []);
    addWelcome();
  }
  $newChat.addEventListener("click", startNewChat);

  // ── 최근 대화 목록 ──
  // GET /api/sessions 를 레일과 모바일 드롭다운 두 곳에 같은 HTML 로 그린다. API 가 없거나 실패해도 화면은 유지.
  let convos = [];
  let convosLoaded = false;
  let convosFailed = false;
  let confirmingId = null; // 인라인 삭제 확인 중인 세션
  let convoReq = 0;
  const convoLists = [$convoList, $convoListM].filter(Boolean);

  function pad2(n) { return String(n).padStart(2, "0"); }
  // 오늘이면 HH:MM, 아니면 M/D
  function convoTime(value) {
    if (!value) return "";
    const d = new Date(value);
    if (isNaN(d)) return "";
    const now = new Date();
    const today = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return today ? pad2(d.getHours()) + ":" + pad2(d.getMinutes()) : (d.getMonth() + 1) + "/" + d.getDate();
  }
  function convoTitle(s) {
    const t = String((s && s.title) || "").trim();
    return t ? t.slice(0, 60) : "제목 없는 대화";
  }

  function convoItemHtml(s) {
    const id = String(s.session_id);
    const cur = id === currentSession;
    const title = convoTitle(s);
    const confirming = confirmingId === id;
    let html = '<li class="convo-item' + (cur ? " is-current" : "") + '"><div class="convo-row">' +
      '<button type="button" class="convo-open" data-open="' + esc(id) + '"' + (cur ? ' aria-current="true"' : "") + ' title="' + esc(title) + '">' +
      '<span class="convo-title">' + esc(title) + "</span>" +
      '<span class="convo-meta"><span class="num">' + esc(convoTime(s.last_at)) + "</span>" +
      '<span class="convo-turns num">' + esc(s.turn_count || 0) + "턴</span></span></button>" +
      '<button type="button" class="convo-del" data-del="' + esc(id) + '" aria-label="대화 삭제: ' + esc(title) + '"' +
      ' aria-expanded="' + (confirming ? "true" : "false") + '">' + I.x(14) + "</button></div>";
    if (confirming) {
      html += '<div class="convo-confirm" role="group" aria-label="삭제 확인">' +
        '<p class="convo-confirm-msg">이 대화를 삭제할까요? 대화만 지워지고 실행 로그는 남습니다.</p>' +
        '<div class="convo-confirm-actions">' +
        '<button type="button" class="btn btn-sm btn-danger-fill" data-confirm-del="' + esc(id) + '">삭제</button>' +
        '<button type="button" class="btn btn-sm" data-cancel-del="' + esc(id) + '">취소</button></div></div>';
    }
    return html + "</li>";
  }

  function renderConvos() {
    if (!convoLists.length) return;
    let html;
    if (convos.length) html = convos.map(convoItemHtml).join("");
    else if (!convosLoaded) html = '<li class="convo-empty">불러오는 중…</li>';
    else html = '<li class="convo-empty">' + (convosFailed ? "대화 목록을 불러오지 못했습니다" : "아직 대화가 없습니다") + "</li>";
    convoLists.forEach((ul) => { ul.innerHTML = html; });
  }

  async function refreshConvos() {
    const my = ++convoReq;
    try {
      const list = await window.Api.get("/api/sessions");
      if (my !== convoReq) return;
      convos = Array.isArray(list) ? list.filter((s) => s && s.session_id) : [];
      convosFailed = false;
    } catch (_) {
      if (my !== convoReq) return;
      convosFailed = true; // 기존(낙관적) 목록은 그대로 둔다
    }
    convosLoaded = true;
    renderConvos();
  }

  // 응답을 받으면 목록을 즉시 고친 뒤(새 세션이면 맨 위에 추가) 서버 목록으로 다시 맞춘다.
  function noteTurn(sessionId, message) {
    if (!sessionId) return;
    const now = window.Fmt.toLocalIso(new Date());
    const i = convos.findIndex((s) => s.session_id === sessionId);
    const item = i >= 0
      ? Object.assign({}, convos.splice(i, 1)[0], { last_at: now })
      : { session_id: sessionId, title: String(message || "").slice(0, 60), started_at: now, last_at: now, turn_count: 0, last_intent: null };
    item.turn_count = (item.turn_count || 0) + 1;
    convos.unshift(item);
    convosLoaded = true;
    renderConvos();
    refreshConvos();
  }

  function findButton(ul, attr, id) {
    return Array.from(ul.querySelectorAll("button")).find((b) => b.getAttribute(attr) === id) || null;
  }

  async function deleteConvo(id, btn) {
    btn.disabled = true;
    try {
      await window.Api.del("/api/sessions/" + encodeURIComponent(id));
    } catch (e) {
      if (e.status !== 404) { // 404 는 이미 지워진 것으로 본다
        btn.disabled = false;
        const box = btn.closest(".convo-confirm");
        const msg = box && box.querySelector(".convo-confirm-msg");
        if (msg) msg.textContent = e.message;
        return;
      }
    }
    const fromRail = !!($convoList && $convoList.contains(btn));
    confirmingId = null;
    convos = convos.filter((s) => s.session_id !== id);
    if (id === currentSession) startNewChat();
    else renderConvos();
    if (fromRail && $convoHeading) $convoHeading.focus();
    refreshConvos();
  }

  function onConvoClick(ev) {
    const ul = ev.currentTarget;
    const b = ev.target.closest("button");
    if (!b || !ul.contains(b)) return;
    if (b.hasAttribute("data-open")) {
      confirmingId = null;
      setConvoSheet(false);
      restoreSession(b.getAttribute("data-open"));
    } else if (b.hasAttribute("data-del")) {
      const id = b.getAttribute("data-del");
      confirmingId = confirmingId === id ? null : id;
      renderConvos();
      const target = confirmingId ? findButton(ul, "data-confirm-del", id) : findButton(ul, "data-del", id);
      if (target) target.focus();
    } else if (b.hasAttribute("data-confirm-del")) {
      deleteConvo(b.getAttribute("data-confirm-del"), b);
    } else if (b.hasAttribute("data-cancel-del")) {
      const id = b.getAttribute("data-cancel-del");
      confirmingId = null;
      renderConvos();
      const target = findButton(ul, "data-del", id);
      if (target) target.focus();
    }
  }
  convoLists.forEach((ul) => ul.addEventListener("click", onConvoClick));

  // 모바일: 헤더 "대화 목록" 버튼으로 여닫는 상단 드롭다운
  function setConvoSheet(open) {
    if (!$convoSheet || !$convoToggle) return;
    if ($convoSheet.hidden === !open) return;
    $convoSheet.hidden = !open;
    $convoToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      refreshConvos();
      const first = $convoSheet.querySelector("button.convo-open") || $convoClose;
      if (first) first.focus();
    }
  }
  if ($convoToggle) $convoToggle.addEventListener("click", () => setConvoSheet($convoSheet.hidden));
  if ($convoClose) $convoClose.addEventListener("click", () => { setConvoSheet(false); $convoToggle.focus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $convoSheet && !$convoSheet.hidden) { setConvoSheet(false); $convoToggle.focus(); }
  });

  // ── 세션 복원 ──
  // GET /api/sessions/{id} 의 턴을 그대로 다시 그린다. 404 면 안내 후 새 대화. quiet 면 실패를 알리지 않는다(자동 복원).
  async function restoreSession(id, opts) {
    const quiet = !!(opts && opts.quiet);
    if (!id) return false;
    const myGen = ++generation; // 진행 중인 전송·복원 결과는 버린다
    let detail;
    try {
      detail = await window.Api.get("/api/sessions/" + encodeURIComponent(id));
    } catch (e) {
      if (myGen !== generation) return false;
      if (quiet) { startNewChat(); return false; }
      if (e.status === 404) {
        convos = convos.filter((s) => s.session_id !== id);
        startNewChat();
        const wrap = addBot("Astrowatch");
        wrap.appendChild(el("div", "banner banner-cond", I.alert(16) + "<span>이 대화를 찾을 수 없습니다. 삭제되었을 수 있어 새 대화로 시작합니다.</span>"));
        refreshConvos();
      } else {
        const wrap = addBot("Astrowatch");
        wrap.appendChild(el("div", "banner banner-bad", I.alert(16) + "<span>" + esc(e.message) + "</span>"));
        scrollToEnd(wrap);
      }
      return false;
    }
    if (myGen !== generation) return false;

    const turns = (detail && Array.isArray(detail.turns) ? detail.turns.slice() : [])
      .sort((a, b) => (a.seq || 0) - (b.seq || 0));
    confirmingId = null;
    $messages.innerHTML = "";
    resetPanel();
    renderConditions(null, []);
    setSession((detail && detail.session_id) || id);
    restoring = true;
    try {
      if (!turns.length) addWelcome();
      turns.forEach((t) => {
        addUser(t.user_message || "");
        if (t.response) renderResponse(t.response);
      });
    } finally {
      restoring = false;
    }
    const last = turns.length ? turns[turns.length - 1].response : null;
    if (last) {
      renderConditions(last.conditions, last.needs_clarification);
      window.Timeline.render($timeline, last.steps);
      renderEvidence(last);
      renderSummary(last);
    }
    $messages.scrollTop = $messages.scrollHeight;
    refreshConvos();
    return true;
  }

  document.querySelectorAll("[data-example]").forEach((b) =>
    b.addEventListener("click", () => {
      $input.value = b.dataset.example;
      $input.focus();
    }));

  function addWelcome() {
    const wrap = addBot("Astrowatch");
    wrap.appendChild(el("div", "answer",
      "<p>안녕하세요! 관측하고 싶은 <b>날짜 · 지역 · 현상</b>을 알려 주세요. 예: \"12월 14일 밤 서울에서 쌍둥이자리 유성우 볼 수 있어?\"</p>"));
  }

  window.Chat = { restoreSession, refreshConvos, startNewChat };

  // ── 시작: 저장된 세션이 있으면 조용히 복원, 없거나 실패하면 새 대화 ──
  renderConditions(null, []);
  resetPanel();
  const stored = readStoredSession();
  if (stored) {
    renderConvos();
    $sessionLabel.textContent = "불러오는 중…";
    restoreSession(stored, { quiet: true });
  } else {
    setSession(null);
    addWelcome();
  }
  refreshConvos();
})();
