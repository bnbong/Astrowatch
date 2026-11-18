"""공통 HTTP 호출 — 타임아웃 · 오류 분류. 키는 절대 예외 메시지에 넣지 않는다."""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_TIMEOUT = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024   # 외부 응답 상한 (자원 한도)
_CHUNK = 64 * 1024


class UpstreamError(Exception):
    """외부 API 가 오류 응답을 준 경우 (HTTP 4xx/5xx 또는 본문의 오류 코드)."""

    def __init__(self, service: str, status: int | None, detail: str = ""):
        self.service, self.status, self.detail = service, status, detail
        super().__init__(f"{service} 응답 오류 (HTTP {status}) {detail}".strip())


class UpstreamTimeout(Exception):
    pass


class AuthError(Exception):
    """키 누락·미등록·거부. 재시도해도 같은 결과."""


def get(
    service: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    # 예외는 `from None` 으로 체인을 끊는다 — requests 예외 메시지 · traceback 에는
    # serviceKey 가 들어간 전체 URL 이 남기 때문 (redact 필터와 이중 방어, NF3001)
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout, stream=True)
    except requests.Timeout:
        raise UpstreamTimeout(f"{service} 시간 초과") from None
    except requests.RequestException as e:
        raise UpstreamError(service, None, type(e).__name__) from None
    try:
        if resp.status_code in (401, 403):
            raise AuthError(f"{service} 인증 실패 (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise UpstreamError(service, resp.status_code)
        _read_capped(service, resp)
    finally:
        resp.close()
    return resp


def _read_capped(service: str, resp: requests.Response, limit: int = MAX_RESPONSE_BYTES) -> None:
    """본문을 limit 바이트까지만 읽는다. 넘으면 UpstreamError("응답 과대").

    읽은 본문은 resp._content 에 넣어 두어 .text / .json() 이 평소처럼 동작한다.
    """
    declared = resp.headers.get("Content-Length") if resp.headers else None
    if declared and declared.isdigit() and int(declared) > limit:
        raise UpstreamError(service, resp.status_code, "응답 과대")
    buf = bytearray()
    try:
        for chunk in resp.iter_content(_CHUNK):
            buf.extend(chunk)
            if len(buf) > limit:
                raise UpstreamError(service, resp.status_code, "응답 과대")
    except requests.Timeout:
        raise UpstreamTimeout(f"{service} 시간 초과") from None
    except requests.RequestException as e:
        raise UpstreamError(service, None, type(e).__name__) from None
    resp._content = bytes(buf)
    resp._content_consumed = True


def check_data_go_kr_header(service: str, text: str) -> None:
    """data.go.kr 게이트웨이 오류는 HTTP 200 으로도 올 수 있다. 본문 헤더로 판별."""
    if "SERVICE_KEY_IS_NOT_REGISTERED" in text or "SERVICE_KEY_IS_NULL" in text or "returnReasonCode" in text:
        if "<returnReasonCode>00<" in text or '"returnReasonCode":"00"' in text:
            return
        if "KEY" in text.upper():
            raise AuthError(f"{service} 서비스키 미등록 또는 누락")
        raise UpstreamError(service, 200, "게이트웨이 오류")
