"""메인 Agent 그래프 (LangGraph). settings.agent_structure 로 두 구조 중 하나를 만든다.

classic (구조 변경 전):

    analyze ─(chat, clarify 등)→ respond
       │
       └→ plan → execute → judge ─(충족, 조건부)→ respond
                ↑                 │
                └──(0개 & 재계획 < 상한)┘

planner_worker (심화 F3021, 기본):

    analyze → planner → worker → judge → verify → respond
                ↑  ↑  ↑   │        │        │
                │  │  └───┘(Worker 요청) │        │
                │  └───────────(judge 재계획)┘        │
                └──────────────(로깅 Agent 이상, 턴마다 한 번)┘
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from astrowatch.agent import nodes

_compiled: dict[str, Any] = {}

STRUCTURES = ("planner_worker", "classic")


def _structure(structure: str | None) -> str:
    if structure in STRUCTURES:
        return structure  # type: ignore[return-value]
    from astrowatch.config import get_settings

    return get_settings().agent_structure


def _classic() -> Any:
    g = StateGraph(nodes.RunState)
    g.add_node("analyze", nodes.analyze)
    g.add_node("plan", nodes.plan)
    g.add_node("execute", nodes.execute)
    g.add_node("judge", nodes.judge)
    g.add_node("respond", nodes.respond)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", nodes.route_after_analyze, {"plan": "plan", "respond": "respond"})
    g.add_edge("plan", "execute")
    g.add_edge("execute", "judge")
    g.add_conditional_edges("judge", nodes.route_after_judge, {"plan": "plan", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


def _planner_worker() -> Any:
    from astrowatch.agent.planner import planner, route_after_planner
    from astrowatch.agent.pw_state import PWState
    from astrowatch.agent.verify import route_after_verify, verify
    from astrowatch.agent.worker import route_after_worker, worker

    g = StateGraph(PWState)
    g.add_node("analyze", nodes.analyze)
    g.add_node("planner", planner)
    g.add_node("worker", worker)
    g.add_node("judge", nodes.judge)
    g.add_node("verify", verify)
    g.add_node("respond", nodes.respond)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", nodes.route_after_analyze, {"plan": "planner", "respond": "respond"})
    g.add_conditional_edges("planner", route_after_planner, {"worker": "worker", "judge": "judge"})
    g.add_conditional_edges("worker", route_after_worker, {"planner": "planner", "judge": "judge"})
    g.add_conditional_edges("judge", nodes.route_after_judge, {"plan": "planner", "respond": "verify"})
    g.add_conditional_edges("verify", route_after_verify, {"planner": "planner", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


def build_graph(structure: str | None = None) -> Any:
    """컴파일한 그래프 (구조별 모듈 캐시). structure 를 비우면 settings.agent_structure."""
    key = _structure(structure)
    if key not in _compiled:
        _compiled[key] = _planner_worker() if key == "planner_worker" else _classic()
    return _compiled[key]


def graph_ascii(structure: str | None = None) -> str:
    """검증 4.2 캡처용 그래프 그림 (활성 구조). grandalf 가 없으면 mermaid 텍스트로 대신한다."""
    key = _structure(structure)
    graph = build_graph(key).get_graph()
    try:
        text = graph.draw_ascii()
    except Exception:  # noqa: BLE001: grandalf 미설치
        text = graph.draw_mermaid()
    return f"[agent_structure={key}]\n{text}"
