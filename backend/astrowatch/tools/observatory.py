"""F3004 search_observatory — 카카오 키워드 검색으로 주변 관측 장소 찾기.

장소의 하늘 상태는 알려 주지 않는다(임의 결합 금지). 결과는 저장하지 않고 ID·링크만 넘긴다.
"""

from __future__ import annotations

from typing import Any

from astrowatch.clients import http, kakao
from astrowatch.config import get_settings
from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import ObservatoryInput, ObservatoryOutput, Place
from astrowatch.tools.registry import registry


def to_place(doc: dict[str, Any]) -> Place | None:
    # 거리는 x,y 를 보냈을 때만 온다. 없으면 0 으로 지어내지 않고 그 항목을 뺀다.
    try:
        distance = int(float(doc["distance"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not doc.get("id") or not doc.get("place_name"):
        return None
    return Place(
        place_id=str(doc["id"]),
        name=doc["place_name"],
        address=doc.get("road_address_name") or doc.get("address_name") or "",
        url=doc.get("place_url") or "",
        distance_m=distance,
        category=doc.get("category_group_name") or None,
    )


@registry.tool(
    name="search_observatory",
    description=(
        "관측할 장소(천문대·관측지 등)를 위치 주변에서 찾을 때 쓴다. 반경은 최대 20km. "
        "장소의 날씨·하늘 상태는 알려 주지 않는다."
    ),
    kind=ToolKind.EXTERNAL,
    input_model=ObservatoryInput,
    output_model=ObservatoryOutput,
)
def search_observatory(args: ObservatoryInput) -> ToolResult[ObservatoryOutput]:
    if not get_settings().kakao_rest_key:
        return fail(ErrorCode.AUTH, "카카오 REST 키가 설정되지 않았다")
    query = args.query.strip() or "천문대"
    try:
        docs = kakao.search_keyword(query, args.lat, args.lon, int(args.radius_km * 1000))
    except http.AuthError:
        return fail(ErrorCode.AUTH, "카카오 API 인증 실패 (REST 키 확인)")
    except http.UpstreamTimeout:
        return fail(ErrorCode.TIMEOUT, "카카오 API 응답 시간 초과")
    except http.UpstreamError:
        return fail(ErrorCode.UPSTREAM, "카카오 API 오류 응답")

    places = [p for p in (to_place(d) for d in docs) if p is not None]
    if not places:
        return fail(ErrorCode.NO_RESULT, f"반경 {args.radius_km:g}km 안에 검색 결과 없음")
    return ok(ObservatoryOutput(query=query, radius_km=args.radius_km, places=places))
