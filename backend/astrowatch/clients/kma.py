"""기상청 공공데이터 클라이언트 — 단기예보 · 중기육상예보. 응답은 JSON(dataType=JSON).

외부 호출은 request() 하나로 모은다. 발표 시각 계산은 순수 함수로 둔다.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from astrowatch.clients import http
from astrowatch.config import get_settings

SERVICE = "KMA"
VILAGE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
MID_LAND_URL = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"

VILAGE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
PUBLISH_LAG = dt.timedelta(minutes=10)   # 발표 후 API 반영까지 여유


def request(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """KMA 호출 1회 → JSON. 키 없음·게이트웨이 오류(XML 본문)는 예외로 올린다."""
    key = get_settings().data_go_kr_key
    if not key:
        raise http.AuthError(f"{SERVICE} 서비스키 없음")
    resp = http.get(SERVICE, url, params={"serviceKey": key, "dataType": "JSON", "pageNo": "1", **params})
    text = resp.text
    http.check_data_go_kr_header(SERVICE, text)
    try:
        return json.loads(text)
    except ValueError as e:
        raise http.UpstreamError(SERVICE, 200, "JSON 아님") from e


def items_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """response.body.items.item 추출. resultCode 00 아니면 UpstreamError, 03(데이터 없음)은 빈 목록."""
    resp = payload.get("response") or {}
    code = str((resp.get("header") or {}).get("resultCode", ""))
    if code == "03":
        return []
    if code != "00":
        raise http.UpstreamError(SERVICE, 200, f"resultCode={code or '?'}")
    items = (resp.get("body") or {}).get("items") or {}
    if not isinstance(items, dict):  # 데이터 없을 때 "" 로 오는 경우
        return []
    item = items.get("item") or []
    return item if isinstance(item, list) else [item]


def vilage_base(now: dt.datetime) -> tuple[str, str]:
    """단기예보 (base_date, base_time). 지금-10분 이전의 가장 최근 발표. 02:10 전이면 전날 2300."""
    t = now - PUBLISH_LAG
    hours = [h for h in VILAGE_HOURS if h <= t.hour]
    if hours:
        return t.strftime("%Y%m%d"), f"{hours[-1]:02d}00"
    return (t - dt.timedelta(days=1)).strftime("%Y%m%d"), "2300"


def mid_tmfc(now: dt.datetime) -> dt.datetime:
    """중기예보 발표 시각. 06시·18시 발표(+10분 반영), 06:10 전이면 전날 18시."""
    t = now - PUBLISH_LAG
    if t.hour >= 18:
        return t.replace(hour=18, minute=0, second=0, microsecond=0)
    if t.hour >= 6:
        return t.replace(hour=6, minute=0, second=0, microsecond=0)
    return (t - dt.timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)


def get_vilage_fcst(nx: int, ny: int, base_date: str, base_time: str) -> list[dict[str, Any]]:
    params = {"numOfRows": "1000", "base_date": base_date, "base_time": base_time, "nx": str(nx), "ny": str(ny)}
    return items_of(request(VILAGE_URL, params))


def get_mid_land_fcst(reg_id: str, tmfc: str) -> dict[str, Any] | None:
    items = items_of(request(MID_LAND_URL, {"numOfRows": "10", "regId": reg_id, "tmFc": tmfc}))
    return items[0] if items else None
