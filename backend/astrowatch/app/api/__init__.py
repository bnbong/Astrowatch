"""API 라우터 공통 — 경로 · 페이지 파라미터 검증 (보안 강화 8 · 17b)."""

from __future__ import annotations

from typing import Annotated, TypeVar

from fastapi import Path, Query

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{6,64}$"
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN, description="세션 ID (영숫자 · _ · -, 6~64자)")]
PlanId = Annotated[int, Path(ge=1, description="계획 ID (1 이상)")]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="최대 개수")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 개수")]

T = TypeVar("T")


def page(items: list[T], limit: int, offset: int) -> list[T]:
    """목록 응답 모양(리스트)은 그대로 두고 잘라서 돌려준다."""
    return list(items)[offset : offset + limit]
