"""캘린더 API (심화 F3023) — Tool 결과(ToolResult)를 그대로 JSON 으로 돌려준다.

실패는 코드별 HTTP 상태 + 사용자용 짧은 메시지만 (상세 오류 노출 금지).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from astrowatch.tools.base import ErrorCode, ToolResult, ToolSpec

router = APIRouter(tags=["calendar"])

_STATUS = {
    ErrorCode.AUTH: 503,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.BAD_INPUT: 422,
}
_MESSAGE = {
    ErrorCode.AUTH: "캘린더 인증 정보가 없습니다. 관리자에게 Google 토큰 발급을 요청하세요.",
    ErrorCode.NOT_FOUND: "계획을 찾을 수 없습니다",
    ErrorCode.BAD_INPUT: "입력값을 확인해 주세요",
    ErrorCode.TIMEOUT: "캘린더 응답이 늦습니다. 잠시 후 다시 시도해 주세요.",
}
_DEFAULT_MESSAGE = "캘린더 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _tools() -> tuple[ToolSpec, ToolSpec]:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.tools import calendar as cal

    return cal.get_calendar, cal.add_plan_to_calendar


def _respond(result: ToolResult[Any]) -> Any:
    if result.ok:
        return result.model_dump(mode="json")
    code = result.code or ErrorCode.INTERNAL
    message = _MESSAGE.get(code, _DEFAULT_MESSAGE)
    return JSONResponse(
        status_code=_STATUS.get(code, 502),
        content={"ok": False, "error": {"code": code.value, "message": message}, "detail": message},
    )


@router.post("/plans/{plan_id}/calendar", summary="계획을 캘린더에 등록 (plan_id 만)")
def add_plan_to_calendar(plan_id: int) -> Any:
    _, add_tool = _tools()
    return _respond(add_tool.invoke({"plan_id": plan_id}))


@router.get("/calendar", summary="일정 조회 (date_from~date_to, 포함)")
def get_calendar(date_from: date = Query(...), date_to: date = Query(...)) -> Any:
    get_tool, _ = _tools()
    return _respond(get_tool.invoke({"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}))
