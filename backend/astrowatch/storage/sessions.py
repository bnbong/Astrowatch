"""대화 맥락 저장소 (F3010). 조건 객체 · 메모리 요약 · 직전 추천 · 대화 이력만 저장한다."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from astrowatch.schemas.domain import Conditions, JudgedCandidate
from astrowatch.storage.db import connect
from astrowatch.storage.plans import ensure_db


@dataclass
class SessionRecord:
    session_id: str
    conditions: Conditions
    memory_summary: str | None
    last_recommended: list[JudgedCandidate] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_session(session_id: str, *, db_path: Path | None = None) -> SessionRecord | None:
    path = ensure_db(db_path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    recommended = json.loads(row["last_recommended_json"] or "[]")
    return SessionRecord(
        session_id=row["session_id"],
        conditions=Conditions.model_validate(json.loads(row["conditions_json"] or "{}")),
        memory_summary=row["memory_summary"],
        last_recommended=[JudgedCandidate.model_validate(c) for c in recommended],
        history=json.loads(row["history_json"] or "[]"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def save_session(
    session_id: str,
    *,
    conditions: Conditions,
    memory_summary: str | None,
    last_recommended: list[JudgedCandidate],
    history: list[dict[str, str]],
    db_path: Path | None = None,
) -> None:
    """upsert. created_at 은 처음 저장 때만 기록한다."""
    path = ensure_db(db_path)
    now = _now()
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO sessions (session_id, conditions_json, memory_summary,
                   last_recommended_json, history_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   conditions_json = excluded.conditions_json,
                   memory_summary = excluded.memory_summary,
                   last_recommended_json = excluded.last_recommended_json,
                   history_json = excluded.history_json,
                   updated_at = excluded.updated_at""",
            (
                session_id,
                json.dumps(conditions.model_dump(mode="json"), ensure_ascii=False),
                memory_summary,
                json.dumps([c.model_dump(mode="json") for c in last_recommended], ensure_ascii=False),
                json.dumps(history, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )


def delete_session(session_id: str, *, db_path: Path | None = None) -> bool:
    path = ensure_db(db_path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    return cur.rowcount > 0
