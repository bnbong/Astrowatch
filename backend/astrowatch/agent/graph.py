"""메인 Agent 그래프 (LangGraph).

    analyze ─(chat·clarify 등)→ respond
       │
       └→ plan → execute → judge ─(충족·조건부)→ respond
                ↑                 │
                └──(0개 & 재계획 < 상한)┘
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from astrowatch.agent import nodes

_compiled: Any | None = None


def build_graph() -> Any:
    """컴파일한 그래프 (모듈 캐시)."""
    global _compiled
    if _compiled is not None:
        return _compiled
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
    _compiled = g.compile()
    return _compiled


def graph_ascii() -> str:
    """검증 4.2 캡처용 그래프 그림. grandalf 가 없으면 mermaid 텍스트로 대신한다."""
    graph = build_graph().get_graph()
    try:
        return graph.draw_ascii()
    except Exception:  # noqa: BLE001 — grandalf 미설치
        return graph.draw_mermaid()
