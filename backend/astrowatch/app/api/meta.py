"""메타 API — Tool 목록(검증 4.1) · Agent 그래프(검증 4.2)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from astrowatch.tools.registry import registry

router = APIRouter(tags=["meta"])
logger = logging.getLogger("astrowatch.api")


def _graph_ascii() -> str:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.agent.graph import graph_ascii

    return graph_ascii()


@router.get("/tools", response_model=list[dict[str, Any]], summary="등록된 Tool 명세")
def list_tools() -> list[dict[str, Any]]:
    return registry.describe()


@router.get("/graph", response_model=dict[str, str], summary="Agent 그래프 (ASCII)")
def get_graph() -> dict[str, str]:
    try:
        text = _graph_ascii()
    except ImportError as exc:
        logger.warning("그래프 모듈 로드 실패: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Agent 그래프를 아직 사용할 수 없습니다") from None
    return {"ascii": text}
