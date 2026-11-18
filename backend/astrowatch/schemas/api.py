"""백엔드 API 요청·응답 (F3017). 경로는 app/api/*.py 참고.

    POST /api/chat                 ChatRequest  → ChatResponse
    GET  /api/plans                → list[PlanOut]
    POST /api/plans                PlanCreate   → PlanSaveResult
    GET  /api/plans/{id}           → PlanOut
    PATCH /api/plans/{id}          PlanUpdate   → PlanOut
    DELETE /api/plans/{id}         → 204
    GET  /api/logs                 → list[LogSessionOut]
    GET  /api/logs/{session_id}    → list[LogEventOut]   (seq 순)
    DELETE /api/logs/{session_id}  → 204
    (실행 로그 수정 API 는 두지 않는다 — F3014)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from astrowatch.schemas.domain import Conditions, Intent, JudgedCandidate
from astrowatch.tools.contracts import Place


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="비우면 새 세션을 만든다")
    message: str = Field(min_length=1, max_length=2000)


class StepOut(BaseModel):
    """화면의 실행 타임라인 한 줄 (F3019). 로그 이벤트를 사람이 읽을 형태로 줄인 것."""

    seq: int
    type: str = Field(description="stage_start | stage_end | tool_call | tool_result | llm_decision | replan | error")
    stage: str | None = None
    tool: str | None = None
    ok: bool | None = None
    summary: str
    duration_ms: int | None = None
    replan_round: int | None = Field(default=None, description="재계획 안에서 일어난 단계면 몇 번째 재계획인지")


class ChatResponse(BaseModel):
    session_id: str
    intent: Intent
    answer: str = Field(description="최종 응답 문장 (F3011)")
    conditions: Conditions
    needs_clarification: list[str] = Field(default_factory=list, description="되물은 조건 이름")
    recommended: list[JudgedCandidate] = Field(default_factory=list, description="충족·조건부 충족 후보")
    excluded: list[JudgedCandidate] = Field(default_factory=list, description="제외 후보 + 사유")
    places: list[Place] = Field(default_factory=list)
    replan_count: int = 0
    replan_limit_reached: bool = False
    steps: list[StepOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="API 키 누락 · Tool 실패 안내 (상세 오류는 넣지 않는다, NF3002)")
    safety_note: str = "야간 관측 시 이동 안전과 방한에 유의하세요."


class LogSessionOut(BaseModel):
    session_id: str
    started_at: datetime
    last_at: datetime
    event_count: int
    tool_calls: int
    replans: int
    first_message: str | None = None


class LogEventOut(BaseModel):
    session_id: str
    seq: int
    ts: datetime
    type: str
    stage: str | None = None
    tool: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
