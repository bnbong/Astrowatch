// 실행 로그 (F3014): 세션 목록 · 검색 · 이벤트 조회(유형 필터) · 삭제. 수정 기능은 두지 않는다.
// URL 해시 #<session_id> 로 열면 그 세션을 바로 선택한다.
// 심화 F3020: 세션 목록의 판정 배지, 상세의 "로깅 Agent 판정" 패널(경고와 진단을 나눠 표시), "지금 판정" 버튼.
(function () {
  "use strict";

  const esc = window.Fmt.escapeHtml;
  const F = window.Fmt;
  const I = window.UI.Icons;

  const $list = document.getElementById("session-list");
  const $empty = document.getElementById("sessions-empty");
  const $detail = document.getElementById("session-detail");
  const $msg = document.getElementById("page-msg");
  const $search = document.getElementById("session-search");
  const $count = document.getElementById("sessions-count");

  const DETAIL_EMPTY = '<p class="drawer-empty">왼쪽에서 세션을 고르면 이벤트가 발생 순서대로 표시됩니다. 실행 로그는 조회와 삭제만 할 수 있습니다.</p>';

  const FILTERS = [
    { key: "all", label: "전체", cls: "", test: () => true },
    { key: "stage", label: "단계", cls: "f-stage", test: (e) => e.type === "stage_start" || e.type === "stage_end" },
    { key: "tool", label: "Tool", cls: "f-tool", test: (e) => e.type === "tool_call" || e.type === "tool_result" },
    { key: "llm", label: "LLM 판단", cls: "f-llm", test: (e) => e.type === "llm_decision" },
    { key: "fail", label: "실패", cls: "f-fail", test: (e) => isFail(e) },
    { key: "replan", label: "재계획", cls: "f-replan", test: (e) => e.type === "replan" },
    { key: "audit", label: "판정", cls: "f-audit", test: (e) => isAudit(e) },
  ];

  // ── 로깅 Agent 판정 (F3020) ──
  const KIND_KO = { repeated_call: "같은 입력 반복 호출", excessive_replan: "재계획 과다", unplanned_call: "계획 외 Tool 호출" };
  const CULPRIT_KO = { planner: "Planner(계획)", worker: "Worker(실행)", tool: "Tool(외부 응답)", unknown: "불명" };

  function auditBadge(status, warnings) {
    if (status === "normal") return '<span class="audit-badge a-normal">정상</span>';
    if (status === "anomaly") return '<span class="audit-badge a-anomaly">이상 ' + esc(warnings || 0) + "건</span>";
    if (status === "undetermined") return '<span class="audit-badge a-undetermined">판정 불가</span>';
    return '<span class="audit-badge a-none">판정 없음</span>';
  }

  let sessions = [];
  let selected = null;
  let events = [];
  let typeFilter = "all";
  let audits = []; // 선택한 세션의 판정 목록 (최신 먼저)
  const failCounts = {}; // session_id → 이벤트를 불러온 뒤 알게 된 실패 수

  function flash(text, kind) {
    $msg.innerHTML = (kind === "err" ? I.alert(16) : I.check(14)) + "<span>" + esc(text) + "</span>";
    $msg.className = "banner " + (kind === "err" ? "banner-bad" : "banner-ok");
    $msg.hidden = false;
  }

  function isAudit(e) {
    return e.type === "audit_result" || e.type === "audit_diagnosis";
  }

  function isFail(e) {
    return e.type === "error" || (e.payload && e.payload.ok === false);
  }

  function fmtClock(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    const p = (n) => String(n).padStart(2, "0");
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function payloadSummary(payload) {
    if (!payload || Object.keys(payload).length === 0) return "";
    const p = payload;
    const parts = [];
    ["summary", "decision", "reason", "message", "strategy", "error", "note"].forEach((k) => {
      if (p[k] != null && typeof p[k] !== "object") parts.push(String(p[k]));
    });
    if (p.ok === false) parts.unshift("실패");
    const text = parts.join(" · ") || Object.keys(p).join(", ");
    return text.length > 120 ? text.slice(0, 120) + "…" : text;
  }

  function typeClass(e) {
    if (isFail(e)) return "t-fail";
    if (e.type === "stage_start" || e.type === "stage_end") return "t-stage";
    if (e.type === "tool_call" || e.type === "tool_result") return "t-tool";
    if (e.type === "llm_decision") return "t-llm";
    if (e.type === "replan") return "t-replan";
    if (isAudit(e)) return "t-audit";
    return "";
  }

  // ── 세션 목록 ──
  function filteredSessions() {
    const q = $search.value.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) =>
      s.session_id.toLowerCase().includes(q) || String(s.first_message || "").toLowerCase().includes(q));
  }

  function renderList() {
    $empty.hidden = sessions.length > 0;
    $count.textContent = sessions.length ? "세션 " + sessions.length : "";
    const items = filteredSessions();
    $list.innerHTML = "";
    if (sessions.length && !items.length) {
      $list.innerHTML = '<li class="muted">검색 결과가 없습니다.</li>';
      return;
    }
    items.forEach((s) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "session-card";
      if (s.session_id === selected) btn.setAttribute("aria-current", "true");
      const fails = failCounts[s.session_id];
      btn.innerHTML =
        '<div class="top"><span class="num">' + esc(s.session_id.slice(0, 8)) + '</span><span class="time">' + esc(F.fmtDateTime(s.started_at)) + "</span></div>" +
        '<div class="msg">' + esc(s.first_message || "(첫 메시지 없음)") + "</div>" +
        '<div class="mini-chips"><span class="mini-chip">' + esc(s.event_count) + " 이벤트</span>" +
        '<span class="mini-chip c-tool">Tool ' + esc(s.tool_calls) + "</span>" +
        '<span class="mini-chip' + (s.replans ? " c-replan" : "") + '">재계획 ' + esc(s.replans) + "</span>" +
        (fails ? '<span class="mini-chip c-fail">실패 ' + esc(fails) + "</span>" : "") +
        auditBadge(s.audit_status, s.audit_warnings) + "</div>";
      btn.addEventListener("click", () => select(s.session_id, true));
      li.appendChild(btn);
      $list.appendChild(li);
    });
  }

  async function select(sessionId, reveal) {
    selected = sessionId;
    typeFilter = "all";
    renderList();
    try {
      history.replaceState(null, "", sessionId ? "#" + encodeURIComponent(sessionId) : location.pathname);
    } catch (_) { /* 무시 */ }
    if (!sessionId) {
      $detail.innerHTML = DETAIL_EMPTY;
      return;
    }
    $detail.innerHTML = '<p class="status"><span class="spinner" aria-hidden="true"></span> 불러오는 중…</p>';
    if (reveal && window.matchMedia("(max-width: 1080px)").matches) {
      $detail.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    try {
      const list = await window.Api.get("/api/logs/" + encodeURIComponent(sessionId));
      if (selected !== sessionId) return; // 그 사이 다른 세션을 골랐으면 버린다
      try {
        audits = await window.Api.get("/api/logs/" + encodeURIComponent(sessionId) + "/audits");
      } catch (_) {
        audits = []; // 판정을 못 읽어도 로그는 보여 준다
      }
      if (selected !== sessionId) return;
      events = list.slice().sort((a, b) => a.seq - b.seq);
      failCounts[sessionId] = events.filter(isFail).length;
      renderList();
      renderEvents(sessionId);
    } catch (e) {
      if (selected !== sessionId) return;
      $detail.innerHTML = '<div class="banner banner-bad">' + I.alert(16) + "<span>" + esc(e.message) + "</span></div>";
    }
  }

  // ── 이벤트 표 ──
  function rowsHtml() {
    const f = FILTERS.find((x) => x.key === typeFilter) || FILTERS[0];
    const rows = events.filter(f.test);
    if (!rows.length) return '<tr><td colspan="6" class="muted">이 유형의 이벤트가 없습니다.</td></tr>';
    return rows.map((e) => {
      const fail = isFail(e);
      const hasPayload = e.payload && Object.keys(e.payload).length > 0;
      const where = [e.stage, e.tool].filter(Boolean).join(" · ");
      const ms = e.payload && e.payload.duration_ms != null ? e.payload.duration_ms : null;
      return '<tr id="ev-' + esc(e.seq) + '" class="' + (fail ? "row-fail" : "") + (e.type === "replan" ? " row-replan" : "") + '">' +
        '<td class="c-num">' + esc(e.seq) + "</td>" +
        '<td class="c-time">' + esc(fmtClock(e.ts)) + "</td>" +
        '<td><span class="type-chip ' + typeClass(e) + '">' + esc(e.type) + "</span></td>" +
        '<td class="c-where">' + (where ? esc(where) : '<span class="muted">—</span>') + "</td>" +
        '<td class="c-sum">' + (hasPayload
          ? "<details><summary>" + esc(payloadSummary(e.payload)) + "</summary><pre>" + esc(JSON.stringify(e.payload, null, 2)) + "</pre></details>"
          : '<span class="muted">—</span>') + "</td>" +
        '<td class="n">' + (ms != null ? esc(ms) : '<span class="muted">—</span>') + "</td></tr>";
    }).join("");
  }

  function renderEvents(sessionId) {
    const s = sessions.find((x) => x.session_id === sessionId) || {};
    let span = "";
    if (s.started_at && s.last_at) {
      const sec = (new Date(s.last_at) - new Date(s.started_at)) / 1000;
      span = F.fmtDateTime(s.started_at) + " → " + F.fmtTime(s.last_at) + (isFinite(sec) ? " · " + sec.toFixed(1) + "초" : "");
    }
    $detail.innerHTML =
      '<header class="events-head"><div>' +
      '<div class="drawer-kicker">세션 ' + esc(sessionId.slice(0, 8)) + (span ? " · " + esc(span) : "") + "</div>" +
      "<h2>" + esc(s.first_message || "세션 " + sessionId.slice(0, 12)) + "</h2></div>" +
      '<button type="button" class="btn btn-danger btn-sm" id="delete-btn">이 세션 삭제</button></header>' +
      '<div class="confirm" id="confirm-box" hidden><span>이 세션의 실행 로그를 모두 삭제할까요? 되돌릴 수 없습니다.</span>' +
      '<button type="button" class="btn btn-danger-fill btn-sm" id="confirm-yes">삭제</button>' +
      '<button type="button" class="btn btn-sm" id="confirm-no">취소</button></div>' +
      '<section class="audit-panel" id="audit-panel" aria-label="로깅 Agent 판정">' + auditPanelHtml() + "</section>" +
      '<div class="filters" role="group" aria-label="유형 필터"><span class="filters-label">유형</span>' +
      FILTERS.map((f) => '<button type="button" class="chip ' + f.cls + '" data-type="' + f.key + '" aria-pressed="' + (f.key === typeFilter) + '">' +
        esc(f.label) + ' <span class="num">' + events.filter(f.test).length + "</span></button>").join("") + "</div>" +
      '<div class="table-wrap"><table class="table"><thead><tr>' +
      '<th scope="col">#</th><th scope="col">시각</th><th scope="col">유형</th><th scope="col">단계 · Tool</th><th scope="col">내용</th><th scope="col" class="n">ms</th>' +
      '</tr></thead><tbody id="events-body">' + rowsHtml() + "</tbody></table></div>" +
      '<p class="foot-note">로그는 로깅 Agent 가 이벤트를 받은 그대로 저장한 것이며 수정할 수 없습니다. 내용을 펼치면 payload 를 JSON 으로 봅니다.</p>';

    $detail.querySelectorAll("[data-type]").forEach((b) =>
      b.addEventListener("click", () => {
        typeFilter = b.dataset.type;
        $detail.querySelectorAll("[data-type]").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
        document.getElementById("events-body").innerHTML = rowsHtml();
      }));
    bindAuditPanel(sessionId);

    const confirmBox = document.getElementById("confirm-box");
    document.getElementById("delete-btn").addEventListener("click", () => {
      confirmBox.hidden = false;
      document.getElementById("confirm-no").focus();
    });
    document.getElementById("confirm-no").addEventListener("click", () => { confirmBox.hidden = true; });
    document.getElementById("confirm-yes").addEventListener("click", async () => {
      try {
        await window.Api.del("/api/logs/" + encodeURIComponent(sessionId));
        sessions = sessions.filter((x) => x.session_id !== sessionId);
        select(null);
        flash("실행 로그를 삭제했습니다.");
      } catch (e) {
        confirmBox.hidden = true;
        flash(e.message, "err");
      }
    });
  }

  function auditPanelHtml() {
    const a = audits[0];
    const head = '<div class="audit-head"><h3>로깅 Agent 판정</h3>' + (a ? auditBadge(a.status, a.findings.length) : auditBadge(null)) +
      '<button type="button" class="btn btn-sm" id="audit-run">지금 판정</button></div>';
    if (!a) {
      return head + '<p class="muted audit-note">아직 판정 기록이 없습니다. 규칙(같은 입력 반복, 재계획 과다, 계획 외 호출)으로 이 세션의 로그를 대조하려면 "지금 판정"을 누르세요.</p>';
    }
    const range = "seq " + a.seq_from + "~" + a.seq_to + ", 대조한 이벤트 " + a.evidence_count + "건, " + F.fmtDateTime(a.audited_at);
    let body = '<p class="audit-meta">' + esc(range) + "</p>";
    if (a.status === "undetermined") {
      body += '<p class="audit-note">근거 이벤트(Tool 호출, 단계 종료, 재계획)가 없어 판정할 수 없습니다. "이상 없음"이 아닙니다.</p>';
    } else if (!a.findings.length) {
      body += '<p class="audit-note">경고 0건. 규칙 3가지 모두 해당 없음.</p>';
    } else {
      body += '<div class="audit-sub">경고 ' + esc(a.findings.length) + '건 <span class="muted">(규칙 판정)</span></div><ul class="audit-findings">' +
        a.findings.map((f) =>
          '<li><span class="audit-kind">' + esc(KIND_KO[f.kind] || f.kind) + "</span> " + esc(f.message) +
          '<div class="audit-seqs">근거 ' + (f.evidence_seqs || []).map((n) =>
            '<button type="button" class="seq-link" data-seq="' + esc(n) + '">#' + esc(n) + "</button>").join(" ") + "</div></li>").join("") +
        "</ul>";
    }
    const d = a.diagnosis;
    if (d) {
      body += '<div class="audit-diag' + (d.failed ? " is-failed" : "") + '"><div class="audit-sub">진단 <span class="muted">(LLM' +
        (d.model ? ", " + esc(d.model) : "") + ", 경고 수에 포함하지 않음)</span></div>" +
        (d.failed ? "" : '<p class="audit-culprit">원인 쪽: <b>' + esc(CULPRIT_KO[d.culprit] || d.culprit) + "</b>" +
          (d.fix ? ", 고칠 점: " + esc(d.fix) : "") + "</p>") +
        "<p>" + esc(d.text) + "</p></div>";
    } else if (a.status === "anomaly") {
      body += '<p class="muted audit-note">진단 없음 (LLM 미설정 또는 AUDIT_LLM=0).</p>';
    }
    if (audits.length > 1) body += '<p class="muted audit-note">이전 판정 ' + esc(audits.length - 1) + "건은 API(/audits)로 볼 수 있습니다.</p>";
    return head + body;
  }

  function bindAuditPanel(sessionId) {
    const panel = document.getElementById("audit-panel");
    if (!panel) return;
    panel.querySelectorAll(".seq-link").forEach((b) => b.addEventListener("click", () => {
      if (typeFilter !== "all") $detail.querySelector('[data-type="all"]').click();
      const row = document.getElementById("ev-" + b.dataset.seq);
      if (!row) return;
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("row-flash");
      setTimeout(() => row.classList.remove("row-flash"), 1600);
    }));
    const run = document.getElementById("audit-run");
    run.addEventListener("click", async () => {
      run.disabled = true;
      run.textContent = "판정 중…";
      try {
        const result = await window.Api.post("/api/logs/" + encodeURIComponent(sessionId) + "/audit");
        if (selected !== sessionId) return;
        audits = [result].concat(audits.filter((x) => !(x.seq_from === result.seq_from && x.seq_to === result.seq_to)));
        const s = sessions.find((x) => x.session_id === sessionId);
        if (s) { s.audit_status = result.status; s.audit_warnings = result.findings.length; }
        renderList();
        panel.innerHTML = auditPanelHtml();
        bindAuditPanel(sessionId);
      } catch (e) {
        run.disabled = false;
        run.textContent = "지금 판정";
        flash(e.message, "err");
      }
    });
  }

  function hashSession() {
    try { return decodeURIComponent(location.hash.slice(1)) || null; } catch (_) { return null; }
  }

  async function load() {
    try {
      sessions = await window.Api.get("/api/logs");
      sessions.sort((a, b) => new Date(b.last_at) - new Date(a.last_at));
      renderList();
      events = []; // 새로고침이면 선택된 세션의 이벤트도 다시 불러온다
      applyHash(false);
    } catch (e) {
      flash(e.message, "err");
    }
  }

  $search.addEventListener("input", renderList);
  // 해시가 비면 선택 해제, 없는 세션 ID 면 선택 해제 + 상세 비우고 안내. 있으면 그 세션 선택.
  function applyHash(reveal) {
    const want = hashSession();
    if (!want) {
      if (selected !== null) select(null);
      else $detail.innerHTML = DETAIL_EMPTY;
      return;
    }
    if (!sessions.some((s) => s.session_id === want)) {
      selected = null;
      events = [];
      renderList();
      $detail.innerHTML = '<div class="banner banner-cond">' + I.alert(16) +
        "<span>세션을 찾을 수 없습니다. 삭제되었거나 주소가 잘못되었을 수 있습니다.</span></div>" + DETAIL_EMPTY;
      return;
    }
    if (want !== selected || !events.length) select(want, reveal);
  }
  window.addEventListener("hashchange", () => applyHash(true));
  document.getElementById("reload-btn").addEventListener("click", load);
  load();
})();
