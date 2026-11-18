"""대화 API (F3017 · F3018)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from astrowatch.schemas.api import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


def _run_turn(session_id: str | None, message: str) -> ChatResponse:
    # 지연 import — Agent 모듈은 첫 요청 때 불러온다 (테스트에서 이 함수를 교체)
    from astrowatch.agent import service

    return service.run_turn(session_id, message)


@router.post("/chat", response_model=ChatResponse, summary="메시지 한 턴 처리")
async def post_chat(req: ChatRequest) -> ChatResponse:
    # Agent 는 동기 호출(LLM · 외부 API)이므로 스레드에서 돌린다
    return await run_in_threadpool(_run_turn, req.session_id, req.message)
