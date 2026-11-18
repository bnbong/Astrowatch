"""frontend/js/chat.js — 최근 대화 복원 스모크 테스트.

node 로 api/markdown/ui/timeline/chat.js 를 가짜 DOM 위에 올리고, fetch 를 가짜로 바꿔
`window.Chat.restoreSession(id)` 가 턴 2개(사용자 말풍선 + 봇 응답)를 그리는지 확인한다.
샘플 응답은 samples/run_R1.json 을 GET /api/sessions/{id} 의 turns 모양으로 감싼다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONT_JS = ROOT / "frontend" / "js"
SAMPLE = ROOT / "samples" / "run_R1.json"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 없어 프론트 세션 복원 검사를 건너뜀")

# 최소한의 가짜 DOM. innerHTML 은 문자열로만 보관하고, appendChild 로 붙은 자식만 트리로 센다.
HARNESS = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const [jsDir, payloadPath] = process.argv.slice(1);
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

class El {
  constructor(tag, id) {
    this.tagName = String(tag || "div").toUpperCase();
    this.id = id || "";
    this.className = "";
    this.children = [];
    this.parent = null;
    this._html = "";
    this.textContent = "";
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 1000;
    const self = this;
    this.classList = {
      add: (c) => { if (!self.classList.contains(c)) self.className = (self.className + " " + c).trim(); },
      remove: (c) => { self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
      contains: (c) => self.className.split(/\s+/).includes(c),
      toggle: (c, on) => { const want = on === undefined ? !self.classList.contains(c) : on; want ? self.classList.add(c) : self.classList.remove(c); return want; },
    };
  }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get innerHTML() { return this._html + this.children.map((c) => c.innerHTML).join(""); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  querySelector() { return new El("div"); }
  querySelectorAll() { return []; }
  addEventListener() {}
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  contains(n) { for (let x = n; x; x = x.parent) if (x === this) return true; return false; }
  closest() { return null; }
  focus() {}
  scrollIntoView() {}
  requestSubmit() {}
}

const byId = {};
const document = {
  getElementById: (id) => byId[id] || (byId[id] = new El("div", id)),
  createElement: (tag) => new El(tag),
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
const calls = [];
function fetch(url, opts) {
  calls.push((opts && opts.method || "GET") + " " + url);
  const send = (status, body) => Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
  if (url === "/api/sessions") return send(200, payload.list);
  if (url === "/api/sessions/" + encodeURIComponent(payload.detail.session_id)) return send(200, payload.detail);
  return send(404, { detail: "not found" });
}

const ctx = { document, localStorage, fetch, console, setTimeout, clearTimeout, Promise };
ctx.window = ctx;
vm.createContext(ctx);
for (const f of ["api.js", "markdown.js", "ui.js", "timeline.js", "chat.js"]) {
  vm.runInContext(fs.readFileSync(path.join(jsDir, f), "utf8"), ctx, { filename: f });
}

(async () => {
  const ok = await ctx.Chat.restoreSession(payload.detail.session_id);
  await new Promise((r) => setTimeout(r, 0));
  const msgs = byId["messages"].children;
  process.stdout.write(JSON.stringify({
    ok,
    classes: msgs.map((m) => m.className),
    userTexts: msgs.filter((m) => m.className === "msg-user").map((m) => m._html),
    botHtml: msgs.filter((m) => m.className === "msg-bot").map((m) => m.innerHTML),
    stored: localStorage.getItem("astrowatch.session_id"),
    label: byId["session-label"].textContent,
    timelineItems: byId["timeline"].children.length ? byId["timeline"].children[0].children.length : 0,
    convoHtml: byId["convo-list"].innerHTML,
    logHref: byId["sum-log"].href,
    calls,
  }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _sample_turns() -> list[tuple[str, dict]]:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    if isinstance(data.get("turns"), list) and data["turns"]:
        turns = [(t.get("message") or t.get("user_message") or "", t["response"]) for t in data["turns"]]
    else:  # 예전 모양: ChatResponse 하나
        turns = [("12월 14일 밤 서울에서 쌍둥이자리 유성우 볼 수 있어?", data)]
    while len(turns) < 2:
        turns.append(("다른 날도 알려 줘", turns[0][1]))
    return turns[:2]


def test_restore_session_draws_two_turns(tmp_path):
    sid = "sess-smoke-01"
    turns = _sample_turns()
    detail = {
        "session_id": sid,
        "title": turns[0][0][:60],
        "started_at": "2026-11-18T20:00:00",
        "last_at": "2026-11-18T20:05:00",
        "turns": [
            {"session_id": sid, "seq": i + 1, "ts": "2026-11-18T20:0%d:00" % i, "user_message": msg,
             "response": dict(resp, session_id=sid)}
            for i, (msg, resp) in enumerate(turns)
        ],
    }
    listing = [{"session_id": sid, "title": detail["title"], "started_at": detail["started_at"],
                "last_at": detail["last_at"], "turn_count": 2, "last_intent": "plan"}]
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"detail": detail, "list": listing}, ensure_ascii=False), encoding="utf-8")

    out = subprocess.run(
        [NODE, "-e", HARNESS, str(FRONT_JS), str(payload)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)

    assert res["ok"] is True
    assert res["classes"] == ["msg-user", "msg-bot", "msg-user", "msg-bot"]  # 환영 말풍선은 지워진다
    assert res["userTexts"][0].startswith(turns[0][0][:10].replace("<", "&lt;"))
    first_title = (turns[0][1].get("recommended") or [{}])[0].get("candidate", {}).get("title")
    if first_title:
        assert first_title in res["botHtml"][0]  # 추천 카드까지 복원
    assert res["stored"] == sid
    assert res["label"] == "세션 " + sid[:8]
    assert res["timelineItems"] > 0  # 마지막 턴 steps 로 타임라인 복원
    assert "is-current" in res["convoHtml"] and "2턴" in res["convoHtml"]
    assert res["logHref"] == "logs.html#" + sid
    assert "GET /api/sessions/" + sid in res["calls"]
