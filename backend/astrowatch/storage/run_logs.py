"""실행 로그 저장소 (F3013~F3014). 덧붙이기·조회·세션 삭제만 — 수정 함수는 두지 않는다."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from astrowatch.events.bus import Event, EventType
from astrowatch.schemas.api import LogEventOut, LogSessionOut
from astrowatch.storage.db import connect
from astrowatch.storage.plans import ensure_db


def append_event(event: Event, *, db_path: Path | None = None) -> None:
    """같은 (session_id, seq) 는 무시한다."""
    path = ensure_db(db_path)
    etype = event.type.value if isinstance(event.type, EventType) else str(event.type)
    ts = event.ts.isoformat() if isinstance(event.ts, datetime) else str(event.ts)
    with connect(path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO run_logs (session_id, seq, ts, type, stage, tool, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.session_id, event.seq, ts, etype, event.stage, event.tool,
                json.dumps(event.payload or {}, ensure_ascii=False, default=str),
            ),
        )


def max_seq(session_id: str, *, db_path: Path | None = None) -> int:
    """세션의 마지막 seq. 없으면 0. EventBus.seq_seed 훅에 꽂아 재시작 뒤 번호가 이어지게 한다."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        row = conn.execute("SELECT MAX(seq) FROM run_logs WHERE session_id = ?", (session_id,)).fetchone()
    return int(row[0] or 0) if row else 0


def _load_payload(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {"value": value}


def list_sessions(*, db_path: Path | None = None) -> list[LogSessionOut]:
    """세션별 집계. 마지막 이벤트 시각 내림차순."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute(
            """SELECT session_id,
                      MIN(ts) AS started_at,
                      MAX(ts) AS last_at,
                      COUNT(*) AS event_count,
                      SUM(type = 'tool_call') AS tool_calls,
                      SUM(type = 'replan') AS replans,
                      (SELECT s.payload FROM run_logs s
                        WHERE s.session_id = r.session_id AND s.type = 'session_start'
                        ORDER BY s.seq LIMIT 1) AS start_payload
                 FROM run_logs r
                GROUP BY session_id
                ORDER BY last_at DESC"""
        ).fetchall()
    out: list[LogSessionOut] = []
    for r in rows:
        message = None
        if r["start_payload"]:
            msg = _load_payload(r["start_payload"]).get("message")
            message = str(msg) if msg is not None else None
        out.append(
            LogSessionOut(
                session_id=r["session_id"],
                started_at=datetime.fromisoformat(r["started_at"]),
                last_at=datetime.fromisoformat(r["last_at"]),
                event_count=r["event_count"],
                tool_calls=r["tool_calls"] or 0,
                replans=r["replans"] or 0,
                first_message=message,
            )
        )
    return out


def get_session_events(session_id: str, *, db_path: Path | None = None) -> list[LogEventOut]:
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM run_logs WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
    return [
        LogEventOut(
            session_id=r["session_id"],
            seq=r["seq"],
            ts=datetime.fromisoformat(r["ts"]),
            type=r["type"],
            stage=r["stage"],
            tool=r["tool"],
            payload=_load_payload(r["payload"]),
        )
        for r in rows
    ]


def delete_session(session_id: str, *, db_path: Path | None = None) -> bool:
    path = ensure_db(db_path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM run_logs WHERE session_id = ?", (session_id,))
    return cur.rowcount > 0
