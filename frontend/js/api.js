// fetch 래퍼 + 공통 유틸. 실패 시 사용자용 짧은 메시지만 던진다 (상세 오류 노출 금지).
// 보안: 모든 요청에 X-Requested-With: Astrowatch (서버의 CSRF 2차 방어).
// 서버에 APP_ACCESS_TOKEN 이 설정돼 있으면 401 을 받았을 때 토큰 입력 폼을 띄우고
// sessionStorage 에 저장한 뒤 X-Astrowatch-Token 헤더로 다시 보낸다 (탭을 닫으면 사라진다).
(function () {
  "use strict";

  const TOKEN_KEY = "astrowatch.access_token";

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  function friendlyMessage(status) {
    if (status === 401) return "접근 토큰이 필요합니다.";
    if (status === 403) return "허용되지 않은 요청입니다.";
    if (status === 404) return "요청한 항목을 찾을 수 없습니다.";
    if (status === 413) return "요청이 너무 큽니다.";
    if (status === 429) return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.";
    if (status === 422 || status === 400 || status === 415) return "입력값을 확인해 주세요.";
    if (status === 503) return "일시적으로 사용할 수 없는 기능입니다.";
    if (status >= 500) return "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
    return "요청을 처리하지 못했습니다.";
  }

  function readToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; }
  }
  function saveToken(token) {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (_) { /* 저장소 불가 시 이번 페이지에서만 사용 */ }
    memoryToken = token || "";
  }
  let memoryToken = readToken();

  // 토큰 입력 폼 — DOM API 로만 만든다(innerHTML 없음). 동시에 여러 요청이 401 을 받아도 폼은 하나.
  let pendingPrompt = null;
  function askToken(wasRejected) {
    if (pendingPrompt) return pendingPrompt;
    pendingPrompt = new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "token-overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.setAttribute("aria-labelledby", "token-title");
      Object.assign(overlay.style, {
        position: "fixed", inset: "0", background: "rgba(0,0,0,.45)", display: "flex",
        alignItems: "center", justifyContent: "center", zIndex: "1000", padding: "16px",
      });
      const form = document.createElement("form");
      form.className = "token-form";
      Object.assign(form.style, {
        background: "var(--surface, #fff)", color: "var(--ink, #111)", borderRadius: "12px",
        padding: "20px", maxWidth: "360px", width: "100%", boxShadow: "0 8px 30px rgba(0,0,0,.25)",
        display: "flex", flexDirection: "column", gap: "10px",
      });
      const title = document.createElement("h2");
      title.id = "token-title";
      title.textContent = "접근 토큰";
      title.style.margin = "0";
      title.style.fontSize = "1.05rem";
      const desc = document.createElement("p");
      desc.style.margin = "0";
      desc.textContent = wasRejected
        ? "토큰이 맞지 않습니다. 다시 입력해 주세요."
        : "이 서버는 접근 토큰이 필요합니다. 관리자에게 받은 토큰을 입력하세요.";
      const input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "off";
      input.required = true;
      input.setAttribute("aria-label", "접근 토큰");
      input.style.padding = "8px";
      const row = document.createElement("div");
      Object.assign(row.style, { display: "flex", gap: "8px", justifyContent: "flex-end" });
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "btn btn-sm";
      cancel.textContent = "취소";
      const ok = document.createElement("button");
      ok.type = "submit";
      ok.className = "btn btn-sm btn-primary";
      ok.textContent = "확인";
      row.append(cancel, ok);
      form.append(title, desc, input, row);
      overlay.appendChild(form);

      const finish = (value) => {
        overlay.remove();
        pendingPrompt = null;
        resolve(value);
      };
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const v = input.value.trim();
        if (v) finish(v);
      });
      cancel.addEventListener("click", () => finish(""));
      overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") finish(""); });
      (document.body || document.documentElement).appendChild(overlay);
      input.focus();
    });
    return pendingPrompt;
  }

  async function send(method, path, body) {
    const opts = { method, headers: { "X-Requested-With": "Astrowatch" }, credentials: "same-origin" };
    if (memoryToken) opts.headers["X-Astrowatch-Token"] = memoryToken;
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    try {
      return await fetch(path, opts);
    } catch (_) {
      throw new ApiError("서버에 연결할 수 없습니다.", 0);
    }
  }

  async function request(method, path, body) {
    let res = await send(method, path, body);
    // 401 → 토큰을 받아 최대 3번까지 다시 시도 (취소하면 중단)
    for (let attempt = 0; res.status === 401 && attempt < 3 && typeof document !== "undefined"; attempt++) {
      const token = await askToken(!!memoryToken);
      if (!token) break;
      saveToken(token);
      res = await send(method, path, body);
    }
    if (res.status === 401) saveToken("");
    if (!res.ok) throw new ApiError(friendlyMessage(res.status), res.status);
    if (res.status === 204) return null;
    try {
      return await res.json();
    } catch (_) {
      throw new ApiError("응답을 해석하지 못했습니다.", res.status);
    }
  }

  // ── 표시용 유틸 ──
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Window.label 과 같은 규칙: 1560 → "02:00+1"
  function minuteLabel(minute) {
    if (minute == null) return "-";
    const h = Math.floor(minute / 60);
    const m = minute % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return pad(h % 24) + ":" + pad(m) + (h >= 24 ? "+1" : "");
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  // "2026-12-14T22:10:00" → "2026-12-14 22:10"
  function fmtDateTime(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function fmtTime(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function fmtDate(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  // naive 로컬 ISO 문자열 (서버는 현지 naive 를 기대)
  function toLocalIso(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      "T" + pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":00";
  }

  // datetime-local 입력값 ("YYYY-MM-DDTHH:MM")
  function toInputValue(value) {
    if (!value) return "";
    const d = new Date(value);
    if (isNaN(d)) return "";
    return toLocalIso(d).slice(0, 16);
  }

  window.Api = {
    ApiError,
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    patch: (path, body) => request("PATCH", path, body),
    del: (path) => request("DELETE", path),
  };
  window.Fmt = { escapeHtml, minuteLabel, fmtDateTime, fmtTime, fmtDate, toLocalIso, toInputValue };
})();
