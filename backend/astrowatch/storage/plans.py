"""관측 계획 저장소 (F3015~F3016).

datetime 은 ISO 문자열(현지 naive, 초 단위)로 저장하고 읽을 때 datetime 으로 되돌린다.
겹치는 계획이 있어도 저장은 막지 않고 overlaps 로 알려 준다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from astrowatch.config import get_settings
from astrowatch.schemas.plan import PlanCreate, PlanOut, PlanSaveResult, PlanUpdate
from astrowatch.storage.db import connect, init_db

_initialized: set[Path] = set()

# UPDATE 에 쓸 수 있는 컬럼 — PlanUpdate 필드 + updated_at. f-string SQL 에는 이 집합의 이름만 들어간다.
UPDATABLE_COLUMNS = frozenset(PlanUpdate.model_fields) | {"updated_at"}


def ensure_db(db_path: Path | None = None) -> Path:
    """스키마가 없으면 만든다 (경로별 1회). 저장소 모듈이 공유한다."""
    path = Path(db_path or get_settings().db_path)
    if path not in _initialized or not path.exists():
        init_db(path)
        _initialized.add(path)
    return path


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _now() -> str:
    return _iso(datetime.now())


def _row_to_plan(row: sqlite3.Row) -> PlanOut:
    data = dict(row)
    for key in ("start_dt", "end_dt", "created_at", "updated_at"):
        data[key] = datetime.fromisoformat(data[key])
    return PlanOut.model_validate(data)


def find_overlaps(
    start_dt: datetime,
    end_dt: datetime,
    exclude_id: int | None = None,
    *,
    db_path: Path | None = None,
) -> list[PlanOut]:
    """start < other.end and end > other.start 인 계획. 자정 넘김은 날짜 포함 비교로 처리된다."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        return _overlaps_in(conn, start_dt, end_dt, exclude_id)


def _overlaps_in(conn: sqlite3.Connection, start_dt: datetime, end_dt: datetime, exclude_id: int | None = None) -> list[PlanOut]:
    sql = "SELECT * FROM plans WHERE start_dt < ? AND end_dt > ?"
    params: list[object] = [_iso(end_dt), _iso(start_dt)]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " ORDER BY start_dt, id"
    return [_row_to_plan(r) for r in conn.execute(sql, params).fetchall()]


def create_plan(data: PlanCreate, *, db_path: Path | None = None) -> PlanSaveResult:
    path = ensure_db(db_path)
    now = _now()
    with connect(path) as conn:
        # 겹침 조회와 삽입을 한 트랜잭션(쓰기 잠금)에서 — 동시 저장이 서로를 놓치지 않게
        conn.execute("BEGIN IMMEDIATE")
        overlaps = _overlaps_in(conn, data.start_dt, data.end_dt)
        cur = conn.execute(
            """INSERT INTO plans (session_id, event_title, event_kind, start_dt, end_dt,
                   location_label, lat, lon, place_id, place_url, target_alt, target_az,
                   basis, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.session_id, data.event_title, data.event_kind.value,
                _iso(data.start_dt), _iso(data.end_dt), data.location_label,
                data.lat, data.lon, data.place_id, data.place_url,
                data.target_alt, data.target_az, data.basis, now, now,
            ),
        )
        plan_id = int(cur.lastrowid)
    plan = get_plan(plan_id, db_path=path)
    assert plan is not None
    return PlanSaveResult(plan=plan, overlaps=overlaps)


def list_plans(*, db_path: Path | None = None) -> list[PlanOut]:
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM plans ORDER BY start_dt, id").fetchall()
    return [_row_to_plan(r) for r in rows]


def get_plan(plan_id: int, *, db_path: Path | None = None) -> PlanOut | None:
    path = ensure_db(db_path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return _row_to_plan(row) if row else None


def update_plan(plan_id: int, data: PlanUpdate, *, db_path: Path | None = None) -> PlanOut | None:
    """보낸 필드만 갱신. 결과가 end<=start 이면 ValueError."""
    path = ensure_db(db_path)
    current = get_plan(plan_id, db_path=path)
    if current is None:
        return None
    # 보낸 필드만 반영한다. 명시적 null 은 basis 처럼 비울 수 있는 필드에만 허용한다.
    changes = data.model_dump(exclude_unset=True)
    for key in ("event_title", "start_dt", "end_dt", "location_label"):
        if key in changes and changes[key] is None:
            raise ValueError(f"{key} 는 비울 수 없다")
    if not changes:
        return current
    start = changes.get("start_dt", current.start_dt)
    end = changes.get("end_dt", current.end_dt)
    if end <= start:
        raise ValueError("end_dt 는 start_dt 보다 뒤여야 한다")
    values = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in changes.items()}
    values["updated_at"] = _now()
    unknown = set(values) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"수정할 수 없는 필드: {sorted(unknown)}")
    cols = ", ".join(f"{k} = ?" for k in values)  # 키는 UPDATABLE_COLUMNS 로 검증됨
    with connect(path) as conn:
        conn.execute(f"UPDATE plans SET {cols} WHERE id = ?", [*values.values(), plan_id])
    return get_plan(plan_id, db_path=path)


def delete_plan(plan_id: int, *, db_path: Path | None = None) -> bool:
    path = ensure_db(db_path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return cur.rowcount > 0


def set_calendar_event_id(plan_id: int, event_id: str | None, *, db_path: Path | None = None) -> None:
    """캘린더 등록 결과 ID 기록 (심화 F3023)."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        conn.execute(
            "UPDATE plans SET calendar_event_id = ?, updated_at = ? WHERE id = ?",
            (event_id, _now(), plan_id),
        )
