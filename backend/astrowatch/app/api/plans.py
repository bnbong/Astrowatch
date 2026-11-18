"""관측 계획 API (F3015 · F3016)."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, HTTPException, Response, status

from astrowatch.schemas.plan import PlanCreate, PlanOut, PlanSaveResult, PlanUpdate

router = APIRouter(prefix="/plans", tags=["plans"])

NOT_FOUND = "계획을 찾을 수 없습니다"
BAD_ORDER = "종료 시각은 시작 시각보다 뒤여야 합니다"


def _store() -> ModuleType:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.storage import plans

    return plans


@router.get("", response_model=list[PlanOut], summary="계획 목록")
def list_plans() -> list[PlanOut]:
    return _store().list_plans()


@router.post("", response_model=PlanSaveResult, status_code=status.HTTP_201_CREATED, summary="계획 저장")
def create_plan(body: PlanCreate) -> PlanSaveResult:
    try:
        return _store().create_plan(body)
    except ValueError:
        raise HTTPException(status_code=422, detail=BAD_ORDER) from None


@router.get("/{plan_id}", response_model=PlanOut, summary="계획 상세")
def get_plan(plan_id: int) -> PlanOut:
    plan = _store().get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.patch("/{plan_id}", response_model=PlanOut, summary="계획 수정 (보낸 필드만)")
def update_plan(plan_id: int, body: PlanUpdate) -> PlanOut:
    try:
        plan = _store().update_plan(plan_id, body)
    except ValueError:
        raise HTTPException(status_code=422, detail=BAD_ORDER) from None
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, summary="계획 삭제")
def delete_plan(plan_id: int) -> Response:
    if not _store().delete_plan(plan_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
