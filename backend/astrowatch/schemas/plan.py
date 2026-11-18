"""관측 계획 (F3015~F3016). 저장소 · API 공통."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from astrowatch.tools.contracts import EventKind


class PlanBase(BaseModel):
    event_title: str
    event_kind: EventKind = EventKind.OTHER
    start_dt: datetime = Field(description="관측 시작 (현지, naive). 자정 넘김은 end_dt 가 다음 날")
    end_dt: datetime
    location_label: str
    lat: float | None = None
    lon: float | None = None
    place_id: str | None = Field(default=None, description="카카오 장소 ID. 이름·주소는 저장하지 않는다")
    place_url: str | None = None
    target_alt: float | None = None
    target_az: float | None = None
    basis: str | None = Field(default=None, description="판정 등급 · 맑은 시간 · 달 영향 요약")

    @model_validator(mode="after")
    def _check_order(self) -> "PlanBase":
        if self.end_dt <= self.start_dt:
            raise ValueError("end_dt 는 start_dt 보다 뒤여야 한다")
        return self


class PlanCreate(PlanBase):
    session_id: str | None = Field(default=None, description="어느 대화에서 만든 계획인지")


class PlanUpdate(BaseModel):
    """핵심 값 수정. 보낸 필드만 바뀐다."""

    event_title: str | None = None
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    location_label: str | None = None
    basis: str | None = None


class PlanOut(PlanBase):
    id: int
    session_id: str | None = None
    calendar_event_id: str | None = None
    created_at: datetime
    updated_at: datetime


class PlanSaveResult(BaseModel):
    plan: PlanOut
    overlaps: list[PlanOut] = Field(default_factory=list, description="같은 시간대에 겹치는 기존 계획. 저장은 막지 않는다")
