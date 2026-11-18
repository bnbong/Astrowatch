// fetch 래퍼 + 공통 유틸. 실패 시 사용자용 짧은 메시지만 던진다 (상세 오류 노출 금지).
(function () {
  "use strict";

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  function friendlyMessage(status) {
    if (status === 404) return "요청한 항목을 찾을 수 없습니다.";
    if (status === 422 || status === 400) return "입력값을 확인해 주세요.";
    if (status === 503) return "일시적으로 사용할 수 없는 기능입니다.";
    if (status >= 500) return "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
    return "요청을 처리하지 못했습니다.";
  }

  async function request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (_) {
      throw new ApiError("서버에 연결할 수 없습니다.", 0);
    }
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
