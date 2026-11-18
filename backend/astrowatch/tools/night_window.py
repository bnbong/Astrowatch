"""F3003 get_night_window — 그날 저녁~다음 날 새벽의 어두운 창과 달 정보.

어두운 창 = [오늘 aste, 다음 날 astm]. 그래서 출몰 API 를 두 날짜로 부른다.
"""

from __future__ import annotations

import datetime as dt
import re

from astrowatch.clients import http, kasi
from astrowatch.config import get_settings
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import NightWindowInput, NightWindowOutput
from astrowatch.tools.registry import registry

_HHMM = re.compile(r"^(\d{2})(\d{2})")


def _upstream_fail(e: Exception) -> ToolResult:
    if isinstance(e, http.AuthError):
        return fail(ErrorCode.AUTH, "천문연 API 인증 실패 (서비스키 확인)")
    if isinstance(e, http.UpstreamTimeout):
        return fail(ErrorCode.TIMEOUT, "천문연 API 응답 시간 초과")
    return fail(ErrorCode.UPSTREAM, "천문연 API 오류 응답")


def hhmm_minutes(value: str | None) -> int | None:
    """'1848  ' → 1128(분). 비었거나 '--' 이면 None."""
    m = _HHMM.match((value or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return h * 60 + mi


def fmt(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


@registry.tool(
    name="get_night_window",
    description=(
        "특정 날짜·위치에서 밤하늘이 완전히 어두운 시간(천문박명 종료~다음 날 새벽 박명 시작)과 "
        "월출·월몰·월령(달빛 영향)을 알아야 할 때 쓴다. date 는 관측하는 저녁 날짜."
    ),
    kind=ToolKind.EXTERNAL,
    input_model=NightWindowInput,
    output_model=NightWindowOutput,
)
def get_night_window(args: NightWindowInput) -> ToolResult[NightWindowOutput]:
    if not get_settings().data_go_kr_key:
        return fail(ErrorCode.AUTH, "공공데이터포털 서비스키가 설정되지 않았다")
    next_day = args.date + dt.timedelta(days=1)
    try:
        today = kasi.get_rise_set(args.date, args.lat, args.lon)
        tomorrow = kasi.get_rise_set(next_day, args.lat, args.lon)
    except (http.AuthError, http.UpstreamTimeout, http.UpstreamError) as e:
        return _upstream_fail(e)

    if not today or not tomorrow:
        return fail(ErrorCode.NO_WINDOW, f"{args.date} 출몰 정보가 비어 관측 창을 계산할 수 없다")
    sunset = hhmm_minutes(today.get("sunset"))
    aste = hhmm_minutes(today.get("aste"))
    astm = hhmm_minutes(tomorrow.get("astm"))
    sunrise_next = hhmm_minutes(tomorrow.get("sunrise"))
    if aste is None or astm is None or sunset is None or sunrise_next is None:
        return fail(ErrorCode.NO_WINDOW, f"{args.date} 천문박명 값이 비어 관측 창을 계산할 수 없다")

    dark_minutes = 24 * 60 + astm - aste
    if dark_minutes <= 0:
        return fail(ErrorCode.NO_WINDOW, f"{args.date} 완전히 어두운 시간이 없다")

    note = None
    try:
        lun_age = kasi.get_lun_age(args.date)
    except (http.AuthError, http.UpstreamTimeout, http.UpstreamError):
        lun_age = None
    if lun_age is None:
        note = "월령 조회 실패 — 달빛 영향은 월출·월몰 시각으로만 판단한다"

    data = NightWindowOutput(
        date=args.date,
        sunset=fmt(sunset),
        astro_dusk_end=fmt(aste),
        astro_dawn_start=fmt(astm),
        sunrise_next=fmt(sunrise_next),
        moonrise=fmt(hhmm_minutes(today.get("moonrise"))),
        moonset=fmt(hhmm_minutes(today.get("moonset"))),
        moonrise_next=fmt(hhmm_minutes(tomorrow.get("moonrise"))),
        moonset_next=fmt(hhmm_minutes(tomorrow.get("moonset"))),
        lun_age=lun_age,
        dark_minutes=dark_minutes,
    )
    return ok(data, note=note)
