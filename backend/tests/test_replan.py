"""재계획 전략 선택 (F3009)."""

from __future__ import annotations

import datetime as dt

from astrowatch.agent.replan import (
    NEARBY_REGION,
    OTHER_EVENTS_SAME_PERIOD,
    SAME_EVENT_OTHER_DATES,
    next_strategy,
    tried_until,
)
from astrowatch.schemas.domain import Conditions
from astrowatch.tools.contracts import EventKind

BASE = Conditions(date_from=dt.date(2026, 11, 21), date_to=dt.date(2026, 11, 22), region="대전",
                  start_minute=1260, end_minute=1440, event_kind=EventKind.METEOR)


def test_order_and_changes():
    name, c1 = next_strategy(BASE, [], 0, [])
    assert name == SAME_EVENT_OTHER_DATES
    assert (c1.date_from, c1.date_to) == (dt.date(2026, 11, 19), dt.date(2026, 11, 24))

    name, c2 = next_strategy(c1, [], 1, tried_until(name))
    assert name == OTHER_EVENTS_SAME_PERIOD
    assert c2.event_kind is None and c2.event_query is None

    name, c3 = next_strategy(c2, [], 2, tried_until(name))
    assert name == NEARBY_REGION
    assert c3.region not in (None, "대전")

    assert next_strategy(c3, [], 3, tried_until(name)) is None


def test_fixed_date_skips_first():
    c = BASE.model_copy(update={"fixed": ["date_from"]})
    name, _ = next_strategy(c, [], 0, [])
    assert name == OTHER_EVENTS_SAME_PERIOD


def test_fixed_event_skips_second():
    c = BASE.model_copy(update={"fixed": ["date_from", "event_kind"]})
    name, out = next_strategy(c, [], 0, [])
    assert name == NEARBY_REGION
    assert out.event_kind == EventKind.METEOR


def test_all_fixed_exhausted():
    c = BASE.model_copy(update={"fixed": ["date_from", "event_kind", "region"]})
    assert next_strategy(c, [], 0, []) is None


def test_no_event_to_release_skips_second():
    c = BASE.model_copy(update={"event_kind": None})
    assert next_strategy(c, [], 1, [SAME_EVENT_OTHER_DATES])[0] == NEARBY_REGION


def test_tried_strategies_skipped():
    assert next_strategy(BASE, [], 0, [SAME_EVENT_OTHER_DATES, OTHER_EVENTS_SAME_PERIOD])[0] == NEARBY_REGION


def test_fixed_date_to_widens_start_only():
    c = BASE.model_copy(update={"fixed": ["date_to"]})
    name, out = next_strategy(c, [], 0, [])
    assert name == SAME_EVENT_OTHER_DATES
    assert (out.date_from, out.date_to) == (dt.date(2026, 11, 19), dt.date(2026, 11, 22))


def test_both_dates_fixed_skips_first():
    c = BASE.model_copy(update={"fixed": ["date_from", "date_to"]})
    assert next_strategy(c, [], 0, [])[0] == OTHER_EVENTS_SAME_PERIOD


def test_date_defaulted_and_deep_sky_flags():
    from astrowatch.schemas.domain import Candidate, JudgedCandidate, Verdict

    c = BASE.model_copy(update={"event_kind": EventKind.DEEP_SKY, "event_query": "안드로메다 은하"})
    deep = JudgedCandidate(verdict=Verdict.UNFIT, candidate=Candidate(
        event_id="t", title="안드로메다 은하", kind=EventKind.DEEP_SKY, date=c.date_from, region="대전",
        lat=36.35, lon=127.38))
    assert next_strategy(c, [deep], 0, [], date_defaulted=True)[0] == NEARBY_REGION
    assert next_strategy(c, [deep], 0, [])[0] == SAME_EVENT_OTHER_DATES


def test_never_rises_stops_replan():
    from astrowatch.schemas.domain import Candidate, JudgedCandidate, Verdict

    cand = Candidate(event_id="target-20261121", title="남쪽 성단", kind=EventKind.DEEP_SKY, date=dt.date(2026, 11, 21),
                     region="대전", lat=36.35, lon=127.38)
    j = JudgedCandidate(candidate=cand, verdict=Verdict.UNFIT,
                        reasons=["남쪽 성단: 그날 밤 지평선 위로 20° 이상 오르지 않음"])
    assert next_strategy(BASE, [j], 0, []) is None
