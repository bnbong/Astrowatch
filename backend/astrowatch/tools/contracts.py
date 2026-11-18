"""필수 Tool 4개의 입출력 계약 (F3002~F3004). 구현 모듈은 이 모델을 그대로 쓴다.

앞선 Tool 의 출력 중 다음 Tool 의 입력이 되는 값:
    search_sky_events.events[].date  → get_night_window.date, get_sky_forecast.date
    regions 표(실행 흐름)            → lat, lon (세 Tool 공통)
    get_night_window 의 창            → get_sky_forecast.hours
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── 공통 ──────────────────────────────────────────────────────

class EventKind(str, Enum):
    METEOR = "meteor"          # 유성우
    PLANET = "planet"          # 행성 현상 (충, 최대이각, 근접)
    MOON = "moon"              # 달 현상 (근접, 원·근지점) — 위상 자체는 제외
    ECLIPSE = "eclipse"        # 일식·월식
    OCCULTATION = "occultation"  # 엄폐
    SEASON = "season"          # 절기 (동지 등) — 관측 대상 아님
    DEEP_SKY = "deep_sky"      # 은하·성운·성단 등 특정 '현상'이 아닌 천체 — 어두운 밤 전체가 창, 달빛에 민감
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
    moonrise_next: str | None = Field(default=None, description="다음 날 월출 (새벽 구간 달 판단용). 없으면 None")
    moonset_next: str | None = Field(default=None, description="다음 날 월몰. 없으면 None")
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


class Precip(str, Enum):
    NONE = "none"
    RAIN = "rain"
    SNOW = "snow"
    SLEET = "sleet"      # 비/눈
    SHOWER = "shower"


class HourlySky(BaseModel):
    hour: int = Field(ge=0, le=29, description="요청과 같은 표기(24+ 는 다음 날)")
    sky: SkyCondition
    pop: int | None = Field(default=None, ge=0, le=100, description="강수확률 %")
    cloud_pct: int | None = Field(default=None, ge=0, le=100, description="운량 % (OWM 등 제공 시)")
    precip: Precip | None = Field(default=None, description="강수형태(KMA PTY / OWM weather). 모르면 None")


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


# ── 심화 lookup_sky_object (로컬 서버 API) ────────────────────

class SkyObjectInput(BaseModel):
    name: str = Field(min_length=1, description="천체 이름 (한국어·영어·메시에 번호. 예: '안드로메다 은하', 'M31', '목성')")
    date: dt.date = Field(description="관측 날짜(저녁 기준)")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    min_alt: float = Field(default=20, ge=0, le=80, description="이 고도(도) 이상일 때만 '떠 있다'고 본다")


class SkyObjectInfo(BaseModel):
    id: str
    name_ko: str
    name_en: str | None = None
    kind: str = Field(description="galaxy | nebula | cluster | star | constellation | milky_way | dso | planet")
    ra_deg: float = Field(description="적경(도). 행성은 그날 남중 무렵 값")
    dec_deg: float
    mag: float | None = None
    naked_eye: bool | None = None
    constellation_ko: str | None = None


class SkyObjectWindow(BaseModel):
    """분 단위 [start, end). 저녁 기준 날짜 00:00 이 0 (domain.Window 와 같은 표기)."""

    start: int = Field(ge=0, le=2159)
    end: int = Field(ge=0, le=2159)


class SkyObjectOutput(BaseModel):
    object: SkyObjectInfo
    date: dt.date
    min_alt: float
    visible_windows: list[SkyObjectWindow] = Field(description="고도 min_alt 이상인 구간 (12:00~다음 날 12:00, 10분 간격)")
    transit_minute: int = Field(description="남중(가장 높을 때) 시각(분)")
    transit_alt: float
    transit_az: float | None = None
    never_rises: bool = Field(description="그날 min_alt 이상으로 오르지 않음")


# ── 심화 F3023 get_calendar / add_plan_to_calendar ────────────
# 시각은 모두 현지(Asia/Seoul) naive. 공급자(local|google)가 바뀌어도 이 모델은 그대로다.

class CalendarQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: dt.date = Field(description="조회 시작 날짜(포함)")
    date_to: dt.date = Field(description="조회 끝 날짜(포함). date_from 이후, 최대 92일")


class CalendarEvent(BaseModel):
    event_id: str = Field(description="공급자 일정 ID (local 은 'local-<n>')")
    title: str
    start_dt: dt.datetime = Field(description="현지 naive")
    end_dt: dt.datetime
    source: Literal["local", "google"]
    plan_id: int | None = Field(default=None, description="Astrowatch 계획에서 등록한 일정이면 그 계획 번호")
    html_link: str | None = Field(default=None, description="Google 일정 링크. local 은 None")


class CalendarQueryOutput(BaseModel):
    provider: str = Field(description="local | google")
    date_from: dt.date
    date_to: dt.date
    events: list[CalendarEvent]


class CalendarAddInput(BaseModel):
    """계획 번호만 받는다. 제목·시각은 저장소에서 읽는다 (모델이 값을 지어내지 못하게)."""

    model_config = ConfigDict(extra="forbid")

    plan_id: int = Field(gt=0, description="저장된 관측 계획 번호")


class CalendarAddOutput(BaseModel):
    provider: str = Field(description="local | google")
    plan_id: int
    event_id: str
    title: str
    start_dt: dt.datetime
    end_dt: dt.datetime
    html_link: str | None = None
    already_registered: bool = Field(description="이미 등록된 계획이라 새로 만들지 않았으면 true")
