"""대화 세션 API — 화면 복원용 목록 · 상세 · 삭제. 실행 로그(run_logs)는 건드리지 않는다."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, HTTPException, Response, status

from astrowatch.agent import memory
from astrowatch.app.api import DEFAULT_LIMIT, Limit, Offset, SessionId, page
from astrowatch.schemas.api import SessionDetailOut, SessionSummaryOut

router = APIRouter(prefix="/sessions", tags=["sessions"])

NOT_FOUND = "대화를 찾을 수 없습니다"


def _turns() -> ModuleType:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.storage import turns

    return turns


def _sessions() -> ModuleType:
    from astrowatch.storage import sessions

    return sessions


@router.get("", response_model=list[SessionSummaryOut], summary="대화 목록 (최근 순)")
def list_sessions(limit: Limit = DEFAULT_LIMIT, offset: Offset = 0) -> list[SessionSummaryOut]:
    return page(_turns().list_sessions(), limit, offset)


@router.get("/{session_id}", response_model=SessionDetailOut, summary="대화 상세 (턴 seq 순)")
def get_session(session_id: SessionId) -> SessionDetailOut:
    detail = _turns().get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return detail


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="대화 삭제 (실행 로그 유지)")
def delete_session(session_id: SessionId) -> Response:
    removed_turns = _turns().delete_session_turns(session_id)
    removed_session = _sessions().delete_session(session_id)
    memory.forget(session_id)       # 이 대화의 Tool 결과 캐시도 지운다 (세션 상태는 DB 에만 있다)
    if not removed_turns and not removed_session:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
