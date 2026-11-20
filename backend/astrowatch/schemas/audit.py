"""심화 F3020: 로깅 Agent 의 이상 징후 판정 계약.

- 판정(AuditResult.status, findings)은 **규칙**으로만 만든다. 이벤트를 대조하고 횟수를 센다.
- 근거 이벤트가 없으면 '이상 없음'이 아니라 '판정 불가'(UNDETERMINED).
- 진단(AuditDiagnosis)은 이상(ANOMALY)으로 판정한 실행에만 LLM 으로 붙이며, 판정과 분리해 저장한다.
  진단이 실패해도 판정과 실행 로그는 그대로 남는다 (failed=True).
- 메인 Agent(Planner)는 AuditResult 를 그대로 참조한다 (state["audit"]).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AuditStatus(str, Enum):
    NORMAL = "normal"               # 경고 0건
    ANOMALY = "anomaly"             # 경고 1건 이상
    UNDETERMINED = "undetermined"   # 근거 이벤트 없음 → 판정 불가


class FindingKind(str, Enum):
    REPEATED_CALL = "repeated_call"         # 같은 Tool 을 같은 입력으로 반복 호출 (캐시 재사용 제외)
    EXCESSIVE_REPLAN = "excessive_replan"   # 재계획 과다 반복
    UNPLANNED_CALL = "unplanned_call"       # 계획(planner_tasks 또는 plan stage_end)에 없던 Tool 호출


class AuditFinding(BaseModel):
    kind: FindingKind
    message: str = Field(max_length=300)
    evidence_seqs: list[int] = Field(default_factory=list, description="근거 이벤트 seq")
    count: int | None = None


class AuditDiagnosis(BaseModel):
    source: str = "llm"                       # llm | none
    culprit: str = "unknown"                  # planner | worker | tool | unknown (어느 Agent 쪽 문제인지)
    fix: str = Field(default="", max_length=300)   # 무엇을 고치면 되는지
    text: str = Field(default="", max_length=600)  # 세 문장 이내 진단
    model: str | None = None
    failed: bool = False


class AuditResult(BaseModel):
    session_id: str
    seq_from: int
    seq_to: int
    status: AuditStatus
    findings: list[AuditFinding] = Field(default_factory=list)
    evidence_count: int = 0                   # 대조한 이벤트 수
    rule_version: str = "1"
    audited_at: str                           # ISO 8601
    diagnosis: AuditDiagnosis | None = None   # 진단은 경고 수에 섞지 않는다

    @property
    def warning_count(self) -> int:
        return len(self.findings)
