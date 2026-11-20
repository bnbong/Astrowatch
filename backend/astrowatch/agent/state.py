"""메인 Agent 상태 (F3006 계획 보관 · F3007 결과 통합 · F3009 재계획 · F3010 맥락).

LangGraph 노드 사이에 흐르는 단일 dict. 노드는 자기가 바꾼 키만 돌려준다.

    analyze → plan → execute → judge → respond
                ↑                 │  (충족·조건부 0개 & replan_count < max)
                └─────────────────┘
"""

from __future__ import annotations

from typing import Any, TypedDict

from astrowatch.schemas.domain import Candidate, Conditions, Intent, JudgedCandidate
from astrowatch.tools.base import ToolResult


class PlannedCall(TypedDict):
    """작업 계획 한 줄 (F3006)."""

    tool: str
    args: dict[str, Any]
    reason: str                 # 왜 부르는지 — 로그와 화면에 보임
    fixed_args: list[str]       # 사용자 조건으로 고정된 인자


class ExecutedCall(TypedDict):
    tool: str
    args: dict[str, Any]
    result: ToolResult[Any]
    duration_ms: int
    replan_round: int           # 0 = 첫 계획


class AgentState(TypedDict, total=False):
    # ── 입력 ──
    session_id: str
    message: str
    history: list[dict[str, str]]       # [{"role": "user"|"assistant", "content": ...}] 최근 N턴

    # ── analyze ──
    intent: Intent
    conditions: Conditions              # 이전 턴 조건과 병합된 결과
    user_conditions: Conditions         # 기본값(날짜 범위) 을 채우기 전, 사용자가 말한 그대로의 조건
    prev_conditions: Conditions         # 이번 턴 전까지의 조건 (후속 요청에서 무엇이 바뀌었는지 설명용)
    missing: list[str]                  # 되물을 조건 (비어 있으면 진행)

    # ── plan ──
    plan: list[PlannedCall]

    # ── execute ──
    calls: list[ExecutedCall]           # 이번 턴에 부른 모든 Tool (재계획 포함, 순서대로)
    cache: dict[str, ToolResult[Any]]   # "tool|json(args)" → 결과. 재계획 시 재사용 (F3009)
    candidates: list[Candidate]

    # ── judge ──
    judged: list[JudgedCandidate]
    replan_count: int
    replan_strategy: str | None         # 마지막 재계획 전략 이름
    replan_limit_reached: bool

    # ── respond ──
    answer: str
    warnings: list[str]

    # ── 심화 F3020: 직전 턴의 로깅 Agent 판정 (AuditResult.model_dump). 메인 Agent(Planner)가 참조 ──
    audit: dict[str, Any] | None

    # ── 세션에 남길 요약 (F3010: Tool 원본은 남기지 않는다) ──
    memory_summary: str


def cache_key(tool: str, args: dict[str, Any]) -> str:
    import json

    return f"{tool}|{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
