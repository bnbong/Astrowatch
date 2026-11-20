"""심화 F3021: 구조 변경 전후 비교 → samples/structure_compare.json + Markdown 표.

    .venv/Scripts/python.exe -X utf8 backend/scripts/compare_structures.py
    .venv/Scripts/python.exe -X utf8 backend/scripts/compare_structures.py --only R1,NR --configs classic,pw_llm

같은 시나리오 묶음을 세 구성으로 같은 프로세스에서 실행한다 (run_scenarios.py 와 같은 방식, 실제 LLM, 외부 API).
| 구성 | 설정 |
|---|---|
| classic | AGENT_STRUCTURE=classic (analyze → plan → execute → judge → respond) |
| pw_rule | AGENT_STRUCTURE=planner_worker, WORKER_LLM=0 (Worker 실패 판단을 규칙으로만) |
| pw_llm | AGENT_STRUCTURE=planner_worker, WORKER_LLM=1 (인자 수정 여지가 있는 실패만 LLM 판단) |

측정값은 실행 로그(run_logs) 이벤트에서 센다 (로깅 Agent 를 붙여 실행):
- tool_calls: tool_call 이벤트 수 (캐시 재사용은 이벤트가 없다)
- llm_calls: llm_decision + worker_decision(LLM 시도) + audit_diagnosis 이벤트 수. 응답 문장 생성 1회는 세지 않는다
  (세 구성에서 똑같이 1회라 비교에 영향 없음). ChatResponse.llm_calls 와 같은 정의다.
- replan_count, stop_reason, recommended: ChatResponse. elapsed_s: run_turn 벽시계 시간.
- flow: 단계 순서(stage_start)와 작업 목록 trigger.
키 값은 출력, 저장하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from astrowatch.config import get_settings  # noqa: E402
from run_scenarios import EnvOverride, session_events, write_json  # noqa: E402

CONFIGS: dict[str, dict[str, str]] = {
    "classic": {"AGENT_STRUCTURE": "classic"},
    "pw_rule": {"AGENT_STRUCTURE": "planner_worker", "WORKER_LLM": "0"},
    "pw_llm": {"AGENT_STRUCTURE": "planner_worker", "WORKER_LLM": "1"},
}
BASE_ENV = {"CALENDAR_PROVIDER": "local"}

# (key, env, [turn messages]): R6 은 R1 세션의 두 번째 턴
SCENARIOS: list[tuple[str, dict[str, str], list[str]]] = [
    ("R1", {}, ["12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시부터 새벽 2시 사이에 볼 수 있어?",
                "같은 날 장소만 대전으로 바꿔 줘"]),
    ("R2", {"WEATHER_PROVIDER": "mock"}, ["이번 주말 밤 9시~12시에 대전에서 사자자리 유성우 보고 싶어. 구름 많으면 다른 날로"]),
    ("R3", {"WEATHER_PROVIDER": "mock"}, ["다음 주 아무 날이나 서울에서 보름달 피해서 유성 보고 싶어"]),
    ("R4", {"KAKAO_REST_KEY": ""}, ["토요일 밤 9시부터 대전 근처 천문대에서 보고 싶어, 20km 이내. 자정 전에는 끝내야 해"]),
    ("NR", {}, ["2026년 12월 14일 서울에서 존재하지않는유성우XYZ 관측 계획"]),
]


def turn_label(key: str, i: int) -> str:
    return "R6" if key == "R1" and i == 2 else key


def metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    def t(e: dict[str, Any]) -> str:
        return str(e.get("type"))

    llm = 0
    worker_llm = worker_rule = 0
    for e in events:
        p = e.get("payload") or {}
        if t(e) in ("llm_decision", "audit_diagnosis"):
            llm += 1
        elif t(e) == "worker_decision":
            if p.get("source") == "llm" or p.get("llm_attempted"):
                llm += 1
                worker_llm += 1
            else:
                worker_rule += 1
    stages = [str(e.get("stage")) for e in events if t(e) == "stage_start"]
    triggers = [str((e.get("payload") or {}).get("trigger")) for e in events if t(e) == "planner_tasks"]
    audit = next(((e.get("payload") or {}).get("status") for e in events if t(e) == "audit_result"), None)
    return {
        "tool_calls": sum(1 for e in events if t(e) == "tool_call"),
        "llm_calls_events": llm,
        "worker_llm_decisions": worker_llm,
        "worker_rule_decisions": worker_rule,
        "planner_tasks": len(triggers),
        "worker_results": sum(1 for e in events if t(e) == "worker_result"),
        "replan_events": sum(1 for e in events if t(e) == "replan"),
        "flow": " > ".join(stages),
        "task_triggers": triggers,
        "audit_status": audit,
    }


def run_config(name: str, scenarios: list[tuple[str, dict[str, str], list[str]]]) -> list[dict[str, Any]]:
    from astrowatch.agent.service import run_turn

    rows: list[dict[str, Any]] = []
    for key, env, messages in scenarios:
        with EnvOverride({**BASE_ENV, **CONFIGS[name], **env}):
            sid: str | None = None
            last_seq = 0
            for i, msg in enumerate(messages, 1):
                t0 = time.perf_counter()
                resp = run_turn(sid, msg).model_dump(mode="json")
                elapsed = round(time.perf_counter() - t0, 2)
                sid = resp["session_id"]
                evs = session_events(sid)
                turn_evs = [e for e in evs if e["seq"] > last_seq]
                if evs:
                    last_seq = max(e["seq"] for e in evs)
                row = {
                    "config": name, "scenario": turn_label(key, i), "message": msg, "session_id": sid,
                    "intent": resp.get("intent"), "replan_count": resp.get("replan_count"),
                    "stop_reason": resp.get("stop_reason"), "recommended": len(resp.get("recommended") or []),
                    "llm_calls_response": resp.get("llm_calls"), "elapsed_s": elapsed,
                    "env_overrides": {**CONFIGS[name], **env},
                    **metrics(turn_evs),
                }
                rows.append(row)
                print(f"[{name}] {row['scenario']}: replans={row['replan_count']} tools={row['tool_calls']} "
                      f"llm={row['llm_calls_events']} (worker_llm={row['worker_llm_decisions']}) "
                      f"rec={row['recommended']} stop={row['stop_reason']} {elapsed:.1f}s "
                      f"triggers={row['task_triggers']}")
    return rows


def markdown(rows: list[dict[str, Any]]) -> str:
    head = ("| 시나리오 | 구성 | 재계획 | Tool 호출 | LLM 호출 | Worker LLM | 작업 목록(trigger) | 추천 | stop_reason | 응답 시간(s) |\n"
            "|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    order = {k: i for i, k in enumerate(CONFIGS)}
    for r in sorted(rows, key=lambda x: (x["scenario"], order.get(x["config"], 9))):
        trig = ", ".join(r["task_triggers"]) or "-"
        lines.append(f"| {r['scenario']} | {r['config']} | {r['replan_count']} | {r['tool_calls']} | "
                     f"{r['llm_calls_events']} | {r['worker_llm_decisions']} | {trig} | {r['recommended']} | "
                     f"{r['stop_reason'] or '-'} | {r['elapsed_s']} |")
    lines.append("")
    lines.append("| 구성 | 평균 재계획 | 평균 Tool 호출 | 평균 LLM 호출 | 평균 응답 시간(s) |")
    lines.append("|---|---|---|---|---|")
    for name in CONFIGS:
        rs = [r for r in rows if r["config"] == name]
        if not rs:
            continue
        n = len(rs)
        lines.append(f"| {name} | {sum(r['replan_count'] or 0 for r in rs) / n:.2f} | "
                     f"{sum(r['tool_calls'] for r in rs) / n:.2f} | {sum(r['llm_calls_events'] for r in rs) / n:.2f} | "
                     f"{sum(r['elapsed_s'] for r in rs) / n:.2f} |")
    return "\n".join(lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="F3021 구조 전후 비교")
    ap.add_argument("--only", help="시나리오 키 (R1,R2,R3,R4,NR). R6 은 R1 에 포함")
    ap.add_argument("--configs", help="classic,pw_rule,pw_llm 중 일부")
    ap.add_argument("--out", default="structure_compare.json")
    args = ap.parse_args()
    if not get_settings().gms_key:
        print("GMS_KEY 가 없습니다. 저장소 루트 .env 에 GMS_KEY 를 넣은 뒤 다시 실행하세요.")
        return 2
    scenarios = SCENARIOS
    if args.only:
        wanted = {w.strip().upper() for w in args.only.split(",")}
        scenarios = [s for s in SCENARIOS if s[0] in wanted]
    configs = [c for c in CONFIGS if not args.configs or c in args.configs.split(",")]

    from astrowatch.logging_agent.agent import start_logging_agent
    from astrowatch.storage.db import init_db

    init_db()
    start_logging_agent()
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for name in configs:
        print(f"\n== {name} {CONFIGS[name]}")
        rows += run_config(name, scenarios)
    table = markdown(rows)
    samples = get_settings().samples_dir
    samples.mkdir(exist_ok=True)
    write_json(samples / args.out, {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "configs": CONFIGS, "base_env": BASE_ENV, "model": get_settings().llm_model,
        "max_replans": get_settings().max_replans, "worker_max_retry": get_settings().worker_max_retry,
        "rows": rows, "markdown": table,
    })
    print(f"\n{table}\n\n== 완료 {time.perf_counter() - t0:.0f}s -> samples/{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
