"""관측 창 계산 · 판정 (F3008). 순수 함수 — LLM·네트워크 없음.

시각은 '저녁 기준 날짜 00:00 부터의 분'. 새벽 값(astm, sunrise_next)은 +1440.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from astrowatch.schemas.domain import Candidate, Conditions, HourSky, JudgedCandidate, Verdict, Window
from astrowatch.tools.base import ErrorCode, ToolResult
from astrowatch.tools.contracts import EventKind, NightWindowOutput, Precip, SkyCondition, SkyForecastOutput

MAX_MINUTE = 2159          # Window 상한 (다음 날 11:59)
NOON = 720                 # 이보다 이른 시각은 '다음 날 새벽'으로 본다

# 현상 종류별 현상 창 반폭(분). 없으면 어두운 창 전체
EVENT_HALF_WIDTH: dict[EventKind, int] = {
    EventKind.METEOR: 360,
    EventKind.ECLIPSE: 120,
}

# 강수형태 — 이 값이면 그 시각은 맑음으로 세지 않는다
WET = {Precip.RAIN, Precip.SNOW, Precip.SLEET, Precip.SHOWER}
PRECIP_KO = {Precip.RAIN: "비", Precip.SNOW: "눈", Precip.SLEET: "비/눈", Precip.SHOWER: "소나기"}
NEVER_RISES = "그날 밤 지평선 위로"   # 사유 문구 접두 (재계획 판단에도 쓴다)
# 지목한 대상을 확인하지 못함 (R1) — 사유 문구에 들어가고, 재계획 생략 판단에도 쓴다
TARGET_NOT_FOUND = "천문현상 목록과 천체 카탈로그에서 찾지 못해 판단하지 않음"
TARGET_TEMP_FAIL = "천체 정보를 일시 오류로 확인 못 함"

ObjectKey = tuple[str, dt.date]

_SKY_RANK = {SkyCondition.UNKNOWN: -1, SkyCondition.CLEAR: 0, SkyCondition.PARTLY: 1, SkyCondition.CLOUDY: 2}


# ── 시각 변환 ─────────────────────────────────────────────────

def hhmm_to_min(text: str | None) -> int | None:
    """'HH:MM' 또는 'HHMM' → 분. 형식이 틀리면 None."""
    if not text:
        return None
    t = text.strip().replace(":", "")
    if len(t) < 3 or not t.isdigit():
        return None
    h, m = int(t[:-2]), int(t[-2:])
    if h > 47 or m > 59:
        return None
    return h * 60 + m


def _clamp(v: int) -> int:
    return max(0, min(MAX_MINUTE, v))


def _win(start: int, end: int) -> Window:
    s, e = _clamp(start), _clamp(end)
    return Window(start=s, end=max(s, e))


def label(minute: int | None) -> str:
    return "-" if minute is None else Window.label(minute)


def dark_window(night: NightWindowOutput) -> Window | None:
    aste = hhmm_to_min(night.astro_dusk_end)
    astm = hhmm_to_min(night.astro_dawn_start)
    if aste is None or astm is None:
        return None
    if astm < NOON:
        astm += 1440
    return _win(aste, astm)


def user_window(conditions: Conditions, dark: Window) -> Window:
    start = conditions.start_minute
    end = conditions.end_minute
    if start is not None and start < NOON:
        start += 1440                         # '새벽 1시부터' 같은 경우
    s = dark.start if start is None else start
    e = dark.end if end is None else end
    if e < s:
        e += 1440
    return _win(s, e)


def event_window(candidate: Candidate, dark: Window) -> Window:
    half = EVENT_HALF_WIDTH.get(candidate.kind)
    if half is None or candidate.event_minute is None:
        return dark
    return _win(candidate.event_minute - half, candidate.event_minute + half)


def _day_moon(rise: str | None, set_: str | None, offset: int) -> tuple[list[tuple[int, int]], bool]:
    """하루치 [offset, offset+1440) 의 달 떠 있는 구간. 월출·월몰 둘 다 없으면 (빈 목록, 확인 불가)."""
    r, s = hhmm_to_min(rise), hhmm_to_min(set_)
    if r is None and s is None:
        return [], True
    if r is not None and s is not None:
        spans = [(r, s)] if r < s else [(0, s), (r, 1440)]
    elif r is not None:
        spans = [(r, 1440)]     # 그날 지지 않음
    else:
        assert s is not None
        spans = [(0, s)]        # 그날 뜨지 않음
    return [(a + offset, b + offset) for a, b in spans], False


def moon_up_intervals(night: NightWindowOutput) -> tuple[list[Window], list[Window]]:
    """(달이 떠 있는 구간, 확인 불가 구간). 자정 이후는 다음 날 월출·월몰(moonrise_next/moonset_next)로 계산."""
    up: list[Window] = []
    unknown: list[Window] = []
    days = (
        (night.moonrise, night.moonset, 0),
        (getattr(night, "moonrise_next", None), getattr(night, "moonset_next", None), 1440),
    )
    for rise, set_, offset in days:
        spans, unk = _day_moon(rise, set_, offset)
        if unk:
            unknown.append(_win(offset, offset + 1440))
            continue
        up.extend(_win(a, b) for a, b in spans if _clamp(a) < _clamp(b))
    return up, unknown


def _moon_free_segments(tw: Window, ups: list[Window]) -> list[Window]:
    """관측 창에서 달이 떠 있는 구간(확인된 것만)을 뺀 나머지 구간들. 시간 순."""
    segs = [(tw.start, tw.end)]
    for u in ups:
        nxt = []
        for a, b in segs:
            if u.end <= a or u.start >= b:
                nxt.append((a, b))
                continue
            if a < u.start:
                nxt.append((a, u.start))
            if u.end < b:
                nxt.append((u.end, b))
        segs = nxt
    return [_win(a, b) for a, b in segs if b > a]


def _clear_in(forecast: ToolResult[Any] | None, w: Window, allow_partly: bool) -> int | None:
    """구간 w 의 맑은 시간(분). 쓸 수 있는 예보가 없으면 None."""
    if forecast is None or not forecast.ok or forecast.data is None:
        return None
    fc = forecast.data
    if not isinstance(fc, SkyForecastOutput) or fc.coverage == "none":
        return None
    return sum(b - a for a, b, c in _slots(fc, w, allow_partly) if c == CLEAR)


def _pick_moon_free(segs: list[Window], forecast: ToolResult[Any] | None, allow_partly: bool,
                    min_obs: int) -> Window | None:
    """달 없는 구간 중 최소 관측 시간을 넘는 것을 모두 평가: 맑은 시간 긴 순 → 긴 구간 → 이른 구간.
    예보가 없으면 가장 긴 구간."""
    ok_segs = [s for s in segs if s.minutes >= min_obs]
    if not ok_segs:
        return None
    return max(ok_segs, key=lambda s: (_clear_in(forecast, s, allow_partly) or 0, s.minutes, -s.start))


def _overlaps(w: Window, spans: list[Window]) -> bool:
    return any(w.intersect(u).minutes > 0 for u in spans)


def _unknown_label(w: Window, unknowns: list[Window]) -> str:
    hit = next((u for u in unknowns if w.intersect(u).minutes > 0), None)
    return "자정 이후" if hit is not None and hit.start >= 1440 else "일부 시간대"


# ── 예보 ─────────────────────────────────────────────────────

CLEAR, BAD, UNKNOWN = "clear", "bad", "unknown"


def _slots(fc: SkyForecastOutput, tw: Window, allow_partly: bool) -> list[tuple[int, int, str]]:
    """관측 창을 정시 단위로 잘라 (시작, 끝, 분류). 예보가 없거나 강수확률을 모르면 unknown."""
    good = {SkyCondition.CLEAR} | ({SkyCondition.PARTLY} if allow_partly else set())
    by_hour = {h.hour: h for h in fc.hourly}
    out: list[tuple[int, int, str]] = []
    t = tw.start
    while t < tw.end:
        hour = t // 60
        b = min(tw.end, hour * 60 + 60)
        h = by_hour.get(hour)
        if h is None or h.sky == SkyCondition.UNKNOWN:
            cls = UNKNOWN
        elif h.sky not in good or (h.pop is not None and h.pop > 30) or h.precip in WET:
            cls = BAD           # 강수 예보 시각은 SKY 와 무관하게 맑음으로 세지 않는다
        elif h.pop is None:
            cls = UNKNOWN       # 맑음이어도 강수확률 30% 이하를 확인 못 함
        else:
            cls = CLEAR
        out.append((t, b, cls))
        t = b
    return out


def precip_spans(fc: SkyForecastOutput, w: Window) -> list[str]:
    """창 안 강수 예보를 연속 구간으로 요약: ['눈 예보 21:00~03:00+1', ...]."""
    out: list[str] = []
    cur: tuple[Precip, int, int] | None = None
    for h in sorted(fc.hourly, key=lambda x: x.hour):
        a, b = h.hour * 60, h.hour * 60 + 60
        if b <= w.start or a >= w.end or h.precip not in WET:
            continue
        assert h.precip is not None
        if cur and cur[0] == h.precip and cur[2] == a:
            cur = (cur[0], cur[1], b)
            continue
        if cur:
            out.append(f"{PRECIP_KO[cur[0]]} 예보 {label(cur[1])}~{label(cur[2])}")
        cur = (h.precip, a, b)
    if cur:
        out.append(f"{PRECIP_KO[cur[0]]} 예보 {label(cur[1])}~{label(cur[2])}")
    return out


def _span_label(w: Window) -> str:
    if w.start <= NOON and w.end >= MAX_MINUTE:
        return "밤새"
    return f"{label(w.start)}~{label(w.end)}"


def _sky_extremes(fc: SkyForecastOutput, tw: Window) -> tuple[SkyCondition | None, int | None]:
    worst: SkyCondition | None = None
    max_pop: int | None = None
    for h in fc.hourly:
        if min(tw.end, h.hour * 60 + 60) <= max(tw.start, h.hour * 60) or h.sky == SkyCondition.UNKNOWN:
            continue
        if worst is None or _SKY_RANK[h.sky] > _SKY_RANK[worst]:
            worst = h.sky
        if h.pop is not None:
            max_pop = h.pop if max_pop is None else max(max_pop, h.pop)
    return worst, max_pop


def _latest_arrival(slots: list[tuple[int, int, str]], allowed: set[str], min_obs: int) -> int | None:
    """뒤에서부터 allowed 분류의 분을 모아 min_obs 를 채우는 가장 늦은 도착 시각."""
    need = min_obs
    for a, b, cls in reversed(slots):
        if cls not in allowed:
            continue
        if b - a >= need:
            return b - need
        need -= b - a
    return None


def has_batchim(word: str) -> bool | None:
    """마지막 글자의 받침 유무. 한글·숫자만 판단하고 나머지는 None."""
    w = word.strip().rstrip("'\"")
    if not w:
        return None
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"      # 영·일·삼·육·칠·팔
    return None


def josa(word: str, with_b: str, without_b: str) -> str:
    b = has_batchim(word)
    return f"{with_b}({without_b})" if b is None else (with_b if b else without_b)


def _eul(word: str) -> str:
    return josa(word, "을", "를")


CALENDAR_CONFLICT = "기존 일정"   # 사유 문구 접두 (응답에서 겹침 언급 여부 판단에도 쓴다)


def calendar_windows(day: dt.date, events: list[Any], tw: Window) -> list[tuple[str, Window, str]]:
    """일정(서울 naive start_dt/end_dt) → 저녁 기준 날짜 day 의 분 구간. 관측 창 tw 와 겹치는 것만
    (제목, 구간, 'HH:MM~HH:MM')."""
    base = dt.datetime.combine(day, dt.time())
    out: list[tuple[str, Window, str]] = []
    for ev in events:
        start, end = getattr(ev, "start_dt", None), getattr(ev, "end_dt", None)
        if start is None or end is None or end <= start:
            continue
        a = int((start.replace(tzinfo=None) - base).total_seconds() // 60)
        b = int(-(-(end.replace(tzinfo=None) - base).total_seconds() // 60))
        if b <= 0 or a > MAX_MINUTE:
            continue
        w = _win(a, b)
        if w.intersect(tw).minutes <= 0:
            continue
        out.append((str(getattr(ev, "title", "") or "일정"), w, f"{start:%H:%M}~{end:%H:%M}"))
    return out


def hourly_sky_in(forecast: ToolResult[Any] | None, w: Window) -> list[HourSky]:
    """예보(ok)의 시간별 값 중 구간 w 에 걸치는 시각만 그대로 옮긴다 (밤 타임바용). 예보가 없으면 []."""
    if forecast is None or not forecast.ok or not isinstance(forecast.data, SkyForecastOutput):
        return []
    return [
        HourSky(hour=h.hour, sky=h.sky, pop=h.pop, precip=h.precip.value if h.precip is not None else None)
        for h in sorted(forecast.data.hourly, key=lambda x: x.hour)
        if h.hour * 60 < w.end and h.hour * 60 + 60 > w.start
    ]


# ── 판정 ─────────────────────────────────────────────────────

def judge_one(
    candidate: Candidate,
    conditions: Conditions,
    night: NightWindowOutput | None,
    forecast: ToolResult[Any] | None,
    min_obs: int,
    object_windows: list[Window] | None = None,
    object_meta: dict[str, Any] | None = None,
    calendar_events: list[Any] | None = None,
) -> JudgedCandidate:
    """object_windows: 천체 고도 ≥ min_alt 구간 (None 이면 고도 판단 안 함, [] 이면 그날 안 뜸).
    object_meta: {name, min_alt, transit_minute, transit_alt, transit_az} 또는 {found: False}."""
    j: dict[str, Any] = {"candidate": candidate}
    reasons: list[str] = []

    def done(verdict: Verdict) -> JudgedCandidate:
        return JudgedCandidate(verdict=verdict, reasons=reasons, **j)

    if candidate.kind == EventKind.SEASON:
        reasons.append(f"{candidate.title}: 절기라 관측 대상 아님")
        return done(Verdict.UNJUDGED)

    meta = object_meta or {}
    unresolved = meta.get("unresolved")
    if unresolved:
        # 사용자가 지목한 대상의 좌표를 모른다 — 어두운 창 전체로 '충족'을 만들지 않는다 (R1)
        name = meta.get("query") or candidate.title
        if unresolved == "temporary":
            reasons.append(f"'{name}' {TARGET_TEMP_FAIL} - 판단하지 않음")
        else:
            sims = meta.get("similar") or []
            hint = f" (비슷한 이름: {', '.join(sims)})" if sims else ""
            reasons.append(f"'{name}'{_eul(name)} {TARGET_NOT_FOUND}{hint}")
        return done(Verdict.UNJUDGED)

    if night is None:
        reasons.append("박명·출몰 정보를 확인하지 못해 관측 창을 계산하지 않았습니다")
        return done(Verdict.UNJUDGED)
    # 밤 타임바용 — 월령과 무관하게 달이 떠 있는 구간을 그대로 싣는다
    ups, unknowns = moon_up_intervals(night)
    j.update(moon_up=ups, moon_unknown=unknowns)
    dark = dark_window(night)
    if dark is None:
        reasons.append("천문박명 시각이 비어 있어 관측 창을 계산하지 않았습니다")
        return done(Verdict.UNJUDGED)

    user = user_window(conditions, dark)
    j["hourly_sky"] = hourly_sky_in(forecast, dark.intersect(user))
    evw = event_window(candidate, dark)
    obj_line: str | None = None
    if object_windows is not None:
        name = meta.get("name") or candidate.title
        min_alt = float(meta.get("min_alt", 20))
        if meta.get("transit_alt") is not None:
            j.update(target_alt=meta.get("transit_alt"), target_az=meta.get("transit_az"))
        transit = (f", 남중 {label(meta['transit_minute'])}(고도 {meta['transit_alt']:.0f}°)"
                   if meta.get("transit_minute") is not None and meta.get("transit_alt") is not None else "")
        if not object_windows:
            j.update(dark_window=dark, user_window=user, lun_age=night.lun_age)
            reasons.append(f"{name}: {NEVER_RISES} {min_alt:g}° 이상 오르지 않음{transit}")
            return done(Verdict.UNFIT)
        # 고도 구간이 여러 개면 각각 어두운∩내 창과 겹쳐 보고 가장 긴 결과를 쓴다
        base = dark.intersect(user).intersect(evw)
        src = max(object_windows, key=lambda w: (base.intersect(w).minutes, -w.start))
        evw = evw.intersect(src) if evw.intersect(src).minutes > 0 else src
        j["object_window"] = src
        obj_line = f"{name} 고도 {min_alt:g}° 이상 {_span_label(src)}{transit}"
    tw = dark.intersect(user).intersect(evw)
    j.update(dark_window=dark, user_window=user, event_window=evw, time_window=tw, lun_age=night.lun_age)
    reasons.append(
        f"어두운 시간 {label(dark.start)}~{label(dark.end)} · 내 시간 {label(user.start)}~{label(user.end)}"
        + (f" · 천체 고도 {label(evw.start)}~{label(evw.end)}" if obj_line else "")
        + f" → 관측 창 {label(tw.start)}~{label(tw.end)} ({tw.minutes}분)"
    )
    if obj_line:
        reasons.append(obj_line)
    elif meta.get("found") is False:
        reasons.append("천체 좌표를 몰라 고도는 판단하지 않음")

    if tw.minutes < min_obs:
        reasons.append(f"관측 창 {tw.minutes}분이 최소 {min_obs}분보다 짧음")
        return done(Verdict.UNFIT)

    # 달
    age = night.lun_age
    # 달이 밝다고 보는 월령 범위. 희미한 천체(은하·성운)는 반달 전후(월령 7~23)부터 영향을 받는다
    bright_lo, bright_hi = (7, 23) if candidate.kind == EventKind.DEEP_SKY else (10, 20)
    if age is None:
        j["moon_interferes"] = None
        reasons.append("월령 정보 없음 - 달빛 영향 미확인")
    elif bright_lo <= age <= bright_hi:
        interferes = any(tw.intersect(u).minutes > 0 for u in ups)
        unsure = any(tw.intersect(u).minutes > 0 for u in unknowns)
        if interferes:
            j["moon_interferes"] = True
            if candidate.kind == EventKind.DEEP_SKY and conditions.avoid_moon:
                # 달이 진 뒤/뜨기 전 구간마다 맑은 시간을 따져 가장 나은 구간으로 다시 판정한다
                free = _pick_moon_free(_moon_free_segments(tw, ups), forecast,
                                       conditions.allow_partly_cloudy, min_obs)
                if free is None:
                    reasons.append(f"달빛(월령 {age:g}일) - 희미한 천체 관측에 불리")
                    return done(Verdict.UNFIT)
                basis = []
                if free.start > tw.start:
                    basis.append(f"월몰 {label(free.start)} 이후")
                if free.end < tw.end:
                    basis.append(f"월출 {label(free.end)} 이전")
                tw = free
                j["time_window"] = tw
                reasons.append(f"달빛(월령 {age:g}일)을 피해 관측 창을 {label(tw.start)}~{label(tw.end)}"
                               f" ({tw.minutes}분)로 줄임 - {' · '.join(basis) or '전체'} 기준")
                # 최종 창 기준으로 달 영향을 다시 계산 — 알 수 없는 구간은 '달 없음'으로 단정하지 않는다
                if _overlaps(tw, ups):
                    j["moon_interferes"] = True
                elif _overlaps(tw, unknowns):
                    j["moon_interferes"] = None
                    reasons.append(f"{_unknown_label(tw, unknowns)} 달 정보 없음 - 달빛 영향 확인 불가")
                else:
                    j["moon_interferes"] = False
            elif candidate.kind == EventKind.METEOR and conditions.avoid_moon:
                reasons.append(f"달빛(월령 {age:g}일)이 관측 창에 걸림")
                return done(Verdict.UNFIT)
            else:
                reasons.append(f"달빛(월령 {age:g}일)이 관측 창에 걸림 - 참고")
        elif unsure:
            j["moon_interferes"] = None
            reasons.append(f"월령 {age:g}일, 일부 시간대 월출·월몰 정보 없음 - 달빛 영향 확인 불가")
        else:
            j["moon_interferes"] = False
            reasons.append(f"월령 {age:g}일이지만 관측 창에는 달이 없음")
    else:
        j["moon_interferes"] = False
        reasons.append(f"월령 {age:g}일 - 달빛 영향 적음")

    # 기존 일정 (F3023) — 최종 관측 창(달 회피 후)과 겹치는 일정을 뺀다
    if calendar_events:
        busy = calendar_windows(candidate.date, calendar_events, tw)
        if busy:
            names = ", ".join(f"'{t}'" for t, _w, _l in busy)
            free = _pick_moon_free(_moon_free_segments(tw, [w for _t, w, _l in busy]), forecast,
                                   conditions.allow_partly_cloudy, min_obs)
            if free is None:
                reasons.append(f"{CALENDAR_CONFLICT} {names} 과 겹침")
                return done(Verdict.UNFIT)
            spans = ", ".join(f"'{t}' {lbl}" for t, _w, lbl in busy)
            tw = free
            j["time_window"] = tw
            reasons.append(f"{CALENDAR_CONFLICT} {spans} 과 겹쳐 관측 창을 {label(tw.start)}~{label(tw.end)}"
                           f" ({tw.minutes}분)로 줄임")

    # 예보
    no_weather_arrival = tw.end - min_obs
    if forecast is None:
        j["latest_arrival_minute"] = no_weather_arrival
        reasons.append("예보를 조회하지 않아 날씨 미판단")
        return done(Verdict.CONDITIONAL)
    if not forecast.ok or forecast.data is None:
        j["latest_arrival_minute"] = no_weather_arrival
        if forecast.code == ErrorCode.OUT_OF_RANGE:
            reasons.append("예보 범위 밖(D-3부터 확인 가능) - 날씨 미판단")
        else:
            reasons.append("예보 조회 실패 - 날씨 미판단")
        return done(Verdict.CONDITIONAL)

    fc: SkyForecastOutput = forecast.data
    j["forecast_source"] = fc.source
    if fc.coverage == "none":
        j["latest_arrival_minute"] = no_weather_arrival
        reasons.append("관측 시간대 예보 없음 - 날씨 미판단")
        return done(Verdict.CONDITIONAL)

    wet = precip_spans(fc, dark.intersect(user))
    if wet:
        reasons.append(" · ".join(wet))
    slots = _slots(fc, tw, conditions.allow_partly_cloudy)
    clear = sum(b - a for a, b, c in slots if c == CLEAR)
    unknown = sum(b - a for a, b, c in slots if c == UNKNOWN)
    known = tw.minutes - unknown
    worst, max_pop = _sky_extremes(fc, tw)
    j.update(clear_minutes=clear, worst_sky=worst, max_pop=max_pop)
    partial = f" (확인된 {known}분 기준, {unknown}분은 예보·강수확률 확인 불가)" if unknown > 0 else ""

    if clear >= min_obs:
        j["latest_arrival_minute"] = _latest_arrival(slots, {CLEAR}, min_obs)
        reasons.append(f"맑은 시간 {clear}분{partial}")
        return done(Verdict.FIT)
    if unknown > 0 and clear + unknown >= min_obs:
        # 일부 시간대 날씨를 확인할 수 없음 — 역산은 맑음·미확인 구간만으로 (흐림 확인 구간 제외)
        arrival = _latest_arrival(slots, {CLEAR, UNKNOWN}, min_obs)
        j["latest_arrival_minute"] = arrival
        reasons.append(f"맑은 시간 {clear}분{partial} - 나머지 시간은 날씨 미판단")
        if arrival is not None:
            reasons.append(f"늦어도 {label(arrival)} 도착은 날씨 미확인 구간 기준(흐림 확인 구간 제외)")
        return done(Verdict.CONDITIONAL)
    reasons.append(f"맑은 시간 {clear}분{partial} - 최소 {min_obs}분 미달")
    return done(Verdict.UNFIT)


def judge_candidates(
    candidates: list[Candidate],
    conditions: Conditions,
    night_windows: dict[dt.date, NightWindowOutput | None],
    forecasts: dict[dt.date, ToolResult[Any]],
    settings: Any,
    object_windows: dict[ObjectKey, list[Window]] | None = None,
    object_meta: dict[ObjectKey, dict[str, Any]] | None = None,
    calendar_events: list[Any] | None = None,
) -> list[JudgedCandidate]:
    """object_windows / object_meta 키: (event_id 또는 title, date)."""
    min_obs = int(getattr(settings, "min_obs_minutes", 30))
    ow, om = object_windows or {}, object_meta or {}

    def pick(table: dict[ObjectKey, Any], c: Candidate) -> Any:
        return table.get((c.event_id, c.date), table.get((c.title, c.date)))

    return [
        judge_one(c, conditions, night_windows.get(c.date), forecasts.get(c.date), min_obs,
                  pick(ow, c), pick(om, c), calendar_events)
        for c in candidates
    ]


def forecast_hours(conditions: Conditions, night: NightWindowOutput) -> list[int]:
    """예보 조회 시각: 어두운 창 ∩ 내 창이 걸치는 정시 (0~29)."""
    dark = dark_window(night)
    if dark is None:
        return []
    w = dark.intersect(user_window(conditions, dark))
    if w.minutes <= 0:
        return []
    first, last = w.start // 60, (w.end - 1) // 60
    return [h for h in range(first, last + 1) if 0 <= h <= 29]


_VERDICT_ORDER = {Verdict.FIT: 0, Verdict.CONDITIONAL: 1, Verdict.UNFIT: 2, Verdict.UNJUDGED: 3}
_MOON_ORDER = {False: 0, None: 1, True: 2}


def rank_judged(judged: list[JudgedCandidate]) -> list[JudgedCandidate]:
    """추천 순서: 충족 먼저 → 맑은 시간 많은 순 → 달 영향 없음 우선 → 관측 창 긴 순 → 날짜 빠른 순."""
    return sorted(
        judged,
        key=lambda j: (
            _VERDICT_ORDER[j.verdict],
            -(j.clear_minutes or 0),
            _MOON_ORDER.get(j.moon_interferes, 1),
            -(j.time_window.minutes if j.time_window else 0),
            j.candidate.date,
        ),
    )
