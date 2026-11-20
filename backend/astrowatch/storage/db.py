"""SQLite 연결 · 초기화. 저장소 모듈(plans.py, run_logs.py, sessions.py)이 공유한다."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from astrowatch.config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
    return path


_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("plans", "target_object", "TEXT"),   # 하늘 가리키기 (3일차 추가)
)


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE IF NOT EXISTS 로는 못 붙는 열을 기존 DB 에 추가한다. 이미 있으면 건너뛴다."""
    for table, column, decl in _ADDED_COLUMNS:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or get_settings().db_path
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
