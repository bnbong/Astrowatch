"""재계획 전략 (F3009). 순수 함수 — 다음에 바꿀 조건을 고른다.

순서: ① 같은 현상 다른 날짜(±2일) → ② 같은 기간 다른 현상 → ③ 인접 지역.
사용자가 못박은 값(conditions.fixed)은 바꾸지 않는다. 상한은 호출하는 쪽(settings.max_replans).
"""

from __future__ import annotations

import datetime as dt

from astrowatch.agent.judge import NEVER_RISES
from astrowatch.data.regions import find_region, nearby_regions
from astrowatch.schemas.domain import Conditions, JudgedCandidate, Verdict
from astrowatch.tools.contracts import EventKind

SAME_EVENT_OTHER_DATES = "same_event_other_dates"
OTHER_EVENTS_SAME_PERIOD = "other_events_same_period"
NEARBY_REGION = "nearby_region"

STRATEGY_ORDER = (SAME_EVENT_OTHER_DATES, OTHER_EVENTS_SAME_PERIOD, NEARBY_REGION)

STRATEGY_LABEL = {
    SAME_EVENT_OTHER_DATES: "같은 현상 다른 날짜(±2일)",
    OTHER_EVENTS_SAME_PERIOD: "같은 기간 다른 현상",
    NEARBY_REGION: "인접 지역",
}

WIDEN_DAYS = 2


def tried_until(last: str | None) -> list[str]:
    """전략은 순서대로만 진행하므로 마지막 전략까지가 시도한 목록이다."""
    if last not in STRATEGY_ORDER:
        return []
    return list(STRATEGY_ORDER[: STRATEGY_ORDER.index(last) + 1])


def _widen_dates(c: Conditions) -> Conditions | None:
    """시작 날짜가 고정이면 생략. 끝 날짜(date_to)만 고정이면 앞쪽으로만 넓힌다."""
    if "date_from" in c.fixed or c.date_from is None:
        return None
    start = c.date_from - dt.timedelta(days=WIDEN_DAYS)
    end = c.date_to or c.date_from
    if "date_to" not in c.fixed:
        end = end + dt.timedelta(days=WIDEN_DAYS)
    return c.model_copy(update={"date_from": start, "date_to": end})


def _release_event(c: Conditions) -> Conditions | None:
    if {"event_query", "event_kind"} & set(c.fixed):
        return None
    if c.event_query is None and c.event_kind is None:
        return None  # 풀 조건이 없다
    return c.model_copy(update={"event_query": None, "event_kind": None})


def _nearby(c: Conditions) -> Conditions | None:
    if "region" in c.fixed:
        return None
    region = find_region(c.region)
    if region is None:
        return None
    cands = nearby_regions(region, limit=1)
    if not cands:
        return None
    return c.model_copy(update={"region": cands[0].name})


_BUILDERS = {
    SAME_EVENT_OTHER_DATES: _widen_dates,
    OTHER_EVENTS_SAME_PERIOD: _release_event,
    NEARBY_REGION: _nearby,
}


def next_strategy(
    conditions: Conditions,
    judged: list[JudgedCandidate],
    replan_count: int,
    tried: list[str],
    *,
    date_defaulted: bool = False,
) -> tuple[str, Conditions] | None:
    """다음 전략과 바뀐 조건. 더 시도할 전략이 없으면 None.

    - date_defaulted: 날짜를 기본 범위로 채웠으면 이미 넓으므로 ① 생략
    - 특정 천체(DEEP_SKY) 후보가 있었으면 다른 현상을 추천해도 의미가 없으므로 ② 생략
    - 모든 후보가 '그날 밤 고도 기준 이상으로 오르지 않음'이면 ±2일·인접 지역도 같으므로 재계획 안 함
    """
    if judged and all(any(NEVER_RISES in r for r in j.reasons) for j in judged):
        return None
    deep_sky = any(j.candidate.kind == EventKind.DEEP_SKY for j in judged)
    for name in STRATEGY_ORDER:
        if name in tried:
            continue
        if name == SAME_EVENT_OTHER_DATES and date_defaulted:
            continue
        if name == OTHER_EVENTS_SAME_PERIOD and deep_sky:
            continue
        updated = _BUILDERS[name](conditions)
        if updated is not None:
            return name, updated
    return None


def describe(strategy: str, before: Conditions, after: Conditions) -> str:
    """REPLAN 이벤트·화면에 보일 한 줄 사유."""
    if strategy == SAME_EVENT_OTHER_DATES:
        return f"날짜 범위를 {after.date_from}~{after.date_to} 로 넓힘"
    if strategy == OTHER_EVENTS_SAME_PERIOD:
        target = before.event_query or (before.event_kind.value if before.event_kind else "현상")
        return f"'{target}' 지정을 풀고 같은 기간의 다른 현상 탐색"
    if strategy == NEARBY_REGION:
        return f"지역을 {before.region} → {after.region} 로 바꿈"
    return strategy


def _never_rises(j: JudgedCandidate) -> bool:
    return any(NEVER_RISES in r for r in j.reasons)


def exhausted_stop_reason(
    conditions: Conditions,
    judged: list[JudgedCandidate],
    replan_count: int,
    tried: list[str],
) -> str:
    """상한 전에 next_strategy 가 None 을 돌려줬을 때 왜 멈췄는지 (ChatResponse.stop_reason).

    - target_unobservable: 모든 후보가 고도 기준 이상으로 뜨지 않거나 판단 불가(하나 이상은 never_rises) —
      날짜를 ±2일·인접 지역으로 바꿔도 같은 결과
    - fixed_constraints: 한 번도 재계획하지 못했고, 사용자가 고정한 조건만 풀면 적용할 수 있는 전략이 있었음
    - no_alternative: 그 밖의 전략 소진
    """
    if judged and any(_never_rises(j) for j in judged) and all(
        _never_rises(j) or j.verdict == Verdict.UNJUDGED for j in judged
    ):
        return "target_unobservable"
    if replan_count == 0:
        free = conditions.model_copy(update={"fixed": []})
        for name in STRATEGY_ORDER:
            if name in tried:
                continue
            build = _BUILDERS[name]
            if build(conditions) is None and build(free) is not None:
                return "fixed_constraints"
    return "no_alternative"
