"""FastAPI 진입점 (F3017).

실행:
    cd backend && ..\\.venv\\Scripts\\uvicorn astrowatch.app.main:app --reload --port 8000
브라우저: http://127.0.0.1:8000/  ·  API 문서: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from astrowatch.app.api import calendar, chat, logs, meta, plans, sessions
from astrowatch.app.errors import install_error_handlers
from astrowatch.config import ROOT_DIR, get_settings
from astrowatch.storage.db import init_db
from astrowatch.tools.registry import registry

# Windows 레지스트리에 따라 .js 가 text/plain 으로 잡히는 경우가 있어 명시한다
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

logger = logging.getLogger("astrowatch.app")

FRONTEND_DIR = ROOT_DIR / "frontend"


def _load_tools() -> None:
    try:
        from astrowatch.tools.registry import load_builtin_tools

        load_builtin_tools()
    except Exception as exc:  # noqa: BLE001 — Tool 일부가 없어도 서버는 뜬다
        logger.warning("Tool 로드 실패(일부 기능 제한): %s", type(exc).__name__)


def _start_logging_agent() -> None:
    try:
        from astrowatch.logging_agent.agent import start_logging_agent

        start_logging_agent()
    except Exception as exc:  # noqa: BLE001
        logger.warning("로깅 Agent 시작 실패: %s", type(exc).__name__)


def _stop_logging_agent() -> None:
    try:
        from astrowatch.logging_agent.agent import stop_logging_agent

        stop_logging_agent()
    except Exception as exc:  # noqa: BLE001
        logger.warning("로깅 Agent 종료 실패: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    if not registry.names():  # 재시작(--reload) 시 중복 등록 방지
        _load_tools()
    _start_logging_agent()
    try:
        yield
    finally:
        _stop_logging_agent()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Astrowatch API",
        description="Agent 기반 천문 관측 플래너",
        version="0.1.0",
        lifespan=lifespan,
    )
    # 개발용 — 모든 출처 허용
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.get("/api/health", tags=["meta"], summary="상태 확인")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "llm_configured": bool(get_settings().gms_key),
            "tools": registry.names(),
        }

    for module in (chat, plans, calendar, logs, sessions, meta):
        app.include_router(module.router, prefix="/api")

    # 정적 파일은 API 뒤에 마운트해야 /api 경로를 가리지 않는다
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
