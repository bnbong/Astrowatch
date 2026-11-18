"""심화 lookup_sky_object — 로컬 서버 API Tool (천체 카탈로그 + 고도 계산).

카탈로그: astrowatch/data/sky_objects.json (scripts/build_sky_objects.py 로 생성, VastHorizon 출처).
행성(수성~해왕성)은 카탈로그 대신 그날 위치를 계산한다. 카탈로그에 없는 천체는 고도를 추정하지 않는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from astrowatch.astro import planets
from astrowatch.astro.altaz import culmination, minute_to_utc, visible_spans
from astrowatch.astro.catalog import match_object, similar_names
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import SkyObjectInfo, SkyObjectInput, SkyObjectOutput, SkyObjectWindow
from astrowatch.tools.registry import registry


def _output(info: SkyObjectInfo, radec: Any, args: SkyObjectInput) -> tuple[SkyObjectOutput, str | None]:
    spans = visible_spans(radec, args.lat, args.lon, args.date, args.min_alt)
    minute, alt, az = culmination(radec, args.lat, args.lon, args.date)
    never = not spans
    note = None
    if never:
        note = (f"{info.name_ko}: 그날 지평선 위로 뜨지 않음 (최고 고도 {alt:.0f}°)" if alt < 0
                else f"{info.name_ko}: 그날 고도 {args.min_alt:g}° 이상으로 오르지 않음 (최고 고도 {alt:.0f}°)")
    out = SkyObjectOutput(
        object=info, date=args.date, min_alt=args.min_alt,
        visible_windows=[SkyObjectWindow(start=a, end=b) for a, b in spans],
        transit_minute=minute, transit_alt=round(alt, 1), transit_az=round(az, 1), never_rises=never,
    )
    return out, note


@registry.tool(
    name="lookup_sky_object",
    description=(
        "사용자가 특정 천체(은하·성운·성단·별·별자리·행성)를 말했을 때 그 천체가 그날 밤 언제 떠 있는지 알아야 할 때 쓴다. "
        "적경·적위로 고도를 계산해 고도 min_alt(기본 20°) 이상인 시간 구간과 남중 시각·고도를 준다. "
        "이름을 못 찾으면 NOT_FOUND(비슷한 이름 제시) — 카탈로그에 없는 천체는 추정하지 않는다."
    ),
    kind=ToolKind.LOCAL,
    input_model=SkyObjectInput,
    output_model=SkyObjectOutput,
    fixed_arg_names=("lat", "lon"),
)
def lookup_sky_object(args: SkyObjectInput) -> ToolResult[SkyObjectOutput]:
    body = planets.find_body(args.name)
    if body is not None:
        ko, en, mag = planets.BODIES[body]

        def radec(t: dt.datetime) -> tuple[float, float]:
            return planets.radec(body, t)

        minute, _alt, _az = culmination(radec, args.lat, args.lon, args.date)

        ra, dec = radec(minute_to_utc(args.date, minute))
        info = SkyObjectInfo(id=body, name_ko=ko, name_en=en, kind="planet", ra_deg=round(ra, 4),
                             dec_deg=round(dec, 4), mag=mag, naked_eye=mag < 6)
        out, note = _output(info, radec, args)
        extra = "행성 위치는 저정밀 궤도 요소로 계산(오차 수 분각)"
        return ok(out, note=f"{note} / {extra}" if note else extra)

    o, ambiguous = match_object(args.name)
    if o is None:
        # 입력 원문은 메시지에 넣지 않는다 — 고정 문구 + 카탈로그 이름만
        if ambiguous:
            return fail(ErrorCode.NOT_FOUND, f"천체 이름이 모호함. 후보: {', '.join(ambiguous)}")
        sims = similar_names(args.name)
        hint = f" 비슷한 이름: {', '.join(sims)}" if sims else ""
        return fail(ErrorCode.NOT_FOUND, f"천체 카탈로그에서 찾지 못함.{hint}")
    info = SkyObjectInfo(id=o["id"], name_ko=o["name_ko"], name_en=o.get("name_en"), kind=o.get("kind") or "dso",
                         ra_deg=o["ra_deg"], dec_deg=o["dec_deg"], mag=o.get("mag"), naked_eye=o.get("naked_eye"),
                         constellation_ko=o.get("constellation_ko"))
    ra, dec = o["ra_deg"], o["dec_deg"]
    out, note = _output(info, lambda _t: (ra, dec), args)
    return ok(out, note=note)
