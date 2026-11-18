"""카카오 로컬 키워드 검색 클라이언트. 결과는 저장하지 않는다(이용 조건) — ID·링크만 쓴다."""

from __future__ import annotations

from typing import Any

from astrowatch.clients import http
from astrowatch.config import get_settings

SERVICE = "Kakao"
KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
MAX_RADIUS_M = 20000


def request(params: dict[str, Any]) -> dict[str, Any]:
    """키워드 검색 1회 → JSON."""
    key = get_settings().kakao_rest_key
    if not key:
        raise http.AuthError(f"{SERVICE} REST 키 없음")
    resp = http.get(SERVICE, KEYWORD_URL, params=params, headers={"Authorization": f"KakaoAK {key}"})
    try:
        return resp.json()
    except ValueError as e:
        raise http.UpstreamError(SERVICE, resp.status_code, "JSON 아님") from e


def search_keyword(query: str, lat: float, lon: float, radius_m: int, size: int = 15) -> list[dict[str, Any]]:
    payload = request(
        {
            "query": query,
            "x": f"{lon:.6f}",
            "y": f"{lat:.6f}",
            "radius": str(min(max(radius_m, 0), MAX_RADIUS_M)),
            "size": str(size),
            "sort": "distance",
        }
    )
    docs = payload.get("documents")
    if not isinstance(docs, list):
        raise http.UpstreamError(SERVICE, 200, "documents 없음")
    return docs
