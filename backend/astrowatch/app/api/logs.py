"""실행 로그 API (F3014). 조회 · 삭제만 — 수정 API 는 두지 않는다."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, HTTPException, Response, status

from astrowatch.schemas.api import LogEventOut, LogSessionOut

router = APIRouter(prefix="/logs", tags=["logs"])

NOT_FOUND = "실행 로그를 찾을 수 없습니다"


def _store() -> ModuleType:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.storage import run_logs

    return run_logs


@router.get("", response_model=list[LogSessionOut], summary="세션 목록")
def list_sessions() -> list[LogSessionOut]:
    return _store().list_sessions()


@router.get("/{session_id}", response_model=list[LogEventOut], summary="세션 이벤트 (seq 순)")
def get_session_events(session_id: str) -> list[LogEventOut]:
    events = _store().get_session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return sorted(events, key=lambda e: e.seq)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="세션 로그 삭제")
def delete_session(session_id: str) -> Response:
    if not _store().delete_session(session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
