"""대화 맥락 (F3010) — 세션 저장소(SQLite) 래퍼.

세션 상태(조건, 요약, 직전 추천 제목, 대화 이력)의 기준은 SQLite 하나다. 메모리 사본을 두지 않는다.
저장소 장애는 응답을 막지 않지만 성공으로 숨기지도 않는다: 경고 로그를 남기고 빈 세션/False 를 돌려준다.

Tool 원본 응답은 세션에 저장하지 않는다. 다만 같은 프로세스 안에서 현상 조회 결과는
재사용하도록 메모리에만 잠깐 둔다 (_tool_cache, DB 에 남지 않음). 대화를 지우면 함께 지운다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from astrowatch.schemas.domain import Conditions
from astrowatch.storage import sessions as store

logger = logging.getLogger("astrowatch.agent.memory")

MAX_TURNS = 6                       # 보관할 최근 턴 수 (user+assistant = 1턴)
REUSABLE_TOOLS = ("search_sky_events",)  # 날짜·지역과 무관하게 변하지 않는 결과
_MAX_CACHE_ENTRIES = 24


@dataclass
class Session:
    session_id: str
    conditions: Conditions = field(default_factory=Conditions)
    memory_summary: str | None = None
    last_recommended: list[str] = field(default_factory=list)   # 직전 추천 제목 (캘린더 "먼저 저장" 안내용)
    history: list[dict[str, str]] = field(default_factory=list)


_tool_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def new_session_id() -> str:
    return store.new_session_id()


def load(session_id: str) -> Session:
    """세션을 읽는다. 없거나 저장소를 읽지 못하면 빈 세션 (읽기 실패는 경고 로그)."""
    try:
        rec = store.load_session(session_id)
    except Exception as exc:  # noqa: BLE001 (저장소 장애는 응답을 막지 않는다)
        logger.warning("세션 읽기 실패(빈 맥락으로 진행): %s", type(exc).__name__)
        return Session(session_id=session_id)
    if rec is None:
        return Session(session_id=session_id)
    return Session(
        session_id=session_id,
        conditions=rec.conditions or Conditions(),
        memory_summary=rec.memory_summary,
        last_recommended=list(rec.last_recommended),
        history=list(rec.history),
    )


def trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    return history[-MAX_TURNS * 2:]


def save(session: Session) -> bool:
    """저장 성공이면 True. 실패는 경고 로그만 남기고 False (응답은 정상 반환)."""
    session.history = trim_history(session.history)
    try:
        store.save_session(
            session.session_id,
            conditions=session.conditions,
            memory_summary=session.memory_summary,
            last_recommended=session.last_recommended,
            history=session.history,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("세션 저장 실패(응답은 정상 반환, 다음 턴은 이 턴의 맥락 없이 시작): %s", type(exc).__name__)
        return False


def forget(session_id: str) -> None:
    """대화 삭제 시 이 세션의 Tool 결과 캐시를 지운다 (세션 행은 storage.sessions.delete_session)."""
    with _lock:
        _tool_cache.pop(session_id, None)


# ── 프로세스 내 Tool 결과 재사용 (DB 미저장) ─────────────────────

def cached_tool_results(session_id: str) -> dict[str, Any]:
    with _lock:
        return dict(_tool_cache.get(session_id, {}))


def remember_tool_results(session_id: str, cache: dict[str, Any]) -> None:
    keep = {k: v for k, v in cache.items() if k.split("|", 1)[0] in REUSABLE_TOOLS and getattr(v, "ok", False)}
    if not keep:
        return
    with _lock:
        cur = _tool_cache.setdefault(session_id, {})
        cur.update(keep)
        while len(cur) > _MAX_CACHE_ENTRIES:
            cur.pop(next(iter(cur)))


def reset_memory() -> None:
    """테스트용."""
    with _lock:
        _tool_cache.clear()
