"""심화 F3020 검증용: 합성 실행 로그 5종을 EventBus 로 발행하고 로깅 Agent 판정을 보여 준다.

    .venv/Scripts/python.exe -X utf8 backend/scripts/make_anomalies.py          # 임시 DB (기본)
    .venv/Scripts/python.exe -X utf8 backend/scripts/make_anomalies.py --db     # 설정된 DB (화면에서 확인용)

LLM 과 네트워크를 쓰지 않는다 (AUDIT_LLM=0 으로 진단을 끈다). 세션 5종:

| 세션 | 내용 | 기대 판정 |
|---|---|---|
| demo-audit-normal | 계획(planned_tools)과 같은 Tool 만 호출 | normal |
| demo-audit-repeated | 같은 Tool + 같은 입력 3회 호출 | anomaly (repeated_call) |
| demo-audit-replan | 재계획을 기준 횟수까지, 마지막은 limit_reached | anomaly (excessive_replan) |
| demo-audit-unplanned | 계획에 없던 Tool 호출 | anomaly (unplanned_call) |
| demo-audit-empty | session_start, session_end 만 | undetermined (판정 불가) |

결과는 samples/audit_samples.json 에 저장한다 (문서가 인용).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

os.environ["AUDIT_LLM"] = "0"   # 진단(LLM) 없이 규칙 판정만

from astrowatch.config import get_settings, reset_settings_cache  # noqa: E402

reset_settings_cache()

from astrowatch.events.bus import EventBus, EventType  # noqa: E402
from astrowatch.logging_agent.agent import LoggingAgent  # noqa: E402
from astrowatch.logging_agent.audit import audit_session  # noqa: E402
from astrowatch.storage import run_audits, run_logs  # noqa: E402

EV, NIGHT, FC, CAL = "search_sky_events", "get_night_window", "get_sky_forecast", "get_calendar"
OUT = BACKEND_DIR.parent / "samples" / "audit_samples.json"


def _turn(bus: EventBus, sid: str, message: str, body) -> None:
    bus.publish(sid, EventType.SESSION_START, payload={"message": message})
    bus.publish(sid, EventType.STAGE_START, stage="analyze")
    bus.publish(sid, EventType.STAGE_END, stage="analyze", payload={"duration_ms": 5, "summary": "의도 plan"})
    replans = body(bus, sid) or 0
    bus.publish(sid, EventType.STAGE_END, stage="respond", payload={"duration_ms": 3, "summary": "plan 응답"})
    bus.publish(sid, EventType.SESSION_END, payload={"intent": "plan", "replan_count": replans, "duration_ms": 30})


def _plan(bus: EventBus, sid: str, tools: list[str], round_: int = 0) -> None:
    prefix = f"[재계획 {round_}회차] " if round_ else ""
    bus.publish(sid, EventType.STAGE_START, stage="plan")
    bus.publish(sid, EventType.STAGE_END, stage="plan",
                payload={"duration_ms": 1, "summary": f"{prefix}{len(tools)}단계 계획", "planned_tools": tools})


def _call(bus: EventBus, sid: str, tool: str, args: dict[str, Any], ok: bool = True) -> None:
    bus.publish(sid, EventType.TOOL_CALL, stage="execute", tool=tool, payload={"args": args, "reason": "합성"})
    bus.publish(sid, EventType.TOOL_RESULT, stage="execute", tool=tool, payload={"ok": ok, "duration_ms": 10})


def normal(bus: EventBus, sid: str) -> int:
    _plan(bus, sid, [EV, NIGHT, FC])
    _call(bus, sid, EV, {"year": 2026, "month": 12})
    _call(bus, sid, NIGHT, {"date": "2026-12-14", "lat": 37.56, "lon": 126.97})
    _call(bus, sid, FC, {"date": "2026-12-14", "lat": 37.56, "lon": 126.97, "hours": [22, 23]})
    return 0


def repeated(bus: EventBus, sid: str) -> int:
    _plan(bus, sid, [EV, NIGHT])
    for _ in range(3):
        _call(bus, sid, NIGHT, {"date": "2026-12-14", "lat": 37.56, "lon": 126.97}, ok=False)
    return 0


def replan(bus: EventBus, sid: str) -> int:
    limit = get_settings().audit_replan_threshold
    _plan(bus, sid, [EV, NIGHT, FC])
    _call(bus, sid, FC, {"date": "2026-12-14", "hours": [22]})
    for r in range(1, limit + 1):
        bus.publish(sid, EventType.REPLAN, stage="judge",
                    payload={"round": r, "strategy": "same_event_other_dates", "reason": "충족 후보 0개",
                             "limit_reached": r >= limit})
        _plan(bus, sid, [EV, NIGHT, FC], round_=r)
        _call(bus, sid, FC, {"date": f"2026-12-{14 + r}", "hours": [22]})
    return limit


def unplanned(bus: EventBus, sid: str) -> int:
    _plan(bus, sid, [EV, NIGHT])
    _call(bus, sid, EV, {"year": 2026, "month": 12})
    _call(bus, sid, CAL, {"date_from": "2026-12-14", "date_to": "2026-12-14"})
    return 0


SCENARIOS = [
    ("demo-audit-normal", "쌍둥이자리 유성우 서울", normal, "normal"),
    ("demo-audit-repeated", "같은 입력 반복", repeated, "anomaly"),
    ("demo-audit-replan", "재계획 과다", replan, "anomaly"),
    ("demo-audit-unplanned", "계획 외 호출", unplanned, "anomaly"),
    ("demo-audit-empty", "근거 없음", None, "undetermined"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", action="store_true", help="임시 DB 대신 설정된 DB_PATH 에 쓴다")
    ap.add_argument("--no-save", action="store_true", help="samples/audit_samples.json 을 쓰지 않는다")
    args = ap.parse_args()

    db_path = None if args.db else Path(tempfile.mkdtemp(prefix="astrowatch-audit-")) / "audit.db"
    bus = EventBus()
    agent = LoggingAgent(db_path=db_path)
    agent.attach(bus)
    rows, results = [], []
    try:
        for sid, message, body, expected in SCENARIOS:
            run_logs.delete_session(sid, db_path=db_path)     # 다시 돌려도 같은 결과
            run_audits.delete_session(sid, db_path=db_path)
            if body is None:
                bus.publish(sid, EventType.SESSION_START, payload={"message": message})
                bus.publish(sid, EventType.SESSION_END, payload={"intent": "chat", "replan_count": 0, "duration_ms": 1})
            else:
                _turn(bus, sid, message, body)
            result = audit_session(sid, db_path=db_path)
            run_audits.save_audit(result, db_path=db_path)
            bus.publish(sid, EventType.AUDIT_RESULT, stage="audit", payload=result.model_dump(mode="json", exclude={"diagnosis"}))
            rows.append((sid, result.status.value, expected, result.warning_count,
                         "; ".join(f"{f.kind.value} seq{f.evidence_seqs}" for f in result.findings) or "-",
                         result.evidence_count))
            results.append({"scenario": sid, "message": message, "expected": expected,
                            "result": result.model_dump(mode="json"),
                            "events": [e.model_dump(mode="json", exclude={"session_id", "ts"})
                                       for e in run_logs.get_session_events(sid, db_path=db_path)]})
    finally:
        agent.detach()

    head = ("세션", "판정", "기대", "경고", "근거 (kind seq)", "이벤트")
    print(" | ".join(head))
    print(" | ".join("---" for _ in head))
    for r in rows:
        print(" | ".join(str(x) for x in r))
    ok = all(r[1] == r[2] for r in rows)
    print(f"\n기대와 일치: {'예' if ok else '아니오'}  (DB: {'설정된 DB_PATH' if args.db else '임시 파일'})")

    if not args.no_save:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "replan_threshold": get_settings().audit_replan_threshold,
            "llm": False,
            "samples": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: samples/{OUT.name}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
