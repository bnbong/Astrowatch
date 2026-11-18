// 계획 관리 (F3015 · F3016): 날짜별 목록 · 상세 서랍 · 인라인 수정 · 삭제 · 캘린더 등록(F3023).
(function () {
  "use strict";

  const esc = window.Fmt.escapeHtml;
  const F = window.Fmt;
  const UI = window.UI;
  const I = UI.Icons;

  const $list = document.getElementById("plans-list");
  const $empty = document.getElementById("plans-empty");
  const $detail = document.getElementById("plan-detail");
  const $msg = document.getElementById("page-msg");
  const $summary = document.getElementById("plans-summary");

  const KIND = {
    meteor: "유성우", planet: "행성", moon: "달", eclipse: "식",
    occultation: "엄폐", season: "절기", deep_sky: "딥스카이", other: "기타",
  };
  const WEEK = ["일", "월", "화", "수", "목", "금", "토"];
  const DRAWER_EMPTY = '<p class="drawer-empty">목록에서 계획을 고르면 상세 · 수정 · 캘린더 등록이 여기에 표시됩니다.</p>';

  let plans = [];
  let selectedId = null;
  let filter = "all";
  const calResults = {}; // plan_id → 이번 화면에서 받은 등록 결과 (provider · html_link)

  const AUTH_HINT = "Google 토큰이 없습니다. backend/scripts/google_auth.py 를 실행하세요.";

  function flash(text, kind) {
    $msg.innerHTML = (kind === "err" ? I.alert(16) : I.check(14)) + "<span>" + esc(text) + "</span>";
    $msg.className = "banner " + (kind === "err" ? "banner-bad" : "banner-ok");
    $msg.hidden = false;
  }

  // ── 날짜 계산 ──
  // 앱의 '저녁 기준 날짜' 규칙을 따른다: 정오 이전에 시작하면 전날 밤 계획으로 묶는다.
  function nightDate(p) {
    const d = new Date(p.start_dt);
    const n = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    if (d.getHours() < 12) n.setDate(n.getDate() - 1);
    return n;
  }
  function dayKey(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function minutesOfNight(p) {
    const base = nightDate(p).getTime();
    const s = Math.round((new Date(p.start_dt).getTime() - base) / 60000);
    const e = Math.round((new Date(p.end_dt).getTime() - base) / 60000);
    return { start: s, end: e };
  }
  function ddayText(d) {
    const today = new Date();
    const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const diff = Math.round((d - t) / 86400000);
    if (diff === 0) return "오늘 밤";
    return diff > 0 ? "D-" + diff : "D+" + -diff + " · 지남";
  }
  function dayLabel(d) {
    return (d.getMonth() + 1) + "월 " + d.getDate() + "일 (" + WEEK[d.getDay()] + ")";
  }

  // 클라이언트 겹침 계산: 시간 구간이 겹치는 다른 계획과 겹친 분
  function overlapsOf(p) {
    const s = new Date(p.start_dt).getTime();
    const e = new Date(p.end_dt).getTime();
    return plans
      .filter((o) => o.id !== p.id)
      .map((o) => {
        const os = new Date(o.start_dt).getTime();
        const oe = new Date(o.end_dt).getTime();
        const ms = Math.min(e, oe) - Math.max(s, os); // 판정은 밀리초 차이로
        return { plan: o, ms, minutes: Math.max(1, Math.round(ms / 60000)) }; // 분은 표시용
      })
      .filter((x) => x.ms > 0);
  }

  function basisShort(p) {
    if (!p.basis) return "";
    const first = String(p.basis).split(" / ")[0];
    return first.length > 60 ? first.slice(0, 60) + "…" : first;
  }

  function isPast(p) {
    return new Date(p.end_dt) < new Date();
  }

  function visiblePlans() {
    return plans.filter((p) => {
      if (filter === "upcoming") return !isPast(p);
      if (filter === "past") return isPast(p);
      if (filter === "calendar") return !!p.calendar_event_id;
      return true;
    });
  }

  function renderSummary() {
    const cal = plans.filter((p) => p.calendar_event_id).length;
    const ov = plans.filter((p) => overlapsOf(p).length).length;
    $summary.textContent = "저장 " + plans.length + " · 캘린더 등록 " + cal + " · 겹침 " + ov;
    const count = document.getElementById("rail-plan-count");
    if (count) { count.textContent = String(plans.length); count.hidden = false; }
  }

  function calendarFlag(p) {
    if (calResults[p.id] || p.calendar_event_id) return '<span class="flag flag-ok">' + I.check(12) + "캘린더 등록됨</span>";
    return '<span class="flag flag-none">캘린더 미등록</span>';
  }

  function renderList() {
    renderSummary();
    const items = visiblePlans().slice().sort((a, b) => new Date(a.start_dt) - new Date(b.start_dt));
    $empty.hidden = plans.length > 0;
    $list.innerHTML = "";
    if (plans.length && !items.length) {
      $list.innerHTML = '<p class="muted">이 필터에 해당하는 계획이 없습니다.</p>';
      return;
    }
    const groups = new Map();
    items.forEach((p) => {
      const d = nightDate(p);
      const k = dayKey(d);
      if (!groups.has(k)) groups.set(k, { date: d, items: [] });
      groups.get(k).items.push(p);
    });
    groups.forEach((g) => {
      const box = document.createElement("div");
      box.className = "day-group";
      box.innerHTML = '<div class="day-head"><span class="day">' + esc(dayLabel(g.date)) + '</span><span class="dday">' + esc(ddayText(g.date)) + "</span></div>";
      g.items.forEach((p) => {
        const ov = overlapsOf(p);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "plan-row";
        if (p.id === selectedId) btn.setAttribute("aria-current", "true");
        const sub = [p.location_label, KIND[p.event_kind] || p.event_kind, basisShort(p)].filter(Boolean).join(" · ");
        btn.innerHTML =
          '<div class="plan-time"><div class="t1">' + esc(F.fmtTime(p.start_dt)) + '</div><div class="t2">– ' + esc(F.fmtTime(p.end_dt)) + "</div></div>" +
          '<div class="plan-main"><div class="t">' + esc(p.event_title) + '</div><div class="s">' + esc(sub) + "</div></div>" +
          '<div class="plan-flags">' + calendarFlag(p) +
          (ov.length ? '<span class="flag flag-warn">' + I.alert(13) + esc(ov.length === 1
            ? "'" + ov[0].plan.event_title + "' 계획과 " + ov[0].minutes + "분 겹침"
            : "다른 계획 " + ov.length + "건과 겹침") + "</span>" : "") +
          "</div>";
        btn.addEventListener("click", () => select(p.id, true));
        box.appendChild(btn);
      });
      $list.appendChild(box);
    });
  }

  function select(id, reveal) {
    selectedId = id;
    renderList();
    renderDetail();
    if (reveal && id != null && window.matchMedia("(max-width: 1080px)").matches) {
      $detail.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function calendarCardHtml(p) {
    const r = calResults[p.id];
    let status;
    let note;
    if (r) {
      const label = r.already_registered ? "이미 캘린더에 등록됨" : "캘린더에 등록됨" + (r.provider ? " (" + r.provider + ")" : "");
      const link = r.html_link && /^https:\/\//.test(r.html_link)
        ? '<a href="' + esc(r.html_link) + '" target="_blank" rel="noopener">일정 열기 ' + I.external(12) + "</a>" : "";
      status = '<span class="flag flag-ok">' + I.check(14) + "</span><b>" + esc(label) + "</b>";
      note = link;
    } else if (p.calendar_event_id) {
      status = '<span class="flag flag-ok">' + I.check(14) + "</span><b>캘린더에 등록됨</b>";
      note = "";
    } else {
      status = I.calendar(16) + "<b>캘린더 미등록</b>";
      note = "";
    }
    return '<div class="info-card" id="cal-card">' +
      '<div class="row">' + status + "</div>" +
      (note ? "<div>" + note + "</div>" : "") +
      '<div class="note">제목과 시각은 이 계획에서 읽어 등록합니다. 계획을 수정해도 이미 등록된 일정은 자동으로 바뀌지 않습니다.</div>' +
      '<div class="row"><button type="button" class="btn btn-sm" id="cal-btn">' + I.calendar(16) + "캘린더 등록</button></div>" +
      '<p class="form-err" id="cal-err" hidden></p></div>';
  }

  function renderDetail() {
    const p = plans.find((x) => x.id === selectedId);
    if (!p) {
      $detail.innerHTML = DRAWER_EMPTY;
      return;
    }
    const mins = minutesOfNight(p);
    const ov = overlapsOf(p);
    $detail.innerHTML =
      '<div class="drawer-head"><div><div class="drawer-kicker">계획 #' + esc(p.id) + " · " + esc(F.fmtDate(p.created_at)) + " 생성</div>" +
      '<h2 class="drawer-title">' + esc(p.event_title) + "</h2></div>" +
      '<button type="button" class="btn btn-icon" id="close-detail" aria-label="상세 닫기">' + I.close(18) + "</button></div>" +
      UI.miniBar(mins.start, mins.end) +
      '<dl class="meta-list">' +
      "<dt>종류</dt><dd>" + esc(KIND[p.event_kind] || p.event_kind) + "</dd>" +
      '<dt>시간</dt><dd class="num">' + esc(F.fmtDateTime(p.start_dt) + " – " + F.fmtDateTime(p.end_dt)) + "</dd>" +
      "<dt>장소</dt><dd>" + esc(p.location_label) +
      (p.lat != null && p.lon != null ? ' <small class="muted num">(' + esc(p.lat.toFixed(3)) + ", " + esc(p.lon.toFixed(3)) + ")</small>" : "") +
      (p.place_url && /^https?:\/\//.test(p.place_url) ? ' · <a href="' + esc(p.place_url) + '" target="_blank" rel="noopener">장소 링크</a>' : "") +
      "</dd>" +
      '<dt>수정</dt><dd class="num">' + esc(F.fmtDateTime(p.updated_at)) + "</dd>" +
      "</dl>" +
      '<form id="edit-form" class="edit-form" novalidate>' +
      '<label class="field full">제목<input name="event_title" required value="' + esc(p.event_title) + '"></label>' +
      '<label class="field">시작<input type="datetime-local" name="start_dt" required value="' + esc(F.toInputValue(p.start_dt)) + '"></label>' +
      '<label class="field">종료<input type="datetime-local" name="end_dt" required value="' + esc(F.toInputValue(p.end_dt)) + '"></label>' +
      '<label class="field full">장소<input name="location_label" required value="' + esc(p.location_label) + '"></label>' +
      '<div class="basis-box full"><div class="k">판단 근거 (저장 시점)</div>' + esc(p.basis || "-") + "</div>" +
      '<p class="form-err full" id="edit-err" hidden></p>' +
      '<div class="form-actions full"><button type="submit" class="btn btn-primary">변경 저장</button>' +
      '<button type="button" class="btn btn-danger push" id="delete-btn">삭제</button></div>' +
      '<div class="confirm full" id="confirm-box" hidden><span>이 계획을 삭제할까요? 되돌릴 수 없습니다.</span>' +
      '<button type="button" class="btn btn-danger-fill btn-sm" id="confirm-yes">삭제</button>' +
      '<button type="button" class="btn btn-sm" id="confirm-no">취소</button></div>' +
      "</form>" +
      calendarCardHtml(p) +
      (ov.length ? '<div class="banner banner-cond">' + I.alert(16) + "<span>" +
        ov.map((x) => "<b>" + esc(x.plan.event_title) + '</b>(<span class="num">' + esc(F.fmtTime(x.plan.start_dt) + "–" + F.fmtTime(x.plan.end_dt)) + "</span>) 계획과 " + x.minutes + "분").join(", ") +
        " 겹칩니다. 저장은 막지 않았습니다.</span></div>" : "");

    document.getElementById("close-detail").addEventListener("click", () => select(null));
    document.getElementById("cal-btn").addEventListener("click", () => registerCalendar(p));
    const form = document.getElementById("edit-form");
    const err = document.getElementById("edit-err");
    const confirmBox = document.getElementById("confirm-box");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(form);
      const title = String(fd.get("event_title") || "").trim();
      const loc = String(fd.get("location_label") || "").trim();
      const start = fd.get("start_dt");
      const end = fd.get("end_dt");
      if (!title || !loc || !start || !end) {
        err.textContent = "제목 · 시작 · 종료 · 장소를 모두 입력해 주세요.";
        err.hidden = false;
        return;
      }
      if (new Date(end) <= new Date(start)) {
        err.textContent = "종료 시각은 시작 시각보다 뒤여야 합니다.";
        err.hidden = false;
        return;
      }
      err.hidden = true;
      try {
        const updated = await window.Api.patch("/api/plans/" + p.id, {
          event_title: title,
          start_dt: start + ":00",
          end_dt: end + ":00",
          location_label: loc,
        });
        plans = plans.map((x) => (x.id === updated.id ? updated : x));
        renderList();
        renderDetail();
        flash("계획을 수정했습니다.");
      } catch (e) {
        err.textContent = e.message;
        err.hidden = false;
      }
    });

    document.getElementById("delete-btn").addEventListener("click", () => {
      confirmBox.hidden = false;
      document.getElementById("confirm-no").focus();
    });
    document.getElementById("confirm-no").addEventListener("click", () => { confirmBox.hidden = true; });
    document.getElementById("confirm-yes").addEventListener("click", async () => {
      try {
        await window.Api.del("/api/plans/" + p.id);
        plans = plans.filter((x) => x.id !== p.id);
        selectedId = null;
        renderList();
        renderDetail();
        flash("계획을 삭제했습니다.");
      } catch (e) {
        confirmBox.hidden = true;
        err.textContent = e.message;
        err.hidden = false;
      }
    });
  }

  async function registerCalendar(p) {
    const btn = document.getElementById("cal-btn");
    const err = document.getElementById("cal-err");
    btn.disabled = true;
    err.hidden = true;
    try {
      const res = await window.Api.post("/api/plans/" + p.id + "/calendar");
      const d = res.data;
      calResults[p.id] = d;
      plans = plans.map((x) => (x.id === p.id ? Object.assign({}, x, { calendar_event_id: d.event_id }) : x));
      renderList();
      renderDetail();
      flash(d.already_registered ? "이미 캘린더에 등록된 계획입니다." : "캘린더에 등록했습니다.");
    } catch (e) {
      // 상세 오류는 노출하지 않는다
      err.textContent = e.status === 503 ? AUTH_HINT : e.message;
      err.hidden = false;
      btn.disabled = false;
    }
  }

  async function load() {
    try {
      plans = await window.Api.get("/api/plans");
      if (selectedId != null && !plans.some((p) => p.id === selectedId)) selectedId = null;
      renderList();
      renderDetail();
    } catch (e) {
      flash(e.message, "err");
    }
  }

  document.querySelectorAll("[data-filter]").forEach((b) =>
    b.addEventListener("click", () => {
      filter = b.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      renderList();
    }));
  document.getElementById("reload-btn").addEventListener("click", load);
  load();
})();
