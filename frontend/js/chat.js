// 대화 화면 (F3018): 조건 스트립 · 메시지 송수신 · 추천 카드(밤 타임바) · 계획 저장 · 실행 패널.
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
  function scrollToEnd(el) {
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

    const win = tw
      ? stat("관측 창", timeHtml(tw.start) + " – " + timeHtml(tw.end), "is-ink", (tw.end - tw.start) + "분")
      : stat("관측 창", "—", "is-none", "가능한 시간 창 없음");

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

    return '<div class="stats">' + win + clear + moon + arrive + "</div>";
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
    const myGen = generation;
    try {
      const res = await window.Api.post("/api/chat", { session_id: getSession(), message });
      if (myGen !== generation) return;
      setSession(res.session_id);
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

  $newChat.addEventListener("click", () => {
    generation += 1;
    setSession(null);
    $messages.innerHTML = "";
    resetPanel();
    renderConditions(null, []);
    addWelcome();
  });

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

  setSession(readStoredSession());
  renderConditions(null, []);
  resetPanel();
  addWelcome();
})();
