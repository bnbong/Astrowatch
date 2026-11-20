"""셀프 체크리스트 23번: 대표 요청 응답 시간과 동시 요청 부하 (NF3005).

    .venv/Scripts/python -X utf8 backend/scripts/perf_check.py [--port 8011] [--runs 5] [--clients 3] [--each 3]

합격 기준(사용자 확인, 2026-11-20)
  R1 (Tool 5회, LLM 2회): 중앙값 15초 이하, 최대 40초 이하
  R5 (Tool 없음):          중앙값 5초 이하
  동시 3명 x 3회:          오류율 0%, 중앙값이 순차 R1 중앙값의 2배 이내
실제 키와 LLM 으로 임시 DB 서버를 띄워 측정하고 samples/perf_20261120.json 에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = {
    "R1": "12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시부터 새벽 2시까지 볼 수 있어?",
    "R5": "안녕, 이거 어떻게 써?",
    "R7": "밤 11시 전엔 들어와야 해",
}
CRITERIA = {"R1_median_s": 15.0, "R1_max_s": 40.0, "R5_median_s": 5.0, "concurrent_error_rate": 0.0,
            "concurrent_median_ratio": 2.0}


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


def one(base: str, message: str) -> dict:
    sid = uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        r = requests.post(f"{base}/api/chat", json={"session_id": sid, "message": message},
                          headers={"X-Requested-With": "Astrowatch", "Origin": base}, timeout=240)
        ok = r.status_code == 200
        body = r.json() if ok else {}
    except requests.RequestException as e:  # noqa: PERF203
        ok, body, r = False, {}, None
    wall = round(time.time() - t0, 2)
    server_ms = None
    try:
        ev = requests.get(f"{base}/api/logs/{sid}", timeout=10).json()
        end = [e for e in ev if e.get("type") == "session_end"]
        server_ms = (end[-1].get("payload") or {}).get("duration_ms") if end else None
    except Exception:  # noqa: BLE001
        pass
    return {"session_id": sid, "ok": ok, "http": getattr(r, "status_code", None), "wall_s": wall,
            "server_ms": server_ms, "intent": body.get("intent"), "tool_calls": len([s for s in body.get("steps", [])
                                                                                   if s.get("type") == "tool_result"]),
            "llm_calls": body.get("llm_calls")}


def stats(rows: list[dict]) -> dict:
    ws = [r["wall_s"] for r in rows if r["ok"]]
    return {"n": len(rows), "ok": sum(r["ok"] for r in rows), "median_s": round(statistics.median(ws), 2) if ws else None,
            "p95_s": round(sorted(ws)[max(0, int(len(ws) * 0.95) - 1)], 2) if ws else None,
            "max_s": round(max(ws), 2) if ws else None, "min_s": round(min(ws), 2) if ws else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--clients", type=int, default=3)
    ap.add_argument("--each", type=int, default=3)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    tmp = Path(tempfile.mkdtemp(prefix="astrowatch-perf-"))
    env = {**os.environ, "DB_PATH": str(tmp / "perf.db"), "ALLOW_EXTERNAL_PATHS": "1", "CALENDAR_PROVIDER": "local",
           "APP_PORT": str(a.port), "CHAT_RATE_PER_MIN": "600", "PYTHONUTF8": "1"}
    with open(tmp / "server.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "astrowatch.app.main:app",
                                 "--host", "127.0.0.1", "--port", str(a.port)],
                                cwd=ROOT / "backend", env=env, stdout=log, stderr=subprocess.STDOUT)
        result: dict = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "criteria": CRITERIA,
                        "llm": os.environ.get("LLM_MODEL") or "claude-haiku-5-5 (GMS)", "sequential": {}, "concurrent": {}}
        try:
            wait_health(base)
            for key, msg in SCENARIOS.items():
                rows = [one(base, msg) for _ in range(a.runs)]
                result["sequential"][key] = {"message": msg, **stats(rows), "runs": rows}
                print(f"{key}: " + json.dumps(stats(rows), ensure_ascii=False))
            # 동시 부하: clients 개 스레드가 각각 each 회 R1 (세션은 모두 다름)
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=a.clients) as ex:
                futures = [ex.submit(lambda: [one(base, SCENARIOS["R1"]) for _ in range(a.each)]) for _ in range(a.clients)]
                rows = [r for f in futures for r in f.result()]
            result["concurrent"] = {"clients": a.clients, "each": a.each, "total_wall_s": round(time.time() - t0, 1),
                                    **stats(rows), "runs": rows}
            print("concurrent: " + json.dumps(stats(rows), ensure_ascii=False))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    seq, con = result["sequential"], result["concurrent"]
    ratio = round(con["median_s"] / seq["R1"]["median_s"], 2) if con.get("median_s") and seq["R1"]["median_s"] else None
    err_rate = round(1 - con["ok"] / con["n"], 3) if con.get("n") else None
    checks = {
        "R1 중앙값 15초 이하": seq["R1"]["median_s"] is not None and seq["R1"]["median_s"] <= CRITERIA["R1_median_s"],
        "R1 최대 40초 이하": seq["R1"]["max_s"] is not None and seq["R1"]["max_s"] <= CRITERIA["R1_max_s"],
        "R5 중앙값 5초 이하": seq["R5"]["median_s"] is not None and seq["R5"]["median_s"] <= CRITERIA["R5_median_s"],
        "동시 요청 오류율 0%": err_rate == 0.0,
        "동시 요청 중앙값 2배 이내": ratio is not None and ratio <= CRITERIA["concurrent_median_ratio"],
    }
    result["concurrent_median_ratio"] = ratio
    result["concurrent_error_rate"] = err_rate
    result["checks"] = checks
    out = ROOT / "samples" / "perf_20261120.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in checks.items():
        print(("  OK  " if v else "  FAIL") + " " + k)
    print("->", out)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
