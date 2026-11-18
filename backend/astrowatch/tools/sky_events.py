"""F3002 search_sky_events — 천문연 천문현상 월별 목록."""

from __future__ import annotations

import datetime as dt
import re

from astrowatch.clients import http, kasi
from astrowatch.config import get_settings
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import SkyEvent, SkyEventsInput, SkyEventsOutput
from astrowatch.tools.event_kind import classify
from astrowatch.tools.registry import registry

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def _upstream_fail(e: Exception) -> ToolResult:
    if isinstance(e, http.AuthError):
        return fail(ErrorCode.AUTH, "천문연 API 인증 실패 (서비스키 확인)")
    if isinstance(e, http.UpstreamTimeout):
        return fail(ErrorCode.TIMEOUT, "천문연 API 응답 시간 초과")
    return fail(ErrorCode.UPSTREAM, "천문연 API 오류 응답")


def parse_events(items: list[dict[str, str]]) -> list[SkyEvent]:
    """KASI item → SkyEvent. locdate 8자리가 아닌 월 요약 행은 버린다."""
    events: list[SkyEvent] = []
    for it in items:
        locdate = (it.get("locdate") or "").strip()
        if len(locdate) != 8 or not locdate.isdigit():
            continue
        try:
            date = dt.datetime.strptime(locdate, "%Y%m%d").date()
        except ValueError:
            continue
        title = it.get("astroTitle") or it.get("astroEvent") or ""
        if not title:
            continue
        m = _TIME.match(it.get("astroTime") or "")
        time = f"{int(m.group(1)):02d}:{m.group(2)}" if m else None
        seq = (it.get("seq") or "").strip() or str(len(events) + 1)
        desc = it.get("astroEvent") if it.get("astroTitle") and it.get("astroEvent") != title else None
        events.append(
            SkyEvent(
                event_id=f"{locdate}-{seq}",
                date=date,
                time=time,
                title=title,
                kind=classify(title),
                description=desc or None,
            )
        )
    events.sort(key=lambda e: (e.date, e.time or "99:99"))
    return events


@registry.tool(
    name="search_sky_events",
    description=(
        "볼 천문현상을 아직 정하지 않았거나, 특정 연·월에 어떤 현상(유성우·행성·달·식 등)이 있는지 찾을 때 쓴다. "
        "kind 로 종류를 거를 수 있다. 결과의 date 를 출몰·예보 Tool 입력으로 넘긴다."
    ),
    kind=ToolKind.EXTERNAL,
    input_model=SkyEventsInput,
    output_model=SkyEventsOutput,
)
def search_sky_events(args: SkyEventsInput) -> ToolResult[SkyEventsOutput]:
    if not get_settings().data_go_kr_key:
        return fail(ErrorCode.AUTH, "공공데이터포털 서비스키가 설정되지 않았다")
    try:
        items = kasi.get_astro_events(args.year, args.month)
    except (http.AuthError, http.UpstreamTimeout, http.UpstreamError) as e:
        return _upstream_fail(e)

    events = parse_events(items)
    if args.kind is not None:
        events = [e for e in events if e.kind == args.kind]
    if not events:
        kind = f" ({args.kind.value})" if args.kind else ""
        return fail(ErrorCode.NO_RESULT, f"{args.year}년 {args.month}월 천문현상{kind} 없음")

    # 계약상 year·month 는 필수라 핸들러는 '비워서 기본값을 썼는지' 알 수 없다.
    # 기본값을 채운 실행 흐름이 결과의 requested_defaulted 를 true 로 덮어쓴다.
    defaulted = False
    return ok(SkyEventsOutput(year=args.year, month=args.month, requested_defaulted=defaulted, events=events))
