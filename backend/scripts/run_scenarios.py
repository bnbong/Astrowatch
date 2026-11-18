"""대표 시나리오 재실행 → samples/run_<name>.json · samples/logs_<name>.json 갱신.

    .venv/Scripts/python.exe -X utf8 backend/scripts/run_scenarios.py              # 전체
    .venv/Scripts/python.exe -X utf8 backend/scripts/run_scenarios.py --only R1,R2

서버를 띄우지 않고 astrowatch.agent.service.run_turn 을 직접 호출한다. 실제 LLM(GMS)·외부 API 를 쓰며,
키는 저장소 루트 .env 에서 읽는다(출력·저장하지 않는다 — ChatResponse 와 실행 로그에는 키가 없다).

환경 오버라이드(WEATHER_PROVIDER=mock, KAKAO_REST_KEY="" 등)는 같은 프로세스에서 os.environ 을 바꾸고
reset_settings_cache() 를 부른 뒤 실행하고, 끝나면 원복한다. 빈 문자열은 config 에서 "없음"으로 처리된다.
(Tool·클라이언트는 호출 시점에 get_settings() 를 읽으므로 캐시만 비우면 바뀐 값이 적용된다.)

캘린더: 기본으로 모든 시나리오에 CALENDAR_PROVIDER=local 을 건다. .env 가 google 이면 개인 일정 제목이
응답·로그에 들어가 samples 에 저장되고, 결과가 계정 일정에 따라 달라지기 때문이다.
실제 Google 캘린더로 돌리려면 --real-calendar.

저장 형식
  run_<name>.json  : {"scenario", "captured_at", "env_overrides", "session_id",
                      "turns": [{"message", "response": <ChatResponse dict>, "elapsed_s"}]}
  logs_<name>.json : {"scenario", "session_id", "turns": [{"message", "events": [<LogEventOut dict>...]}]}
R6 은 R1 세션의 두 번째 턴이다 — run_R1.json 에 2턴으로 들어가고 run_R6.json · logs_R6.json 에도 따로 저장한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from astrowatch.config import get_settings, reset_settings_cache  # noqa: E402  (.env 로드)

Check = tuple[str, Callable[[dict[str, Any]], bool]]   # (설명, response dict → bool)


@dataclass
class Turn:
    message: str
    checks: list[Check] = field(default_factory=list)
    save_as: str | None = None          # 이 턴을 별도 run_<save_as>.json 으로도 저장 (R6)


@dataclass
class Scenario:
    key: str                             # --only 에 쓰는 이름
    file: str                            # samples/run_<file>.json
    turns: list[Turn]
    env: dict[str, str] = field(default_factory=dict)


# ── 체크 헬퍼 ───────────────────────────────────────────────

def tool_calls(r: dict[str, Any]) -> int:
    return sum(1 for s in r.get("steps") or [] if s.get("type") == "tool_call")


def n_verdict(r: dict[str, Any], verdict: str) -> int:
    return sum(1 for j in r.get("recommended") or [] if j.get("verdict") == verdict)


def intent_is(v: str) -> Check:
    return (f"intent={v}", lambda r: r.get("intent") == v)


SCENARIOS: list[Scenario] = [
    Scenario("R1", "R1", [
        Turn("12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시부터 새벽 2시 사이에 볼 수 있어?", [
            intent_is("plan"),
            ("conditional 1개", lambda r: len(r["recommended"]) == 1 and n_verdict(r, "conditional") == 1),
        ]),
        Turn("같은 날 장소만 대전으로 바꿔 줘", [
            intent_is("plan"),
            ("region=대전 · 날짜 유지", lambda r: r["conditions"].get("region") == "대전"
             and r["conditions"].get("date_from") == "2026-12-14"),
        ], save_as="R6"),
    ]),
    Scenario("R2", "R2_mock", [
        Turn("이번 주말 밤 9시~12시에 대전에서 사자자리 유성우 보고 싶어. 구름 많으면 다른 날로", [
            intent_is("plan"),
            ("replans==3", lambda r: r.get("replan_count") == 3),
        ]),
    ], env={"WEATHER_PROVIDER": "mock"}),
    Scenario("R3", "R3_mock", [
        Turn("다음 주 아무 날이나 서울에서 보름달 피해서 유성 보고 싶어", [
            intent_is("plan"),
            ("replans==3", lambda r: r.get("replan_count") == 3),
        ]),
    ], env={"WEATHER_PROVIDER": "mock"}),
    Scenario("R4", "R4_nokakao", [
        Turn("토요일 밤 9시부터 대전 근처 천문대에서 보고 싶어, 20km 이내. 자정 전에는 끝내야 해", [
            intent_is("plan"),
            ("warnings 에 '카카오'", lambda r: any("카카오" in w for w in r.get("warnings") or [])),
        ]),
    ], env={"KAKAO_REST_KEY": ""}),
    Scenario("R5", "R5", [
        Turn("안녕, 이거 어떻게 써?", [intent_is("chat"), ("tool 0", lambda r: tool_calls(r) == 0)]),
    ]),
    Scenario("R7", "R7", [
        Turn("밤 11시 전에는 들어와야 해", [intent_is("clarify"), ("tool 0", lambda r: tool_calls(r) == 0)]),
    ]),
    Scenario("andromeda", "andromeda", [
        Turn("안드로메다 은하를 제일 잘 볼 수 있는 날짜를 추천해줘"),
        Turn("대전 유성구 지역에서. 날짜는 아직 미정이라 보기 좋은 날짜를 추천해줘", [
            intent_is("plan"),
            ("recommended >= 1", lambda r: len(r["recommended"]) >= 1),
        ]),
    ]),
    Scenario("fact_mars", "fact_mars", [
        Turn("오늘 대전에서 화성이 제일 잘 보이는 시간대", [intent_is("fact")]),
    ]),
    Scenario("invalid_date", "invalid_date", [
        Turn("2026년 2월 30일 서울에서 밤 9시부터 11시까지 별을 보고 싶어.", [
            intent_is("clarify"), ("tool 0", lambda r: tool_calls(r) == 0),
        ]),
    ]),
    Scenario("unknown_object", "unknown_object", [
        Turn("2026년 11월 18일 서울에서 밤 9시부터 11시까지 가짜천체 XYZ-FAKE-999를 관측하는 계획을 짜줘. "
             "다른 천체는 원하지 않아.", [
            ("recommended 0", lambda r: len(r.get("recommended") or []) == 0),
        ]),
    ]),
]


# ── 실행 ────────────────────────────────────────────────────

class EnvOverride:
    """os.environ 을 잠시 바꾸고 settings 캐시를 비운다. 끝나면 원래 값으로 되돌린다."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for k, v in self.env.items():
            self.saved[k] = os.environ.get(k)
            os.environ[k] = v
        reset_settings_cache()

    def __exit__(self, *exc: Any) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_settings_cache()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def session_events(session_id: str) -> list[dict[str, Any]]:
    from astrowatch.storage.run_logs import get_session_events

    try:
        return [e.model_dump(mode="json") for e in get_session_events(session_id)]
    except Exception as exc:  # noqa: BLE001
        print(f"   (실행 로그 읽기 실패: {type(exc).__name__})")
        return []


