"""공통 예외 처리 — 사용자에게는 짧은 문장만 보낸다 (NF3002).

스택 · 키 · 외부 URL 은 응답에 넣지 않고 서버 로그에만 남긴다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("astrowatch.api")

INTERNAL_ERROR_DETAIL = "서버 내부 오류가 발생했습니다"


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 상세는 로그에만
    logger.exception("미처리 예외: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR_DETAIL})


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 응답에 loc · msg · type 만 남긴다. input · ctx(사용자 입력 에코)는 버린다."""
    detail = [
        {"loc": list(err.get("loc", ())), "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
