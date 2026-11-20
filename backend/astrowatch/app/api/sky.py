"""하늘 가리키기 API: 대상 천체의 좌표와 지금 고도, 방위 (계획 → 휴대폰 안내).

GET /api/sky/target  : key 또는 plan_id → 대상 + 관측지 + 지금(또는 at) 고도, 방위
GET /api/sky/targets : 고르기 목록 (이름 일부)
화면(pointer.js)은 적경, 적위를 받아 1초마다 스스로 고도, 방위를 다시 계산한다. 행성, 달만 60초마다 다시 부른다.
"""

from __future__ import annotations

import datetime as dt
from types import ModuleType
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from astrowatch.astro import targets as T

router = APIRouter(prefix="/sky", tags=["sky"])

KST = dt.timezone(dt.timedelta(hours=9))
SEOUL = ("서울", 37.5665, 126.9780)
NOT_FOUND_TARGET = "가리킬 천체를 찾을 수 없습니다"
NOT_FOUND_PLAN = "계획을 찾을 수 없습니다"
NO_PLAN_TARGET = "이 계획에는 가리킬 천체가 없습니다"
NEED_KEY = "key 또는 plan_id 가 필요합니다"
NEED_BOTH = "lat 와 lon 은 함께 보내야 합니다"
REFRESH_MOVING_S = 60
# 행성, 달 계산식과 datetime 연산이 안전한 범위 (이 밖이면 OverflowError 로 500 이 났다)
AT_MIN, AT_MAX = dt.date(1900, 1, 1), dt.date(2100, 12, 31)
AT_RANGE = "at 은 1900-01-01 부터 2100-12-31 사이 시각이어야 합니다"

Lat = Annotated[float | None, Query(ge=-90, le=90, description="관측지 위도. 없으면 계획 좌표, 계획 지역, 서울 순")]
Lon = Annotated[float | None, Query(ge=-180, le=180, description="관측지 경도")]
Key = Annotated[str | None, Query(min_length=1, max_length=100, description="대상 키 또는 이름 (예: 안드로메다 은하, mars, geminids)")]
PlanIdQ = Annotated[int | None, Query(ge=1, description="계획 ID. key 가 없으면 계획의 target_object 를 쓴다")]
At = Annotated[dt.datetime | None, Query(description="ISO 시각. 시간대가 없으면 KST. 기본 지금")]
SearchQ = Annotated[str, Query(max_length=50, description="이름 일부. 비우면 추천 목록")]


class SkyTargetOut(BaseModel):
    key: str
    name: str
    kind: Literal["object", "planet", "moon", "radiant"]
    subkind: str | None = Field(default=None, description="카탈로그 종류(galaxy, star ...) 또는 유성우 IMO 코드")
    ra: float = Field(description="적경(도). 행성, 달은 at 시각 값")
    dec: float
    alt: float
    az: float
    above_horizon: bool
    compass: str
    at: dt.datetime = Field(description="계산 시각 (UTC)")
    lat: float
    lon: float
    location_source: Literal["query", "plan", "region", "default"]
    location_label: str | None = None
    rises_at: dt.datetime | None = Field(default=None, description="지금 지평선 아래면 24시간 안에 떠오르는 대략의 시각 (UTC, 10분 단위)")
    refresh_s: int | None = Field(default=None, description="행성, 달은 이 간격으로 좌표를 다시 받는다. 고정 천체는 null")
    plan_id: int | None = None
    plan_title: str | None = None


class SkySuggestion(BaseModel):
    key: str
    name: str
    kind: Literal["object", "planet", "moon", "radiant"]


def _store() -> ModuleType:
    # 지연 import (테스트에서 교체 가능)
    from astrowatch.storage import plans

    return plans


def _utc(at: dt.datetime | None) -> dt.datetime:
    """at → UTC. 지원 범위(AT_MIN..AT_MAX, 받은 시간대 기준 날짜) 밖이면 422."""
    if at is None:
        return dt.datetime.now(dt.timezone.utc)
    if not AT_MIN <= at.date() <= AT_MAX:
        raise HTTPException(status_code=422, detail=AT_RANGE)
    if at.tzinfo is None:
        at = at.replace(tzinfo=KST)
    try:
        return at.astimezone(dt.timezone.utc)
    except OverflowError:
        raise HTTPException(status_code=422, detail=AT_RANGE) from None


def _plan_location(plan: object) -> tuple[float, float, str, str]:
    """계획 → (lat, lon, 출처, 라벨). 좌표가 없으면 지역 표, 그것도 없으면 서울."""
    label = getattr(plan, "location_label", None)
    lat, lon = getattr(plan, "lat", None), getattr(plan, "lon", None)
    if lat is not None and lon is not None:
        return float(lat), float(lon), "plan", label
    from astrowatch.data.regions import find_region

    region = find_region(label)
    if region is not None:
        return region.lat, region.lon, "region", region.name
    return SEOUL[1], SEOUL[2], "default", SEOUL[0]


@router.get("/target", response_model=SkyTargetOut, summary="가리킬 천체의 지금 고도, 방위")
def sky_target(key: Key = None, plan_id: PlanIdQ = None, lat: Lat = None, lon: Lon = None, at: At = None) -> SkyTargetOut:
    if key is None and plan_id is None:
        raise HTTPException(status_code=422, detail=NEED_KEY)
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail=NEED_BOTH)
    plan = None
    if plan_id is not None:
        plan = _store().get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND_PLAN)
    when = _utc(at)
    target_key = key or getattr(plan, "target_object", None)
    if not target_key:
        raise HTTPException(status_code=404, detail=NO_PLAN_TARGET)
    target = T.resolve_target(target_key, when)
    if target is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_TARGET)

    if lat is not None and lon is not None:
        place = (lat, lon, "query", None)
    elif plan is not None:
        place = _plan_location(plan)
    else:
        place = (SEOUL[1], SEOUL[2], "default", SEOUL[0])
    p_lat, p_lon, source, label = place

    pos = T.position(target, p_lat, p_lon, when)
    rise = None if pos["above_horizon"] else T.next_rise(target, p_lat, p_lon, when)
    return SkyTargetOut(
        key=target.key, name=target.name, kind=target.kind, subkind=target.subkind,
        ra=pos["ra"], dec=pos["dec"], alt=pos["alt"], az=pos["az"],
        above_horizon=pos["above_horizon"], compass=pos["compass"],
        at=when.replace(microsecond=0), lat=p_lat, lon=p_lon, location_source=source, location_label=label,
        rises_at=rise, refresh_s=REFRESH_MOVING_S if target.moving else None,
        plan_id=getattr(plan, "id", None), plan_title=getattr(plan, "event_title", None),
    )


@router.get("/targets", response_model=list[SkySuggestion], summary="가리킬 천체 고르기 목록")
def sky_targets(q: SearchQ = "") -> list[SkySuggestion]:
    return [SkySuggestion(**s) for s in T.search_targets(q.strip(), n=8)]
