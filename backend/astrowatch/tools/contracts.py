"""필수 Tool 4개의 입출력 계약 (F3002~F3004). 구현 모듈은 이 모델을 그대로 쓴다.

앞선 Tool 의 출력 중 다음 Tool 의 입력이 되는 값:
    search_sky_events.events[].date  → get_night_window.date, get_sky_forecast.date
    regions 표(실행 흐름)            → lat, lon (세 Tool 공통)
    get_night_window 의 창            → get_sky_forecast.hours
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


# ── 공통 ──────────────────────────────────────────────────────

class EventKind(str, Enum):
    METEOR = "meteor"          # 유성우
    PLANET = "planet"          # 행성 현상 (충, 최대이각, 근접)
    MOON = "moon"              # 달 현상 (근접, 원·근지점) — 위상 자체는 제외
    ECLIPSE = "eclipse"        # 일식·월식
    OCCULTATION = "occultation"  # 엄폐
    SEASON = "season"          # 절기 (동지 등) — 관측 대상 아님
    OTHER = "other"


# ── F3002 search_sky_events ───────────────────────────────────

class SkyEventsInput(BaseModel):
    year: int = Field(ge=2000, le=2100, description="조회 연도. 비우면 오늘 기준")
    month: int = Field(ge=1, le=12, description="조회 월. 비우면 이번 달")
    kind: EventKind | None = Field(default=None, description="현상 종류로 거르기. 비우면 전부")


class SkyEvent(BaseModel):
    event_id: str = Field(description="'YYYYMMDD-seq' — 상세·판단 단계에서 식별자로 쓴다")
    date: dt.date
    time: str | None = Field(default=None, description="HH:MM. 극대 시각 등. 없으면 None")
    title: str = Field(description="현상 이름. astroTitle 이 비면 astroEvent 로 채운다")
    kind: EventKind
    description: str | None = None


class SkyEventsOutput(BaseModel):
    year: int
    month: int
    requested_defaulted: bool = Field(description="연·월을 비워 기본값(이번 달)을 쓴 경우 true")
    events: list[SkyEvent]


# ── F3003 get_night_window ────────────────────────────────────

class NightWindowInput(BaseModel):
    date: dt.date = Field(description="관측 날짜(저녁 기준). 새벽 박명은 다음 날 값을 함께 조회")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class NightWindowOutput(BaseModel):
    date: dt.date
    sunset: str = Field(description="HH:MM 현지시각")
    astro_dusk_end: str = Field(description="저녁 천문박명 종료(aste). 이때부터 완전히 어둡다")
    astro_dawn_start: str = Field(description="다음 날 새벽 천문박명 시작(astm). 이때까지 어둡다")
    sunrise_next: str
    moonrise: str | None = Field(default=None, description="그날 월출. 없으면 None")
    moonset: str | None = Field(default=None, description="그날 월몰. 없으면 None")
    lun_age: float | None = Field(default=None, description="월령(일). 월령 API 실패 시 None + note")
    dark_minutes: int = Field(description="astro_dusk_end ~ astro_dawn_start 길이(분)")


# ── F3004 get_sky_forecast ────────────────────────────────────

class SkyForecastInput(BaseModel):
    date: dt.date
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    hours: list[int] = Field(
        default_factory=list,
        description="필요한 시각(0~23, 현지). 자정 넘김은 24~29 로 표기(24=다음 날 0시). 비우면 18~29",
    )


class SkyCondition(str, Enum):
    CLEAR = "clear"      # 맑음 (SKY=1)
    PARTLY = "partly"    # 구름많음 (SKY=3)
    CLOUDY = "cloudy"    # 흐림 (SKY=4)
    UNKNOWN = "unknown"  # 예보 없음


class HourlySky(BaseModel):
    hour: int = Field(ge=0, le=29, description="요청과 같은 표기(24+ 는 다음 날)")
    sky: SkyCondition
    pop: int | None = Field(default=None, ge=0, le=100, description="강수확률 %")
    cloud_pct: int | None = Field(default=None, ge=0, le=100, description="운량 % (OWM 등 제공 시)")


class SkyForecastOutput(BaseModel):
    date: dt.date
    source: str = Field(description="kma_short | kma_mid | owm | mock")
    base_time: str | None = Field(default=None, description="예보 발표 시각 (KMA: YYYYMMDD HHMM)")
    grid: tuple[int, int] | None = Field(default=None, description="KMA 격자 (nx, ny). 지역 격자 기준임을 표시")
    hourly: list[HourlySky]
    coverage: str = Field(description="full | partial | none — 요청 시간 중 예보가 있는 비율")


# ── F3004 search_observatory ──────────────────────────────────

class ObservatoryInput(BaseModel):
    query: str = Field(default="천문대", description="검색어. 기본 '천문대'")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, gt=0, le=20, description="카카오 상한 20km")


class Place(BaseModel):
    place_id: str
    name: str
    address: str
    url: str
    distance_m: int
    category: str | None = None


class ObservatoryOutput(BaseModel):
    query: str
    radius_km: float
    places: list[Place]
    note: str = Field(
        default="장소 검색은 그 장소의 하늘 상태를 알려 주지 않는다. 날씨는 지역 격자 기준이다.",
    )
