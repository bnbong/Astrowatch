// 실행 타임라인 (F3019): 단계 · Tool 호출 · 재계획 지점을 순서대로 보여 준다.
// 모양으로 구분: 단계 ○(ink) · Tool □(성공 ok / 실패 bad) · LLM 판단 ○ 안 "LLM"(replan 색) · 재계획 구분선.
(function () {
  "use strict";

  const STAGE_NAME = {
    analyze: "요청 분석", plan: "작업 계획", execute: "Tool 실행",
    judge: "판정", respond: "응답 작성", replan: "재계획",
  };

  const TYPE_META = {
    stage_start: { label: "단계 시작", kind: "stage" },
    stage_end: { label: "단계 종료", kind: "stage" },
    tool_call: { label: "Tool 호출", kind: "tool" },
    tool_result: { label: "Tool 응답", kind: "tool" },
    llm_decision: { label: "LLM 판단", kind: "llm" },
    replan: { label: "재계획", kind: "replan" },
    error: { label: "오류", kind: "error" },
  };

  function fmtMs(ms) {
    if (ms == null) return "";
    return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
  }

  function iconHtml(s, meta, failed) {
    const I = window.UI.Icons;
    if (failed) return '<span class="tl-icon i-fail" title="실패">' + I.x(11) + "</span>";
    if (meta.kind === "tool") {
      return s.type === "tool_call"
        ? '<span class="tl-icon i-tool i-tool-call" title="Tool 호출"></span>'
        : '<span class="tl-icon i-tool" title="Tool 응답 성공">' + I.check(11) + "</span>";
    }
    if (meta.kind === "llm") return '<span class="tl-icon i-llm" title="LLM 판단">LLM</span>';
    return '<span class="tl-icon i-stage' + (s.type === "stage_start" ? " i-stage-start" : "") + '" title="' + meta.label + '"></span>';
  }

  function render(container, steps) {
    const esc = window.Fmt.escapeHtml;
    container.innerHTML = "";
    if (!steps || steps.length === 0) {
      container.innerHTML = '<p class="empty">표시할 실행 단계가 없습니다.</p>';
      return;
    }
    const sorted = steps.slice().sort((a, b) => a.seq - b.seq);
    const list = document.createElement("ol");
    list.className = "timeline";
    let replanSeen = 0;

    sorted.forEach((s) => {
      if (s.type === "replan") {
        replanSeen += 1;
        const round = s.replan_round || replanSeen;
        const sep = document.createElement("li");
        sep.className = "tl-sep";
        sep.innerHTML = "<span>재계획 " + esc(round) + "회차</span>" +
          (s.summary ? "<small>" + esc(s.summary) + "</small>" : "");
        list.appendChild(sep);
        return;
      }
      const meta = TYPE_META[s.type] || { label: s.type, kind: "stage" };
      const failed = s.ok === false || s.type === "error";
      const li = document.createElement("li");
      li.className = "tl-item";
      if (failed) li.classList.add("is-fail");
      const round = s.replan_round || (replanSeen > 0 ? replanSeen : null);
      if (round) li.classList.add("in-replan");

      let head;
      if (meta.kind === "tool" && s.tool) {
        head = "<code>" + esc(s.tool) + "</code>" + (s.type === "tool_call" ? ' <span class="tl-ms">호출</span>' : "");
      } else if (meta.kind === "stage") {
        const name = STAGE_NAME[s.stage] || s.stage || meta.label;
        head = "<b>" + esc(name) + "</b> " + '<span class="tl-ms">' + (s.type === "stage_start" ? "시작" : "완료") + "</span>";
      } else if (meta.kind === "llm") {
        head = "<b>판단</b>" + (s.stage ? ' <span class="tl-ms">' + esc(STAGE_NAME[s.stage] || s.stage) + "</span>" : "");
      } else {
        head = "<b>" + esc(meta.label) + "</b>" + (s.tool ? " <code>" + esc(s.tool) + "</code>" : "");
      }
      const tags = [];
      if (s.duration_ms != null) tags.push('<span class="tl-ms">' + esc(fmtMs(s.duration_ms)) + "</span>");
      if (failed) tags.push('<span class="tl-tag is-fail">실패</span>');
      if (round) tags.push('<span class="tl-tag is-replan">재계획 ' + esc(round) + "</span>");

      li.innerHTML = iconHtml(s, meta, failed) +
        '<div class="tl-body"><div class="tl-head">' + head + " " + tags.join(" ") + "</div>" +
        (s.summary ? '<div class="tl-summary">' + esc(s.summary) + "</div>" : "") + "</div>";
      list.appendChild(li);
    });
    container.appendChild(list);
  }

  // 요약 카드용 집계: tool_result 기준 호출 수·실패 수 (tool_result 가 없으면 tool_call 수)
  function stats(steps) {
    const list = steps || [];
    const results = list.filter((s) => s.type === "tool_result");
    const calls = results.length || list.filter((s) => s.type === "tool_call").length;
    const failed = list.filter((s) => (s.type === "tool_result" && s.ok === false) || s.type === "error").length;
    return { calls, failed };
  }

  window.Timeline = { render, stats, STAGE_NAME };
})();
