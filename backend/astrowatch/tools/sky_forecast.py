"""F3004 get_sky_forecast — 관측 시간대의 하늘 상태(구름·강수확률).

공급자 선택: mock 설정 → mock. 아니면 날짜 거리 D(오늘 기준)로
    kma_short (D≤3, 시간별) → owm (D≤5, 3시간) → kma_mid (D 4~10, 하루 1값) 순서로 첫 가능한 것.
요청 시각 24~29 는 다음 날 0~5시다. 예보가 없는 시각은 unknown 으로 둔다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from astrowatch.clients import http, kma, owm
from astrowatch.config import get_settings
from astrowatch.data.regions import latlon_to_grid, nearest_region
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import HourlySky, Precip, SkyCondition, SkyForecastInput, SkyForecastOutput
from astrowatch.tools.registry import registry

DEFAULT_HOURS = list(range(18, 30))
SHORT_MAX_DAYS = 3
OWM_MAX_DAYS = 5
MID_MIN_DAYS, MID_MAX_DAYS = 4, 10

_KMA_SKY = {"1": SkyCondition.CLEAR, "3": SkyCondition.PARTLY, "4": SkyCondition.CLOUDY}
# 단기예보 강수형태 PTY (0 없음 · 1 비 · 2 비/눈 · 3 눈 · 4 소나기)
_KMA_PTY = {"0": Precip.NONE, "1": Precip.RAIN, "2": Precip.SLEET, "3": Precip.SNOW, "4": Precip.SHOWER}
# OWM weather[0].main
_OWM_MAIN = {"Rain": Precip.RAIN, "Drizzle": Precip.RAIN, "Snow": Precip.SNOW, "Thunderstorm": Precip.SHOWER}

# 시각 라벨 → (sky, pop, cloud_pct, precip)
Slots = dict[int, tuple[SkyCondition, int | None, int | None, Precip | None]]


def now_local() -> dt.datetime:
    """설정 시간대의 현재 시각(naive). 테스트에서 monkeypatch 한다."""
    try:
        tz = ZoneInfo(get_settings().timezone)
    except Exception:  # tzdata 없음 등
        tz = dt.timezone(dt.timedelta(hours=9))
    return dt.datetime.now(tz).replace(tzinfo=None)


def target_times(date: dt.date, hours: list[int]) -> list[tuple[int, dt.datetime]]:
    """시각 라벨(0~29) → 현지 일시. 24 이상은 다음 날."""
    return [(h, dt.datetime.combine(date + dt.timedelta(days=h // 24), dt.time(h % 24))) for h in hours]


def mid_sky(text: str | None) -> SkyCondition:
    """중기예보 문구 → 하늘 상태. 비·눈이 섞이면 흐림으로 본다."""
    t = (text or "").replace(" ", "")
    if not t:
        return SkyCondition.UNKNOWN
    if any(w in t for w in ("비", "눈", "소나기", "흐")):
        return SkyCondition.CLOUDY
    if "구름많" in t:
        return SkyCondition.PARTLY
    if "맑" in t:
        return SkyCondition.CLEAR
    return SkyCondition.UNKNOWN


def cloud_to_sky(pct: int) -> SkyCondition:
    if pct <= 30:
        return SkyCondition.CLEAR
    if pct <= 70:
        return SkyCondition.PARTLY
    return SkyCondition.CLOUDY


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ── 공급자별 변환 ──────────────────────────────────────────────

def short_slots(items: list[dict[str, Any]], targets: list[tuple[int, dt.datetime]]) -> Slots:
    """단기예보 item → 시각별. 정시 값이 없으면 직전 2시간 안의 값(3시간 간격 구간)을 쓴다."""
    table: dict[dt.datetime, dict[str, str]] = {}
    for it in items:
        if it.get("category") not in ("SKY", "POP", "PTY"):
            continue
        try:
            when = dt.datetime.strptime(f"{it['fcstDate']}{it['fcstTime']}", "%Y%m%d%H%M")
        except (KeyError, ValueError):
            continue
        table.setdefault(when, {})[it["category"]] = str(it.get("fcstValue", ""))
    out: Slots = {}
    for label, when in targets:
        for back in range(3):
            row = table.get(when - dt.timedelta(hours=back))
            if row and row.get("SKY") in _KMA_SKY:
                out[label] = (_KMA_SKY[row["SKY"]], _clamp(_int(row.get("POP"))), None, _KMA_PTY.get(row.get("PTY", "")))
                break
    return out


def owm_slots(payload: dict[str, Any], targets: list[tuple[int, dt.datetime]]) -> Slots:
    """OWM 3시간 슬롯 → 시각별. 슬롯 시작 ≤ 시각 < 시작+3h 이면 그 슬롯 값."""
    offset = dt.timedelta(seconds=_int((payload.get("city") or {}).get("timezone")) or 9 * 3600)
    slots: list[tuple[dt.datetime, dict[str, Any]]] = []
    for s in payload.get("list") or []:
        ts = _int(s.get("dt"))
        if ts is None:
            continue
        start = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=ts) + offset
        slots.append((start, s))
    out: Slots = {}
    for label, when in targets:
        for start, s in slots:
            if start <= when < start + dt.timedelta(hours=3):
                pct = _clamp(_int((s.get("clouds") or {}).get("all")))
                pop = s.get("pop")
                pop_pct = _clamp(round(float(pop) * 100)) if isinstance(pop, (int, float)) else None
                main = ((s.get("weather") or [{}])[0] or {}).get("main")
                precip = _OWM_MAIN.get(main, Precip.NONE) if main else None
                out[label] = (cloud_to_sky(pct) if pct is not None else SkyCondition.UNKNOWN, pop_pct, pct, precip)
                break
    return out


def _clamp(v: int | None) -> int | None:
    return None if v is None else max(0, min(100, v))


# ── Tool ──────────────────────────────────────────────────────

def _upstream_fail(e: Exception, name: str) -> ToolResult:
    if isinstance(e, http.AuthError):
        return fail(ErrorCode.AUTH, f"{name} 인증 실패 (키 확인)")
    if isinstance(e, http.UpstreamTimeout):
        return fail(ErrorCode.TIMEOUT, f"{name} 응답 시간 초과")
    return fail(ErrorCode.UPSTREAM, f"{name} 오류 응답")


def _build(
    args: SkyForecastInput,
    hours: list[int],
    slots: Slots,
    source: str,
    *,
    base_time: str | None = None,
    grid: tuple[int, int] | None = None,
    note: str | None = None,
) -> ToolResult[SkyForecastOutput]:
    hourly = []
    for h in hours:
        sky, pop, pct, precip = slots.get(h, (SkyCondition.UNKNOWN, None, None, None))
        hourly.append(HourlySky(hour=h, sky=sky, pop=pop, cloud_pct=pct, precip=precip))
    known = sum(1 for x in hourly if x.sky is not SkyCondition.UNKNOWN)
    missing = [x.hour for x in hourly if x.sky is SkyCondition.UNKNOWN]
    if known == len(hourly):
        coverage = "full"
    elif known == 0:
        coverage = "none"
        note = _join(note, "요청 시간 전체 예보 없음")
    else:
        coverage = "partial"
        note = _join(note, f"예보 없음: {', '.join(str(h) for h in missing)}시 (unknown 으로 표시)")
    data = SkyForecastOutput(
        date=args.date, source=source, base_time=base_time, grid=grid, hourly=hourly, coverage=coverage
    )
    return ok(data, note=note)


def _join(a: str | None, b: str) -> str:
    return f"{a} / {b}" if a else b


@registry.tool(
    name="get_sky_forecast",
    description=(
        "관측 후보 날짜의 시간대별 하늘 상태(맑음·구름많음·흐림)·강수확률·강수형태(비·눈)가 필요할 때 쓴다. "
        "hours 는 관측 창의 시각(24~29 는 다음 날 0~5시). 약 10일 뒤까지만 예보가 있다. 날씨는 지역 격자 기준이다."
    ),
    kind=ToolKind.EXTERNAL,
    input_model=SkyForecastInput,
    output_model=SkyForecastOutput,
)
def get_sky_forecast(args: SkyForecastInput) -> ToolResult[SkyForecastOutput]:
    hours = list(args.hours) or DEFAULT_HOURS
    if any(h < 0 or h > 29 for h in hours):
        return fail(ErrorCode.BAD_INPUT, "hours 는 0~29 범위여야 한다")
    targets = target_times(args.date, hours)
    settings = get_settings()

    if settings.weather_provider == "mock":
        slots: Slots = {h: (SkyCondition.CLOUDY, 80, None, Precip.NONE) for h in hours}
        return _build(args, hours, slots, "mock", note="mock 공급자 — 실제 예보가 아니다")

    now = now_local()
    d = (args.date - now.date()).days
    if d < 0:
        return fail(ErrorCode.OUT_OF_RANGE, "지난 날짜는 예보가 없다")
    has_kma, has_owm = bool(settings.data_go_kr_key), bool(settings.openweather_key)

    # 범위 안의 공급자를 순서대로 시도하고, 요청 시각을 하나라도 덮는 첫 결과를 쓴다.
    # (OWM 은 '지금부터 5일'이라 D+5 저녁은 비어 있을 수 있다 → 중기예보로 넘어간다)
    fallback: ToolResult[SkyForecastOutput] | None = None
    try:
        if d <= SHORT_MAX_DAYS and has_kma:
            base_date, base_time = kma.vilage_base(now)
            nx, ny = latlon_to_grid(args.lat, args.lon)
            items = kma.get_vilage_fcst(nx, ny, base_date, base_time)
            fallback = _build(
                args, hours, short_slots(items, targets), "kma_short",
                base_time=f"{base_date} {base_time}", grid=(nx, ny),
            )
            if fallback.data and fallback.data.coverage != "none":
                return fallback
        if d <= OWM_MAX_DAYS and has_owm:
            payload = owm.get_forecast(args.lat, args.lon)
            result = _build(args, hours, owm_slots(payload, targets), "owm", note="OpenWeatherMap 3시간 간격 예보")
            if result.data and result.data.coverage != "none":
                return result
            fallback = fallback or result
        if MID_MIN_DAYS <= d <= MID_MAX_DAYS and has_kma:
            result = _mid(args, hours, now)
            if result.data and result.data.coverage != "none":
                return result
            fallback = fallback or result
    except (http.AuthError, http.UpstreamTimeout, http.UpstreamError) as e:
        return _upstream_fail(e, "기상 예보 API")
    if fallback is not None:
        return fallback

    if not has_kma and not has_owm:
        return fail(ErrorCode.AUTH, "기상 예보 API 키가 설정되지 않았다")
    if d <= MID_MAX_DAYS and not has_kma:
        return fail(ErrorCode.AUTH, "이 날짜의 예보에는 공공데이터포털 서비스키가 필요하다")
    latest = now.date() + dt.timedelta(days=MID_MAX_DAYS if has_kma else OWM_MAX_DAYS)
    return fail(ErrorCode.OUT_OF_RANGE, f"예보 범위 밖: 가장 늦은 예보일 {latest.isoformat()}")


def _mid(args: SkyForecastInput, hours: list[int], now: dt.datetime) -> ToolResult[SkyForecastOutput]:
    """중기예보: 요청 시각마다 그 시각이 속한 날의 오전/오후 값(8~10일째는 하루 값)을 쓴다.

    자정 넘김(24~29시)은 다음 날 오전 값. 예보 필드가 없는 날은 unknown 으로 둔다 — 값을 지어내지 않는다.
    """
    region = nearest_region(args.lat, args.lon)
    tm = kma.mid_tmfc(now)
    item = kma.get_mid_land_fcst(region.mid_land_reg_id, tm.strftime("%Y%m%d%H%M")) or {}
    slots: Slots = {}
    used: list[str] = []
    for h in hours:
        day = args.date + dt.timedelta(days=h // 24)
        n = (day - tm.date()).days
        suffix = "" if n >= 8 else ("Am" if (h % 24) < 12 else "Pm")
        wf = item.get(f"wf{n}{suffix}")
        sky = mid_sky(wf)
        if sky is SkyCondition.UNKNOWN:
            continue
        slots[h] = (sky, _clamp(_int(item.get(f"rnSt{n}{suffix}"))), None, None)   # 중기: 강수형태 없음
        label = f"{day.isoformat()} {'오전' if suffix == 'Am' else '오후' if suffix == 'Pm' else '하루'} '{wf}'"
        if label not in used:
            used.append(label)
    note = (
        f"중기예보({region.province or region.name} 구역) " + ", ".join(used) + " 값을 해당 시간대 전체에 적용 — 시간별 예보 아님"
        if used else None
    )
    return _build(args, hours, slots, "kma_mid", base_time=tm.strftime("%Y%m%d %H%M"), note=note)
