"""대화 턴 저장소 — 화면 복원·대화 목록용 (GET/DELETE /api/sessions).

LLM 맥락(sessions.history_json)과 실행 로그(run_logs)와는 별개다. 턴 단위로 사용자 메시지와
당시 ChatResponse JSON 을 그대로 보관한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from astrowatch.schemas.api import ChatResponse, SessionDetailOut, SessionSummaryOut, TurnOut
from astrowatch.storage.db import connect
from astrowatch.storage.plans import ensure_db

logger = logging.getLogger("astrowatch.storage.turns")

TITLE_LEN = 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _title(message: str | None) -> str:
    return (message or "").strip()[:TITLE_LEN]


def _parse(raw: str | None, session_id: str, seq: int) -> ChatResponse | None:
    try:
        return ChatResponse.model_validate_json(raw or "")
    except (ValidationError, ValueError):
        logger.warning("대화 턴 역직렬화 실패(건너뜀): session=%s seq=%s", session_id, seq)
        return None


def append_turn(
    session_id: str, user_message: str, response: ChatResponse, *, db_path: Path | None = None
) -> int:
    """턴 하나를 추가하고 seq(그 세션의 max+1)를 돌려준다."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        # seq 계산과 INSERT 를 한 문장으로 — 동시 요청에서도 같은 seq 를 두 번 쓰지 않게
        cur = conn.execute(
            """INSERT INTO turns (session_id, seq, ts, user_message, response_json)
               SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM turns WHERE session_id = ?""",
            (session_id, _now(), user_message, response.model_dump_json(), session_id),
        )
        row = conn.execute("SELECT seq FROM turns WHERE rowid = ?", (cur.lastrowid,)).fetchone()
    return int(row[0])


def list_sessions(*, db_path: Path | None = None) -> list[SessionSummaryOut]:
    """대화 목록. 마지막 턴 시각 내림차순."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT session_id,
                      MIN(ts) AS started_at,
                      MAX(ts) AS last_at,
                      COUNT(*) AS turn_count,
                      (SELECT f.user_message FROM turns f
                        WHERE f.session_id = t.session_id ORDER BY f.seq LIMIT 1) AS first_message,
                      (SELECT l.response_json FROM turns l
                        WHERE l.session_id = t.session_id ORDER BY l.seq DESC LIMIT 1) AS last_response,
                      MAX(seq) AS last_seq
                 FROM turns t
                GROUP BY session_id
                ORDER BY last_at DESC, session_id"""
        ).fetchall()
    out: list[SessionSummaryOut] = []
    for r in rows:
        last = _parse(r["last_response"], r["session_id"], r["last_seq"])
        out.append(
            SessionSummaryOut(
                session_id=r["session_id"],
                title=_title(r["first_message"]),
                started_at=datetime.fromisoformat(r["started_at"]),
                last_at=datetime.fromisoformat(r["last_at"]),
                turn_count=r["turn_count"],
                last_intent=_intent_value(last),
            )
        )
    return out


def _intent_value(response: ChatResponse | None) -> str | None:
    if response is None:
        return None
    intent: Any = response.intent
    return getattr(intent, "value", None) or str(intent)


def get_session_detail(session_id: str, *, db_path: Path | None = None) -> SessionDetailOut | None:
    """턴 전체(seq 순). 턴이 없으면 None. 깨진 턴은 건너뛴다."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,)).fetchall()
    if not rows:
        return None
    turns: list[TurnOut] = []
    for r in rows:
        response = _parse(r["response_json"], session_id, r["seq"])
        if response is None:
            continue
        turns.append(
            TurnOut(
                session_id=session_id,
                seq=r["seq"],
                ts=datetime.fromisoformat(r["ts"]),
                user_message=r["user_message"],
                response=response,
            )
        )
    return SessionDetailOut(
        session_id=session_id,
        title=_title(rows[0]["user_message"]),
        started_at=min(datetime.fromisoformat(r["ts"]) for r in rows),
        last_at=max(datetime.fromisoformat(r["ts"]) for r in rows),
        turns=turns,
    )


def delete_session_turns(session_id: str, *, db_path: Path | None = None) -> int:
    """그 세션의 턴을 모두 지우고 지운 행 수를 돌려준다."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
    return cur.rowcount
