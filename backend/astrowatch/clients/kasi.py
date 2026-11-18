"""천문연(KASI) 공공데이터 클라이언트 — 천문현상 · 출몰시각 · 월령. 응답은 XML.

외부 호출은 request() 하나로 모은다. 테스트는 이 함수를 samples 응답으로 바꾼다.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from typing import Any

from astrowatch.clients import http
from astrowatch.config import get_settings

SERVICE = "KASI"
BASE_URL = "https://apis.data.go.kr/B090041/openapi/service"

ASTRO_EVENT = "AstroEventInfoService/getAstroEventInfo"
RISE_SET = "RiseSetInfoService/getLCRiseSetInfo"
LUN_PHASE = "LunPhInfoService/getLunPhInfo"

_SPACES = re.compile(r"\s+")


def request(path: str, params: dict[str, Any]) -> str:
    """KASI 호출 1회 → XML 본문. 키 없음·게이트웨이 오류는 예외로 올린다."""
    key = get_settings().data_go_kr_key
    if not key:
        raise http.AuthError(f"{SERVICE} 서비스키 없음")
    resp = http.get(SERVICE, f"{BASE_URL}/{path}", params={"serviceKey": key, **params})
    text = resp.text
    http.check_data_go_kr_header(SERVICE, text)
    return text


def clean(text: str | None) -> str:
    """&#xD; · 줄바꿈 · 연속 공백을 한 칸으로 정리."""
    if not text:
        return ""
    return _SPACES.sub(" ", text.replace("\r", " ")).strip()


def parse_items(xml_text: str) -> list[dict[str, str]]:
    """<item> 목록을 dict 로. resultCode 가 00(정상)·03(데이터 없음)이 아니면 UpstreamError."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as e:
        raise http.UpstreamError(SERVICE, 200, "XML 파싱 실패") from e
    code = clean(root.findtext("./header/resultCode"))
    if code == "03":
        return []
    if code and code != "00":
        raise http.UpstreamError(SERVICE, 200, f"resultCode={code}")
    return [{child.tag: clean(child.text) for child in item} for item in root.iter("item")]


def get_astro_events(year: int, month: int) -> list[dict[str, str]]:
    params = {"solYear": str(year), "solMonth": f"{month:02d}", "numOfRows": "100", "pageNo": "1"}
    return parse_items(request(ASTRO_EVENT, params))


def get_rise_set(date: dt.date, lat: float, lon: float) -> dict[str, str] | None:
    params = {"locdate": date.strftime("%Y%m%d"), "longitude": f"{lon:.4f}", "latitude": f"{lat:.4f}", "dnYn": "Y"}
    items = parse_items(request(RISE_SET, params))
    return items[0] if items else None


def get_lun_age(date: dt.date) -> float | None:
    params = {"solYear": str(date.year), "solMonth": f"{date.month:02d}", "solDay": f"{date.day:02d}"}
    items = parse_items(request(LUN_PHASE, params))
    if not items:
        return None
    try:
        return float(items[0].get("lunAge", ""))
    except ValueError:
        return None
