"""심화 F3023 get_calendar / add_plan_to_calendar — 일정 조회 · 계획 등록.

입출력은 contracts 의 Calendar* 모델로 고정하고, 구현체(local|google)는 get_provider() 가 고른다.
등록 Tool 은 plan_id 만 받는다. 제목·시각은 저장소(plans)에서 읽어 모델이 값을 지어내지 못하게 한다.
conflicts_for() 는 제약 적합성 판단(agent)에서 부르는 일반 함수다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from astrowatch.clients import http
from astrowatch.clients.calendar_provider import CalendarProvider, get_provider
from astrowatch.schemas.plan import PlanOut
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import (
    CalendarAddInput,
    CalendarAddOutput,
    CalendarEvent,
    CalendarQueryInput,
    CalendarQueryOutput,
)
from astrowatch.tools.registry import registry

logger = logging.getLogger("astrowatch.calendar")

MAX_RANGE_DAYS = 92


def _provider_fail(e: Exception, provider: str) -> ToolResult[object]:
    """공급자 예외 → 봉투. 메시지에 토큰·URL 은 넣지 않는다."""
    if isinstance(e, http.AuthError):
        if provider == "google":
            return fail(ErrorCode.AUTH, "Google 캘린더 인증 실패 (토큰 없음 또는 만료 — scripts/google_auth.py 실행 필요)")
        return fail(ErrorCode.AUTH, "캘린더 인증 실패")
    if isinstance(e, http.UpstreamTimeout):
        return fail(ErrorCode.TIMEOUT, "캘린더 응답 시간 초과")
    return fail(ErrorCode.UPSTREAM, "캘린더 오류 응답")


_PROVIDER_ERRORS = (http.AuthError, http.UpstreamTimeout, http.UpstreamError)


def build_title(plan: PlanOut) -> str:
    return f"[Astrowatch] {plan.event_title} 관측 — {plan.location_label}"


def build_description(plan: PlanOut) -> str:
    lines = [f"Astrowatch 관측 계획 #{plan.id}"]
    if plan.basis:
        lines.append(f"근거: {plan.basis}")
    if plan.place_url:
        lines.append(f"장소: {plan.place_url}")
    return "\n".join(lines)


@registry.tool(
    name="get_calendar",
    description=(
        "사용자의 기존 일정과 겹치는지 확인할 때 쓴다. 날짜 범위(date_from~date_to, 포함)의 일정 목록을 돌려준다. "
        "일정이 없으면 빈 목록이다."
    ),
    kind=ToolKind.LOCAL,
    input_model=CalendarQueryInput,
    output_model=CalendarQueryOutput,
)
def get_calendar(args: CalendarQueryInput) -> ToolResult[CalendarQueryOutput]:
    if args.date_to < args.date_from:
        return fail(ErrorCode.BAD_INPUT, "date_to 는 date_from 이후여야 한다")
    if (args.date_to - args.date_from).days > MAX_RANGE_DAYS:
        return fail(ErrorCode.BAD_INPUT, f"조회 범위는 최대 {MAX_RANGE_DAYS}일")
    provider = get_provider()
    try:
        events = provider.list_events(args.date_from, args.date_to)
    except _PROVIDER_ERRORS as e:
        logger.warning("캘린더 조회 실패(%s): %s", provider.name, type(e).__name__)
        return _provider_fail(e, provider.name)
    return ok(CalendarQueryOutput(provider=provider.name, date_from=args.date_from, date_to=args.date_to, events=events))


@registry.tool(
    name="add_plan_to_calendar",
    description=(
        "저장된 관측 계획을 캘린더에 등록할 때. 계획 번호만 받는다(plan_id). "
        "제목·시각은 저장된 계획에서 읽는다. 이미 등록된 계획은 다시 등록하지 않는다."
    ),
    kind=ToolKind.LOCAL,
    input_model=CalendarAddInput,
    output_model=CalendarAddOutput,
)
def add_plan_to_calendar(args: CalendarAddInput) -> ToolResult[CalendarAddOutput]:
    from astrowatch.storage import plans as plan_store

    plan = plan_store.get_plan(args.plan_id)
    if plan is None:
        return fail(ErrorCode.NOT_FOUND, f"계획 #{args.plan_id} 을 찾을 수 없다")

    provider = get_provider()
    title = build_title(plan)
    if plan.calendar_event_id:  # 중복 등록 금지
        return ok(
            CalendarAddOutput(
                provider=provider.name,
                plan_id=plan.id,
                event_id=plan.calendar_event_id,
                title=title,
                start_dt=plan.start_dt,
                end_dt=plan.end_dt,
                html_link=None,
                already_registered=True,
            ),
            note="이미 캘린더에 등록된 계획이라 새로 만들지 않았다",
        )

    try:
        event = provider.add_event(title, plan.start_dt, plan.end_dt, build_description(plan), plan.id)
    except _PROVIDER_ERRORS as e:
        logger.warning("캘린더 등록 실패(%s, plan=%s): %s", provider.name, plan.id, type(e).__name__)
        return _provider_fail(e, provider.name)

    plan_store.set_calendar_event_id(plan.id, event.event_id)
    return ok(
        CalendarAddOutput(
            provider=provider.name,
            plan_id=plan.id,
            event_id=event.event_id,
            title=event.title,
            start_dt=event.start_dt,
            end_dt=event.end_dt,
            html_link=event.html_link,
            already_registered=False,
        )
    )


def conflicts_for(
    start_dt: datetime,
    end_dt: datetime,
    *,
    exclude_plan_id: int | None = None,
    provider: CalendarProvider | None = None,
) -> list[CalendarEvent]:
    """[start_dt, end_dt) 와 겹치는 기존 일정 (판단 단계용). 조회 실패 시 빈 리스트 + 로그.

    exclude_plan_id: 그 계획에서 등록한 일정은 겹침으로 치지 않는다.
    """
    if end_dt <= start_dt:
        return []
    prov = provider or get_provider()
    try:
        events = prov.list_events(start_dt.date(), end_dt.date())
    except Exception as e:  # noqa: BLE001 — 판단을 막지 않는다
        logger.warning("캘린더 겹침 조회 실패(%s): %s", getattr(prov, "name", "?"), type(e).__name__)
        return []
    return [
        ev
        for ev in events
        if ev.start_dt < end_dt and ev.end_dt > start_dt
        and (exclude_plan_id is None or ev.plan_id != exclude_plan_id)
    ]
