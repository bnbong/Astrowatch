"""관측 계획 (F3015~F3016). 저장소 · API 공통."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from astrowatch.tools.contracts import EventKind


class PlanBase(BaseModel):
    event_title: str = Field(min_length=1, max_length=200)
    event_kind: EventKind = EventKind.OTHER
    start_dt: datetime = Field(description="관측 시작 (현지, naive). 자정 넘김은 end_dt 가 다음 날")
    end_dt: datetime
    location_label: str = Field(min_length=1, max_length=100)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    place_id: str | None = Field(default=None, max_length=64, description="카카오 장소 ID. 이름·주소는 저장하지 않는다")
    place_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    target_alt: float | None = Field(default=None, ge=-90, le=90)
    target_az: float | None = Field(default=None, ge=0, le=360)
    target_object: str | None = Field(
        default=None, max_length=100,
        description="하늘 가리키기 대상 키(카탈로그 이름, 행성 키, 'moon', 유성우 복사점 키). 비우면 제목에서 추론, 못 찾으면 null",
    )
    basis: str | None = Field(default=None, max_length=2000, description="판정 등급 · 맑은 시간 · 달 영향 요약")

    @model_validator(mode="after")
    def _check_order(self) -> "PlanBase":
        if self.end_dt <= self.start_dt:
            raise ValueError("end_dt 는 start_dt 보다 뒤여야 한다")
        return self


class PlanCreate(PlanBase):
    session_id: str | None = Field(default=None, description="어느 대화에서 만든 계획인지")


class PlanUpdate(BaseModel):
    """핵심 값 수정. 보낸 필드만 바뀐다."""

    event_title: str | None = Field(default=None, min_length=1, max_length=200)
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    location_label: str | None = Field(default=None, min_length=1, max_length=100)
    basis: str | None = Field(default=None, max_length=2000)
    target_object: str | None = Field(default=None, max_length=100, description="null 이면 대상 없음으로 비운다")


class PlanOut(PlanBase):
    id: int
    session_id: str | None = None
    calendar_event_id: str | None = None
    created_at: datetime
    updated_at: datetime


class PlanSaveResult(BaseModel):
    plan: PlanOut
    overlaps: list[PlanOut] = Field(default_factory=list, description="같은 시간대에 겹치는 기존 계획. 저장은 막지 않는다")
