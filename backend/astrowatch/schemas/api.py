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
    GET  /api/logs/{session_id}/audits → list[AuditResult]   (F3020 로깅 Agent 판정, 최신 먼저)
    GET  /api/logs/{session_id}/audit  → AuditResult         (최신 판정, 없으면 404)
    POST /api/logs/{session_id}/audit  → AuditResult         (세션 전체를 지금 다시 판정)
    (실행 로그 수정 API 는 두지 않는다 — F3014)
    GET  /api/sessions             → list[SessionSummaryOut]   (대화 목록, last_at 내림차순)
    GET  /api/sessions/{id}        → SessionDetailOut          (턴 전체, 화면 복원)
    DELETE /api/sessions/{id}      → 204                       (turns·sessions 만, run_logs 유지)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from astrowatch.schemas.audit import AuditResult
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


class AuditFindingBrief(BaseModel):
    kind: str
    message: str


class AuditSummary(BaseModel):
    """심화 F3020: 로깅 Agent 판정 요약 (이번 턴). 진단 문장은 경고 수에 섞지 않는다."""

    status: str = Field(description="normal | anomaly | undetermined(판정 불가)")
    warning_count: int = 0
    findings: list[AuditFindingBrief] = Field(default_factory=list)
    diagnosis_text: str | None = Field(default=None, description="이상 판정에만 붙는 LLM 진단 (실패 시 '진단 실패: ...')")

    @classmethod
    def from_result(cls, r: AuditResult) -> "AuditSummary":
        return cls(
            status=r.status.value, warning_count=r.warning_count,
            findings=[AuditFindingBrief(kind=f.kind.value, message=f.message) for f in r.findings],
            diagnosis_text=r.diagnosis.text if r.diagnosis else None,
        )


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
    audit: AuditSummary | None = Field(default=None, description="심화 F3020 로깅 Agent 판정 (판정 실패 시 null)")
    llm_calls: int | None = Field(
        default=None,
        description="심화 F3021 이번 턴의 LLM 판단 호출 수 (llm_decision + worker_decision 중 LLM 시도 + audit_diagnosis). "
                    "응답 문장 생성 1회는 이벤트가 없어 세지 않는다")


class SessionSummaryOut(BaseModel):
    """대화 목록 한 줄 (GET /api/sessions). 실행 로그 세션과 별개로 '대화' 단위."""

    session_id: str
    title: str = Field(description="첫 사용자 메시지 앞 60자")
    started_at: datetime
    last_at: datetime
    turn_count: int
    last_intent: str | None = None


class TurnOut(BaseModel):
    """대화 턴 하나 — 화면 복원용. response 는 당시 ChatResponse 그대로."""

    session_id: str
    seq: int
    ts: datetime
    user_message: str
    response: "ChatResponse"


class SessionDetailOut(BaseModel):
    """GET /api/sessions/{session_id}. turns 는 seq 순. DELETE 는 turns·sessions 만 지우고 run_logs 는 남긴다."""

    session_id: str
    title: str
    started_at: datetime
    last_at: datetime
    turns: list[TurnOut]


class LogSessionOut(BaseModel):
    session_id: str
    started_at: datetime
    last_at: datetime
    event_count: int
    tool_calls: int
    replans: int
    first_message: str | None = None
    audit_status: str | None = Field(default=None, description="F3020 최신 판정 normal | anomaly | undetermined, 판정 없음 null")
    audit_warnings: int | None = None


class LogEventOut(BaseModel):
    session_id: str
    seq: int
    ts: datetime
    type: str
    stage: str | None = None
    tool: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
