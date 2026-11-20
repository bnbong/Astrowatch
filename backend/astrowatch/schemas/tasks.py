"""심화 F3021: Planner 와 Worker 가 주고받는 정해진 형식.

Planner 는 사용자 요청을 작업 목록(TaskList)으로 분해하고, Worker 는 작업마다 Tool 을 호출해
WorkerResult 로 답한다. 두 메시지는 이벤트(PLANNER_TASKS, WORKER_RESULT)로 실행 로그에 남는다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Task(BaseModel):
    task_id: str                      # "r0-t1" (회차-순번)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    fixed_args: list[str] = Field(default_factory=list, description="사용자 조건으로 고정한 인자. Worker 가 바꾸지 않는다")
    purpose: str = Field(default="", max_length=200)
    # 로그용 메타데이터: 데이터 흐름(박명 ← 현상, 예보 ← 박명)을 실행 로그에 보여 줄 뿐, Worker 는 이 값으로
    # 순서를 정하지 않는다 (작업 목록 순서대로 실행). DAG 스케줄러 없음.
    depends_on: list[str] = Field(default_factory=list, description="로그용. Worker 스케줄링에 쓰지 않는다")


class TaskList(BaseModel):
    round: int = 0                    # 0 = 첫 계획, 1~ = 재계획 회차
    tasks: list[Task] = Field(default_factory=list)
    reason: str = Field(default="", max_length=300)
    trigger: str = "user"             # user | judge_replan | worker_request | audit_anomaly


class WorkerDecisionKind(str, Enum):
    RETRY = "retry"                   # 같은 Tool 을 고친 인자로 다시 (상한 안에서, 고정 인자 불변)
    SKIP = "skip"                     # 이 작업은 건너뛰고 계속
    REPLAN_REQUEST = "replan_request" # Planner 에게 계획 변경 요청


class WorkerDecision(BaseModel):
    kind: WorkerDecisionKind
    reason: str = Field(default="", max_length=300)
    new_args: dict[str, Any] | None = None
    source: str = "rule"              # rule | llm


class WorkerResult(BaseModel):
    task_id: str
    tool: str
    args_used: dict[str, Any] = Field(default_factory=dict)
    args_changed: list[str] = Field(default_factory=list, description="재시도로 바뀐 인자 이름 (args_used 가 실제 인자)")
    ok: bool
    error_code: str | None = None
    attempts: int = 1
    from_cache: bool = False
    duration_ms: int = 0
    decision: WorkerDecision | None = None       # 실패 뒤 Worker 가 내린 판단
    replan_request: str | None = None            # 스스로 해결 못한 실패 사유 (Planner 가 재계획에 반영)
