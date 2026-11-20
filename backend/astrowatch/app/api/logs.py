"""실행 로그 API (F3014). 조회 · 삭제만 — 수정 API 는 두지 않는다.

심화 F3020: 로깅 Agent 판정 조회(GET .../audits, .../audit)와 지금 판정(POST .../audit).
POST .../audit 는 IP 당 분당 AUDIT_RATE_PER_MIN 회로 제한한다 (RequestGuardMiddleware). 같은 구간에 실패하지 않은
진단이 이미 저장돼 있으면 LLM 진단을 다시 부르지 않고 그 진단을 쓴다 (?force=1 이면 다시 진단).
"""

from __future__ import annotations

from types import ModuleType
import logging

from fastapi import APIRouter, HTTPException, Query, Response, status

from astrowatch.app.api import DEFAULT_LIMIT, Limit, Offset, SessionId, page
from astrowatch.schemas.api import LogEventOut, LogSessionOut
from astrowatch.schemas.audit import AuditDiagnosis, AuditResult

router = APIRouter(prefix="/logs", tags=["logs"])

NOT_FOUND = "실행 로그를 찾을 수 없습니다"
AUDIT_NOT_FOUND = "판정 기록이 없습니다"
log = logging.getLogger(__name__)


def _store() -> ModuleType:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.storage import run_logs

    return run_logs


def _audits() -> ModuleType:
    from astrowatch.storage import run_audits

    return run_audits


def _with_audit(sessions: list[LogSessionOut]) -> list[LogSessionOut]:
    """세션 목록에 최신 판정 배지 정보를 붙인다. 판정 저장소 문제는 배지만 비운다."""
    try:
        latest = _audits().latest_by_session()
    except Exception as exc:  # noqa: BLE001
        log.warning("판정 목록 읽기 실패: %s", type(exc).__name__)
        return sessions
    out = []
    for s in sessions:
        a = latest.get(s.session_id)
        out.append(s.model_copy(update={"audit_status": a.status.value, "audit_warnings": a.warning_count}) if a else s)
    return out


@router.get("", response_model=list[LogSessionOut], summary="세션 목록")
def list_sessions(limit: Limit = DEFAULT_LIMIT, offset: Offset = 0) -> list[LogSessionOut]:
    return page(_with_audit(_store().list_sessions()), limit, offset)


@router.get("/{session_id}", response_model=list[LogEventOut], summary="세션 이벤트 (seq 순)")
def get_session_events(session_id: SessionId, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0) -> list[LogEventOut]:
    events = _store().get_session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return page(sorted(events, key=lambda e: e.seq), limit, offset)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="세션 로그 삭제")
def delete_session(session_id: SessionId) -> Response:
    if not _store().delete_session(session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        _audits().delete_session(session_id)   # 근거 로그가 없어진 판정도 함께 지운다
    except Exception as exc:  # noqa: BLE001
        log.warning("판정 삭제 실패: %s", type(exc).__name__)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── 심화 F3020: 로깅 Agent 판정 ─────────────────────────────

@router.get("/{session_id}/audits", response_model=list[AuditResult], summary="판정 목록 (최신 먼저)")
def list_audits(session_id: SessionId) -> list[AuditResult]:
    return _audits().list_audits(session_id)


@router.get("/{session_id}/audit", response_model=AuditResult, summary="최신 판정")
def get_latest_audit(session_id: SessionId) -> AuditResult:
    latest = _audits().get_latest(session_id)
    if latest is None:
        raise HTTPException(status_code=404, detail=AUDIT_NOT_FOUND)
    return latest


@router.post("/{session_id}/audit", response_model=AuditResult, summary="세션 전체를 지금 판정")
def run_audit(session_id: SessionId,
              force: bool = Query(default=False, description="1 이면 저장된 진단이 있어도 LLM 진단을 다시 한다")) -> AuditResult:
    """규칙 판정은 항상 저장한다. 이상이면 진단을 시도하고, 실패해도 판정은 그대로 돌려준다.

    같은 (seq_from, seq_to) 구간에 실패하지 않은 진단이 저장돼 있고 판정 내용이 같으면 그 진단을 재사용한다."""
    from astrowatch.logging_agent.audit import audit_events
    from astrowatch.logging_agent.diagnosis import diagnose

    events = _store().get_session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    examined = [e for e in events if not str(e.type).startswith("audit_")]
    if examined:
        result = audit_events(session_id, examined, seq_from=min(e.seq for e in examined),
                              seq_to=max(e.seq for e in examined))
    else:
        result = audit_events(session_id, [])
    stored = None if force else _stored_diagnosis(result)
    result = result.model_copy(update={"diagnosis": stored if stored is not None else diagnose(result, examined)})
    _audits().save_audit(result)
    return result


def _stored_diagnosis(result: AuditResult) -> AuditDiagnosis | None:
    """같은 구간, 같은 판정의 저장된 성공 진단. 없거나 저장소 문제면 None (진단을 새로 한다)."""
    try:
        rows = _audits().list_audits(result.session_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("저장된 판정 읽기 실패: %s", type(exc).__name__)
        return None
    for a in rows:
        if (a.seq_from, a.seq_to) != (result.seq_from, result.seq_to):
            continue
        same = a.status == result.status and [f.model_dump() for f in a.findings] == [f.model_dump() for f in result.findings]
        if same and a.diagnosis is not None and not a.diagnosis.failed:
            return a.diagnosis
    return None
