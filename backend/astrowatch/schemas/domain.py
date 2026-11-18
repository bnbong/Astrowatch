"""도메인 모델 — 조건(F3005), 후보, 관측 창, 판정(F3008).

시각 표기 규칙: 하루를 '저녁 기준 날짜(date)' 하나로 묶고, 분 단위 오프셋(minute_of_night)을 쓴다.
    0 = date 00:00, 1320 = 22:00, 1560 = 다음 날 02:00 (24h 를 넘겨 표기)
자정 넘김 비교가 단순해진다. 사람에게 보일 때만 HH:MM 으로 바꾼다.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field

from astrowatch.tools.contracts import EventKind, SkyCondition


# ── 의도 (analyze 단계) ────────────────────────────────────────

class Intent(str, Enum):
    PLAN = "plan"                # 관측 계획 수립 (Tool 필요)
    FACT = "fact"                # 사실 조회 (현상·박명 등 단일 Tool)
    CONFIRM = "confirm"          # 직전 추천을 계획으로 확정·저장
    CALENDAR = "calendar"        # 캘린더 등록 (심화)
    KNOWLEDGE = "knowledge"      # 배경 지식 (심화 RAG)
    CHAT = "chat"                # 인사·사용법 — Tool 없음
    CLARIFY = "clarify"          # 조건 부족 — 되묻기


# ── 조건 (F3005) ──────────────────────────────────────────────

class Conditions(BaseModel):
    """요청에서 뽑은 조건. 못 뽑은 값은 None 으로 둔다 — 임의로 채우지 않는다."""

    # 기준 조건
    date_from: dt.date | None = None
    date_to: dt.date | None = Field(default=None, description="범위 요청('다음 주')이면 끝 날짜. 단일 날짜면 date_from 과 같음")
    start_minute: int | None = Field(default=None, ge=0, le=2159, description="시작 가능 시각(분, 저녁 기준 날짜 00:00 부터)")

    # 선호 조건
    region: str | None = Field(default=None, description="사용자가 말한 지역명 원문")
    event_kind: EventKind | None = None
    event_query: str | None = Field(default=None, description="'쌍둥이자리 유성우'처럼 현상을 지목한 경우")
    place_query: str | None = Field(default=None, description="'천문대' 등 관측지 유형")

    # 제약 조건
    end_minute: int | None = Field(default=None, ge=0, le=2159, description="종료(귀가) 시각(분)")
    max_distance_km: float | None = Field(default=None, gt=0, le=20)
    allow_partly_cloudy: bool = Field(default=False, description="구름많음도 허용하는가")
    avoid_moon: bool = Field(default=True, description="달빛 회피 (유성우·은하수에만 적용)")

    # 사용자가 못박은 값 — 재계획에서 바꾸지 않는다
    fixed: list[str] = Field(default_factory=list, description="예: ['date_from', 'event_query']")

    def missing_for_plan(self) -> list[str]:
        """계획 수립에 꼭 필요한데 비어 있는 조건. 비어 있으면 되묻는다 (R7).

        날짜는 필수가 아니다 — "보기 좋은 날짜 추천해 줘" 가 성립해야 하므로, 비어 있으면
        실행 흐름이 기본 범위(오늘부터 예보 한도까지)를 넣고 그 사실을 응답에 밝힌다 (F3002 기본값·추정 표시).
        """
        missing = []
        if self.region is None:
            missing.append("region")
        if self.start_minute is None and self.end_minute is not None:
            missing.append("start_minute")  # 끝만 있고 시작이 없으면 계산 시작점이 없다
        return missing

    def merged(self, update: "Conditions") -> "Conditions":
        """후속 요청(F3010): 새로 말한 값만 갱신하고 나머지는 유지."""
        data = self.model_dump()
        for k, v in update.model_dump(exclude_unset=True).items():
            if v is not None and k != "fixed":
                data[k] = v
        data["fixed"] = sorted(set(self.fixed) | set(update.fixed))
        return Conditions.model_validate(data)


# ── 관측 창 · 판정 ────────────────────────────────────────────

class Window(BaseModel):
    """분 단위 반개구간 [start, end). 저녁 기준 날짜 00:00 이 0."""

    start: int = Field(ge=0, le=2159)
    end: int = Field(ge=0, le=2159)

    @property
    def minutes(self) -> int:
        return max(0, self.end - self.start)

    def intersect(self, other: "Window") -> "Window":
        s, e = max(self.start, other.start), min(self.end, other.end)
        return Window(start=s, end=max(s, e))

    @staticmethod
    def label(minute: int) -> str:
        h, m = divmod(minute, 60)
        return f"{h % 24:02d}:{m:02d}" + ("+1" if h >= 24 else "")


class Verdict(str, Enum):
    FIT = "fit"                  # 충족: 시간 창 ≥ 최소 그리고 맑은 시간 ≥ 최소
    CONDITIONAL = "conditional"  # 조건부 충족: 시간 창은 되는데 예보가 없어 날씨 미판단
    UNFIT = "unfit"              # 불충족
    UNJUDGED = "unjudged"        # 판단 안 함: 박명 등 필요한 속성을 확인 못 함 (F3003)


class Candidate(BaseModel):
    """판정 전 후보 = 현상 × 날짜 × 지역."""

    event_id: str
    title: str
    kind: EventKind
    date: dt.date
    region: str
    lat: float
    lon: float
    event_minute: int | None = Field(default=None, description="극대 시각(분). 없으면 None")


class JudgedCandidate(BaseModel):
    candidate: Candidate
    verdict: Verdict
    dark_window: Window | None = None
    user_window: Window | None = None
    event_window: Window | None = None
    time_window: Window | None = Field(default=None, description="어두운 ∩ 내 ∩ 현상")
    clear_minutes: int | None = Field(default=None, description="맑은 시간(분). 예보 없으면 None")
    worst_sky: SkyCondition | None = None
    max_pop: int | None = None
    moon_interferes: bool | None = Field(default=None, description="월령 10~20 이고 시간 창에 달이 떠 있음")
    lun_age: float | None = None
    latest_arrival_minute: int | None = Field(
        default=None, description="역산 기준: 늦어도 이 시각에 도착하면 최소 관측 시간을 채운다",
    )
    forecast_source: str | None = None
    reasons: list[str] = Field(default_factory=list, description="판정 근거 · 제외 사유 (사용자에게 그대로 보임)")
    target_alt: float | None = None
    target_az: float | None = None
    # 화면의 '밤 타임바'용 — 판정에 쓴 구간을 그대로 노출한다 (값을 새로 만들지 않는다)
    moon_up: list[Window] = Field(default_factory=list, description="달이 지평선 위에 있는 구간(분). 월출·월몰 정보가 없으면 비움")
    moon_unknown: list[Window] = Field(default_factory=list, description="월출·월몰 정보가 없어 달 여부를 모르는 구간")
    hourly_sky: list["HourSky"] = Field(default_factory=list, description="판정에 쓴 시간별 하늘 상태(어두운 창 ∩ 내 창 범위)")
    object_window: Window | None = Field(default=None, description="천체 고도 20° 이상 구간(카탈로그 천체일 때)")


class HourSky(BaseModel):
    """밤 타임바 한 칸. hour 는 저녁 기준 날짜 00:00 부터의 시(24+ 는 다음 날)."""

    hour: int = Field(ge=0, le=35)
    sky: SkyCondition
    pop: int | None = None
    precip: str | None = Field(default=None, description="none|rain|snow|sleet|shower, 모르면 None")
