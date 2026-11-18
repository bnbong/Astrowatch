// 안전한 Markdown 부분집합 렌더러 (R8).
// 원칙: 먼저 전부 HTML 이스케이프한 뒤, 이스케이프된 문자열 위에서만 아래 문법을 태그로 바꾼다.
//   **굵게** → <strong> · `코드` → <code> · "- "/"* " 줄 → <ul><li> · "1. " 줄 → <ol><li>
//   빈 줄 → 문단 구분 · 나머지 줄바꿈 → <br>
// 링크 · 이미지 · HTML 태그는 변환하지 않는다 (<, > 는 이미 &lt; &gt; 로 바뀌어 있다).
(function (root) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // 이미 이스케이프된 한 줄에 인라인 서식 적용. 코드 구간 안은 굵게 변환하지 않는다.
  function inline(escaped) {
    return escaped
      .split(/(`[^`\n]+`)/)
      .map((part) => {
        if (part.length > 2 && part[0] === "`" && part[part.length - 1] === "`") {
          return "<code>" + part.slice(1, -1) + "</code>";
        }
        return part.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
      })
      .join("");
  }

  const UL_RE = /^\s*[-*]\s+(.*)$/;
  const OL_RE = /^\s*\d+[.)]\s+(.*)$/;

  function render(text) {
    const lines = escapeHtml(text).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let para = [];
    let list = null; // { tag: "ul"|"ol", items: [] }

    function flushPara() {
      if (para.length) out.push("<p>" + para.map(inline).join("<br>") + "</p>");
      para = [];
    }
    function flushList() {
      if (list) {
        out.push("<" + list.tag + ">" + list.items.map((i) => "<li>" + inline(i) + "</li>").join("") + "</" + list.tag + ">");
      }
      list = null;
    }

    lines.forEach((line) => {
      let m;
      if (line.trim() === "") {
        flushPara();
        flushList();
      } else if ((m = UL_RE.exec(line)) || (m = OL_RE.exec(line))) {
        const tag = UL_RE.test(line) ? "ul" : "ol";
        flushPara();
        if (list && list.tag !== tag) flushList();
        if (!list) list = { tag, items: [] };
        list.items.push(m[1]);
      } else {
        flushList();
        para.push(line);
      }
    });
    flushPara();
    flushList();
    return out.join("");
  }

  // 카드 사유처럼 한 줄짜리 문자열용: 문단 · 목록 없이 인라인 서식만.
  function renderInline(text) {
    return inline(escapeHtml(text)).replace(/\r?\n/g, "<br>");
  }

  const api = { render, renderInline, escapeHtml };
  root.Markdown = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