def summary_line(r: dict[str, Any], elapsed: float) -> str:
    return (f"intent={r.get('intent')} rec={len(r.get('recommended') or [])} "
            f"excl={len(r.get('excluded') or [])} replans={r.get('replan_count')} "
            f"stop={r.get('stop_reason')} tools={tool_calls(r)} warn={len(r.get('warnings') or [])} "
            f"{elapsed:.1f}s")


BASE_ENV: dict[str, str] = {"CALENDAR_PROVIDER": "local"}   # --real-calendar 이면 비운다


def run_scenario(sc: Scenario, samples: Path) -> list[tuple[str, str, bool, str]]:
    """시나리오 하나 실행·저장. (이름, 체크, 통과 여부, 요약) 목록을 돌려준다."""
    from astrowatch.agent.service import run_turn

    results: list[tuple[str, str, bool, str]] = []
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    session_id: str | None = None
    turns_out: list[dict[str, Any]] = []
    logs_out: list[dict[str, Any]] = []
    last_seq = 0
    env = {**BASE_ENV, **sc.env}

    with EnvOverride(env):
        for i, turn in enumerate(sc.turns, 1):
            label = turn.save_as or (sc.key if len(sc.turns) == 1 else f"{sc.key}#{i}")
            t0 = time.perf_counter()
            try:
                resp = run_turn(session_id, turn.message).model_dump(mode="json")
            except Exception:  # noqa: BLE001 — run_turn 은 원래 예외를 내지 않지만 다음 시나리오는 계속
                traceback.print_exc()
                results.append((label, "run_turn 예외", False, "-"))
                break
            elapsed = round(time.perf_counter() - t0, 2)
            session_id = resp["session_id"]
            turn_rec = {"message": turn.message, "response": resp, "elapsed_s": elapsed}
            turns_out.append(turn_rec)

            events = session_events(session_id)
            turn_events = [e for e in events if e["seq"] > last_seq]
            if events:
                last_seq = max(e["seq"] for e in events)
            logs_out.append({"message": turn.message, "events": turn_events})

            line = summary_line(resp, elapsed)
            print(f"[{label}] {line}")
            if turn.save_as:
                write_json(samples / f"run_{turn.save_as}.json", {
                    "scenario": turn.save_as, "captured_at": captured_at, "env_overrides": env,
                    "session_id": session_id, "follows": sc.key, "turns": [turn_rec],
                })
                write_json(samples / f"logs_{turn.save_as}.json", {
                    "scenario": turn.save_as, "session_id": session_id, "follows": sc.key,
                    "turns": [logs_out[-1]],
                })
            for desc, fn in turn.checks:
                try:
                    ok = bool(fn(resp))
                except Exception:  # noqa: BLE001
                    ok = False
                results.append((label, desc, ok, line))
                print(f"   {'OK  ' if ok else 'DIFF'} {desc}")

    if turns_out:
        write_json(samples / f"run_{sc.file}.json", {
            "scenario": sc.key, "captured_at": captured_at, "env_overrides": env,
            "session_id": session_id, "turns": turns_out,
        })
        write_json(samples / f"logs_{sc.file}.json", {
            "scenario": sc.key, "session_id": session_id, "turns": logs_out,
        })
    return results


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="Astrowatch 대표 시나리오 재실행 (samples/run_*.json 갱신)")
    ap.add_argument("--only", help="쉼표로 구분한 시나리오 이름 (예: R1,R2). R6 은 R1 과 함께 실행된다.")
    ap.add_argument("--real-calendar", action="store_true",
                    help=".env 의 CALENDAR_PROVIDER 를 그대로 쓴다 (기본은 local — 개인 일정이 samples 에 남지 않게)")
    args = ap.parse_args()
    if args.real_calendar:
        BASE_ENV.clear()

    if not get_settings().gms_key:
        print("GMS_KEY 가 없습니다. 저장소 루트 .env 에 GMS_KEY 를 넣은 뒤 다시 실행하세요 (셋업 가이드 참고).")
        return 2

    selected = SCENARIOS
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",") if w.strip()}
        if "r6" in wanted:
            wanted.add("r1")
        names = {s.key.lower() for s in SCENARIOS} | {s.file.lower() for s in SCENARIOS} | {"r6"}
        unknown = wanted - names
        if unknown:
            print(f"알 수 없는 시나리오: {', '.join(sorted(unknown))}")
        selected = [s for s in SCENARIOS if s.key.lower() in wanted or s.file.lower() in wanted]
        if not selected:
            return 2

    from astrowatch.logging_agent.agent import start_logging_agent
    from astrowatch.storage.db import init_db

    init_db()
    start_logging_agent()

    samples = get_settings().samples_dir
    samples.mkdir(exist_ok=True)
    all_results: list[tuple[str, str, bool, str]] = []
    t0 = time.perf_counter()
    for sc in selected:
        env_txt = f" env={sc.env}" if sc.env else ""
        if BASE_ENV:
            env_txt += f" (+{BASE_ENV})"
        print(f"\n== {sc.key} -> samples/run_{sc.file}.json{env_txt}")
        try:
            all_results += run_scenario(sc, samples)
        except Exception:  # noqa: BLE001 — 실패해도 다음 시나리오 계속
            traceback.print_exc()
            all_results.append((sc.key, "시나리오 실행 실패", False, "-"))

    diffs = [r for r in all_results if not r[2]]
    print(f"\n== 완료 {time.perf_counter() - t0:.0f}s · 체크 {len(all_results)}건 · "
          f"OK {len(all_results) - len(diffs)} · DIFF {len(diffs)}")
    for label, desc, _, line in diffs:
        print(f"   DIFF [{label}] {desc} — {line}")
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
