"""대화 맥락 (F3010) — 세션 저장소 래퍼.

astrowatch.storage.sessions 가 없거나 실패하면 프로세스 메모리 dict 로 대신한다.
Tool 원본 응답은 세션에 저장하지 않는다. 다만 같은 프로세스 안에서 현상 조회 결과는
재사용하도록 메모리에만 잠깐 둔다 (DB 에 남지 않음).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from astrowatch.schemas.domain import Conditions, JudgedCandidate

MAX_TURNS = 6                       # 보관할 최근 턴 수 (user+assistant = 1턴)
REUSABLE_TOOLS = ("search_sky_events",)  # 날짜·지역과 무관하게 변하지 않는 결과
_MAX_CACHE_ENTRIES = 24


@dataclass
class Session:
    session_id: str
    conditions: Conditions = field(default_factory=Conditions)
    memory_summary: str | None = None
    last_recommended: list[JudgedCandidate] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)


_mem: dict[str, Session] = {}
_tool_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _storage() -> Any | None:
    """세션 저장소 모듈. 없거나 함수가 빠졌으면 None."""
    try:
        from astrowatch.storage import sessions as mod
    except Exception:  # noqa: BLE001
        return None
    if all(hasattr(mod, n) for n in ("load_session", "save_session", "new_session_id")):
        return mod
    return None


def new_session_id() -> str:
    mod = _storage()
    if mod is not None:
        try:
            return str(mod.new_session_id())
        except Exception:  # noqa: BLE001
            pass
    return uuid.uuid4().hex[:12]


def load(session_id: str) -> Session:
    """세션을 읽는다. 없으면 빈 세션."""
    mod = _storage()
    if mod is not None:
        try:
            rec = mod.load_session(session_id)
            if rec is not None:
                return Session(
                    session_id=session_id,
                    conditions=getattr(rec, "conditions", None) or Conditions(),
                    memory_summary=getattr(rec, "memory_summary", None),
                    last_recommended=list(getattr(rec, "last_recommended", None) or []),
                    history=list(getattr(rec, "history", None) or []),
                )
            if session_id not in _mem:
                return Session(session_id=session_id)
        except Exception:  # noqa: BLE001 — 저장소 장애 시 메모리로
            pass
    with _lock:
        return _mem.get(session_id) or Session(session_id=session_id)


def trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    return history[-MAX_TURNS * 2:]


def save(session: Session) -> None:
    session.history = trim_history(session.history)
    with _lock:
        _mem[session.session_id] = session      # 저장소가 실패해도 이번 프로세스에서는 이어진다
    mod = _storage()
    if mod is None:
        return
    try:
        mod.save_session(
            session.session_id,
            conditions=session.conditions,
            memory_summary=session.memory_summary,
            last_recommended=session.last_recommended,
            history=session.history,
        )
    except Exception:  # noqa: BLE001
        pass


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
        _mem.clear()
        _tool_cache.clear()
