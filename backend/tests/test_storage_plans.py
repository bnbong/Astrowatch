"""관측 계획 저장소 (F3015~F3016)."""

from __future__ import annotations

from datetime import datetime

import pytest

from astrowatch.schemas.plan import PlanCreate, PlanUpdate
from astrowatch.storage import plans
from astrowatch.tools.contracts import EventKind


def _plan(start: datetime, end: datetime, title: str = "쌍둥이자리 유성우 극대") -> PlanCreate:
    return PlanCreate(
        event_title=title,
        event_kind=EventKind.METEOR,
        start_dt=start,
        end_dt=end,
        location_label="서울",
        lat=37.56,
        lon=126.97,
        basis="충족 · 맑은 시간 180분",
        session_id="s1",
    )


def test_crud_roundtrip():
    res = plans.create_plan(_plan(datetime(2026, 12, 14, 22, 0), datetime(2026, 12, 15, 1, 0)))
    p = res.plan
    assert p.id > 0 and res.overlaps == []
    assert p.start_dt == datetime(2026, 12, 14, 22, 0)
    assert p.end_dt == datetime(2026, 12, 15, 1, 0)
    assert p.event_kind == EventKind.METEOR and p.session_id == "s1"
    assert isinstance(p.created_at, datetime)

    assert plans.get_plan(p.id) == p
    assert [x.id for x in plans.list_plans()] == [p.id]

    upd = plans.update_plan(p.id, PlanUpdate(event_title="수정됨", end_dt=datetime(2026, 12, 15, 2, 0)))
    assert upd is not None
    assert upd.event_title == "수정됨"
    assert upd.end_dt == datetime(2026, 12, 15, 2, 0)
    assert upd.location_label == "서울"  # 안 보낸 필드 유지

    plans.set_calendar_event_id(p.id, "cal-1")
    assert plans.get_plan(p.id).calendar_event_id == "cal-1"

    assert plans.delete_plan(p.id) is True
    assert plans.get_plan(p.id) is None
    assert plans.list_plans() == []


def test_list_sorted_by_start():
    b = plans.create_plan(_plan(datetime(2026, 12, 20, 21, 0), datetime(2026, 12, 20, 23, 0))).plan
    a = plans.create_plan(_plan(datetime(2026, 12, 10, 21, 0), datetime(2026, 12, 10, 23, 0))).plan
    assert [x.id for x in plans.list_plans()] == [a.id, b.id]


def test_overlap_detected_but_not_blocking():
    first = plans.create_plan(_plan(datetime(2026, 12, 14, 22, 0), datetime(2026, 12, 15, 1, 0))).plan
    second = plans.create_plan(_plan(datetime(2026, 12, 14, 23, 30), datetime(2026, 12, 15, 2, 0)))
    assert [o.id for o in second.overlaps] == [first.id]
    assert len(plans.list_plans()) == 2  # 저장은 됨

    # 다음 날 22:00 은 겹치지 않는다
    third = plans.create_plan(_plan(datetime(2026, 12, 15, 22, 0), datetime(2026, 12, 16, 0, 30)))
    assert third.overlaps == []

    # 경계만 맞닿으면 겹침 아님
    assert plans.find_overlaps(datetime(2026, 12, 15, 2, 0), datetime(2026, 12, 15, 3, 0)) == []
    # exclude_id
    found = plans.find_overlaps(first.start_dt, first.end_dt, exclude_id=first.id)
    assert [o.id for o in found] == [second.plan.id]


def test_update_invalid_order_raises():
    p = plans.create_plan(_plan(datetime(2026, 12, 14, 22, 0), datetime(2026, 12, 15, 1, 0))).plan
    with pytest.raises(ValueError):
        plans.update_plan(p.id, PlanUpdate(end_dt=datetime(2026, 12, 14, 21, 0)))
    with pytest.raises(ValueError):
        plans.update_plan(p.id, PlanUpdate(start_dt=datetime(2026, 12, 15, 1, 0)))
    assert plans.get_plan(p.id).end_dt == datetime(2026, 12, 15, 1, 0)


def test_missing_id():
    assert plans.get_plan(999) is None
    assert plans.update_plan(999, PlanUpdate(event_title="x")) is None
    assert plans.delete_plan(999) is False


def test_update_can_clear_basis_but_not_required_fields(tmp_path):
    from datetime import datetime

    import pytest

    from astrowatch.schemas.plan import PlanCreate, PlanUpdate
    from astrowatch.storage.plans import create_plan, update_plan

    db = tmp_path / "p.db"
    plan = create_plan(
        PlanCreate(event_title="e", start_dt=datetime(2026, 12, 14, 22), end_dt=datetime(2026, 12, 15, 1),
                   location_label="서울", basis="맑음"),
        db_path=db,
    ).plan
    assert update_plan(plan.id, PlanUpdate(basis=None), db_path=db).basis is None
    assert update_plan(plan.id, PlanUpdate(), db_path=db).event_title == "e"  # 보낸 것 없음 → 그대로
    with pytest.raises(ValueError):
        update_plan(plan.id, PlanUpdate(event_title=None), db_path=db)
