"""관측 계획 API (F3015 · F3016)."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, HTTPException, Response, status

from astrowatch.app.api import DEFAULT_LIMIT, Limit, Offset, PlanId, page
from astrowatch.schemas.plan import PlanCreate, PlanOut, PlanSaveResult, PlanUpdate

router = APIRouter(prefix="/plans", tags=["plans"])

NOT_FOUND = "계획을 찾을 수 없습니다"
BAD_ORDER = "종료 시각은 시작 시각보다 뒤여야 합니다"
BAD_TARGET = "가리킬 수 있는 천체가 아닙니다"


def _store() -> ModuleType:
    # 지연 import (테스트에서 이 함수를 교체)
    from astrowatch.storage import plans

    return plans


@router.get("", response_model=list[PlanOut], summary="계획 목록")
def list_plans(limit: Limit = DEFAULT_LIMIT, offset: Offset = 0) -> list[PlanOut]:
    return page(_store().list_plans(), limit, offset)


@router.post("", response_model=PlanSaveResult, status_code=status.HTTP_201_CREATED, summary="계획 저장")
def create_plan(body: PlanCreate) -> PlanSaveResult:
    # 하늘 가리키기 대상: 보낸 이름을 키로 정규화하고, 없으면 제목에서 추론한다 (못 찾으면 null)
    from astrowatch.astro.targets import canonical_key, infer_target

    target = canonical_key(body.target_object) or infer_target(body.event_title, body.event_kind.value)
    body = body.model_copy(update={"target_object": target})
    try:
        return _store().create_plan(body)
    except ValueError:
        raise HTTPException(status_code=422, detail=BAD_ORDER) from None


@router.get("/{plan_id}", response_model=PlanOut, summary="계획 상세")
def get_plan(plan_id: PlanId) -> PlanOut:
    plan = _store().get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.patch("/{plan_id}", response_model=PlanOut, summary="계획 수정 (보낸 필드만)")
def update_plan(plan_id: PlanId, body: PlanUpdate) -> PlanOut:
    if body.target_object is not None:
        from astrowatch.astro.targets import canonical_key

        key = canonical_key(body.target_object)
        if key is None:
            raise HTTPException(status_code=422, detail=BAD_TARGET)
        body = body.model_copy(update={"target_object": key})
    try:
        plan = _store().update_plan(plan_id, body)
    except ValueError:
        raise HTTPException(status_code=422, detail=BAD_ORDER) from None
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, summary="계획 삭제")
def delete_plan(plan_id: PlanId) -> Response:
    if not _store().delete_plan(plan_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
