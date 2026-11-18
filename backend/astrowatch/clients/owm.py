"""OpenWeatherMap 5일/3시간 예보 클라이언트."""

from __future__ import annotations

from typing import Any

from astrowatch.clients import http
from astrowatch.config import get_settings

SERVICE = "OpenWeatherMap"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


def request(params: dict[str, Any]) -> dict[str, Any]:
    """예보 호출 1회 → JSON."""
    key = get_settings().openweather_key
    if not key:
        raise http.AuthError(f"{SERVICE} 키 없음")
    resp = http.get(SERVICE, FORECAST_URL, params={**params, "appid": key})
    try:
        return resp.json()
    except ValueError as e:
        raise http.UpstreamError(SERVICE, resp.status_code, "JSON 아님") from e


def get_forecast(lat: float, lon: float) -> dict[str, Any]:
    """{'list': [3시간 슬롯...], 'city': {'timezone': UTC 오프셋 초}} 원본 그대로."""
    payload = request({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "units": "metric"})
    if str(payload.get("cod", "200")) != "200" or not isinstance(payload.get("list"), list):
        raise http.UpstreamError(SERVICE, 200, "예보 목록 없음")
    return payload
