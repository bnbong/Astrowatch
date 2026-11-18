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

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from astrowatch.schemas.domain import Conditions, Intent, JudgedCandidate
from astrowatch.tools.contracts import Place


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="비우면 새 세션을 만든다")
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("메시지가 비어 있습니다")
        return v


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
    replan_limit: int = Field(default=3, description="설정된 재계획 상한 (MAX_REPLANS)")
    replan_limit_reached: bool = Field(default=False, description="replan_count 가 상한에 실제로 도달한 경우에만 true")
    stop_reason: Literal["limit_reached", "no_alternative", "target_unobservable", "fixed_constraints", "fatal_error"] | None = Field(
        default=None,
        description="추천 0개로 끝났을 때 왜 더 찾지 않았는지. limit_reached=상한 도달, no_alternative=전략 소진, "
                    "target_unobservable=대상이 그 지역에서 물리적으로 관측 불가, fixed_constraints=사용자가 고정한 조건 때문에 대안 없음, "
                    "fatal_error=키 누락 등 재시도해도 같은 실패",
    )
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
