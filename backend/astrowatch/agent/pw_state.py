"""심화 F3021: Planner/Worker 구조에서만 쓰는 그래프 상태 키.

RunState(nodes.py) 를 그대로 확장한다. classic 구조는 이 키를 쓰지 않는다.
"""

from __future__ import annotations

from typing import Any

from astrowatch.agent.nodes import RunState


class PWState(RunState, total=False):
    task_list: dict[str, Any] | None          # 마지막으로 발행한 TaskList.model_dump()
    worker_requests: list[dict[str, Any]]     # Worker 가 스스로 해결하지 못한 실패 [{task_id, tool, error_code, reason}]
    worker_request_rounds: int                # 이번 턴에 worker_request 로 Planner 를 다시 부른 횟수
    planner_next: str                         # planner 다음 노드: worker | judge
    audit_inturn: dict[str, Any] | None       # verify 노드의 턴 중 판정 (AuditResult.model_dump)
    audit_recomposed: bool                    # audit_anomaly 재구성은 턴마다 한 번
