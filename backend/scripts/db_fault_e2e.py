"""셀프 체크리스트 17번: 실제 DB 쓰기 장애 중에도 메인 Agent 가 정상 응답하는지 (E2E, HTTP).

    .venv/Scripts/python -X utf8 backend/scripts/db_fault_e2e.py [--port 8010] [--shot]

절차
  1. 임시 DB 로 서버를 띄운다 (실제 DB 는 건드리지 않는다)
  2. 정상 턴 1회 (기준)
  3. DB 파일을 읽기 전용으로 바꿔 쓰기 장애를 만든다
  4. Tool 없는 요청(R5)과 Tool 있는 요청(R1)을 보낸다 -> HTTP 200, 답변 있음, 로그에는 경고만
  5. 쓰기 권한 복구 후 후속 턴 1회 -> 다시 기록된다
결과는 samples/db_fault_e2e.json 에, --shot 이면 장애 중 화면을 docs/screenshots/28_db_fault_chat.png 에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
R5 = "안녕, 이거 어떻게 써?"
R1 = "12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시부터 새벽 2시까지 볼 수 있어?"
SID = "fault-e2e"


def wait_health(base: str, timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(f"{base}/api/health", timeout=3).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise SystemExit("서버가 뜨지 않았습니다")


def chat(base: str, message: str) -> tuple[int, dict, float]:
    t0 = time.time()
    r = requests.post(f"{base}/api/chat", json={"session_id": SID, "message": message},
                      headers={"X-Requested-With": "Astrowatch", "Origin": base}, timeout=240)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:300]}
    return r.status_code, body, round(time.time() - t0, 1)


def log_count(base: str) -> int:
    r = requests.get(f"{base}/api/logs/{SID}", timeout=10)
    return len(r.json()) if r.status_code == 200 else 0


def audit_status(base: str) -> str | None:
    r = requests.get(f"{base}/api/logs/{SID}/audit", timeout=10)
    return r.json().get("status") if r.status_code == 200 else None


def summarize(body: dict) -> dict:
    return {"intent": body.get("intent"), "answer_head": (body.get("answer") or "")[:80],
            "recommended": len(body.get("recommended") or []), "audit": body.get("audit"),
            "warnings": body.get("warnings")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--shot", action="store_true", help="장애 중 채팅 화면 캡처 (Playwright + Edge)")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    tmp = Path(tempfile.mkdtemp(prefix="astrowatch-fault-"))
    db = tmp / "fault.db"
    env = {**os.environ, "DB_PATH": str(db), "ALLOW_EXTERNAL_PATHS": "1", "CALENDAR_PROVIDER": "local",
           "APP_PORT": str(a.port), "PYTHONUTF8": "1"}
    log_path = tmp / "server.log"
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "astrowatch.app.main:app",
                                 "--host", "127.0.0.1", "--port", str(a.port)],
                                cwd=ROOT / "backend", env=env, stdout=log, stderr=subprocess.STDOUT)
        result: dict = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "db_path": "(임시 DB)", "steps": []}
        try:
            wait_health(base)
            # 1) 기준 턴
            code, body, sec = chat(base, R5)
            n0 = log_count(base)
            result["steps"].append({"step": "baseline", "message": R5, "http": code, "elapsed_s": sec,
                                    "log_events": n0, "audit": audit_status(base), **summarize(body)})
            assert code == 200 and n0 > 0, "기준 턴 실패"
            # 2) 쓰기 장애: DB 파일 읽기 전용
            os.chmod(db, stat.S_IREAD)
            for name, msg in (("fault_no_tool", R5), ("fault_with_tools", R1)):
                code, body, sec = chat(base, msg)
                result["steps"].append({"step": name, "message": msg, "http": code, "elapsed_s": sec,
                                        "log_events": log_count(base), "audit_latest": audit_status(base),
                                        **summarize(body)})
            if a.shot:
                shot(base)
            # 3) 복구
            os.chmod(db, stat.S_IREAD | stat.S_IWRITE)
            code, body, sec = chat(base, "같은 날 장소만 대전으로 바꿔 줘")
            result["steps"].append({"step": "recovered", "message": "같은 날 장소만 대전으로 바꿔 줘", "http": code,
                                    "elapsed_s": sec, "log_events": log_count(base), "audit_latest": audit_status(base),
                                    **summarize(body)})
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    # uvicorn 아래에서 앱 로거는 핸들러가 없어 Python 의 lastResort 가 메시지만 찍는다 ("WARNING" 접두어 없음)
    lines = [l.rstrip() for l in text.splitlines()
             if any(k in l for k in ("실패", "WARNING", "ERROR")) and "/api/health" not in l]
    result["server_log_excerpt"] = lines[:40]
    result["unhandled_traceback_in_server_log"] = "Traceback (most recent call last)" in text
    fault = [s for s in result["steps"] if s["step"].startswith("fault")]
    base_events = result["steps"][0]["log_events"]
    checks = {
        "장애 중 HTTP 200 + 답변": all(s["http"] == 200 and s["answer_head"] for s in fault),
        "장애 중 새 이벤트 미기록": all(s["log_events"] == base_events for s in fault),
        "장애 중 응답의 audit 는 없음/판정 불가": all((s["audit"] or {}).get("status") in (None, "undetermined") for s in fault),
        "서버 로그에 저장 실패 경고 있음": any("저장 실패" in l for l in lines),
        "처리되지 않은 Traceback 없음": not result["unhandled_traceback_in_server_log"],
        "복구 후 턴이 다시 기록됨": result["steps"][-1]["http"] == 200 and result["steps"][-1]["log_events"] > base_events,
    }
    result["checks"] = checks
    out = ROOT / "samples" / "db_fault_e2e.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for s in result["steps"]:
        print(f"{s['step']:<17} http={s['http']} {s['elapsed_s']:>5}s events={s['log_events']} intent={s['intent']} audit={s.get('audit')}")
    for k, v in checks.items():
        print(("  OK  " if v else "  FAIL") + " " + k)
    print("->", out)
    return 0 if all(checks.values()) else 1


def shot(base: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(bypass_csp=True, viewport={"width": 1400, "height": 900}, device_scale_factor=1.5)
        pg = ctx.new_page()
        pg.goto(f"{base}/", wait_until="networkidle")
        pg.locator("#new-chat").click()
        pg.fill("#chat-input", R5)
        before = pg.locator("#messages .msg-bot").count()   # 인사말 말풍선이 이미 하나 있다
        pg.locator("#send-btn").click()
        pg.wait_for_function("n => document.querySelectorAll('#messages .msg-bot').length > n", arg=before,
                             timeout=120_000)
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(ROOT / "docs" / "screenshots" / "28_db_fault_chat.png"))
        print("  shot 28_db_fault_chat.png")
        b.close()


if __name__ == "__main__":
    sys.exit(main())
