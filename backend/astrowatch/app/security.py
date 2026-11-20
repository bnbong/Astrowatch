"""HTTP 보안 미들웨어 (NF3001 · NF3002).

위협 모델: 기본은 127.0.0.1 바인딩이지만 데모 때 LAN · 터널로 노출될 수 있다.
브라우저 사용자는 신뢰하되, 사용자가 방문한 다른 사이트(CSRF)는 신뢰하지 않는다.

- RequestGuardMiddleware : 선택적 접근 토큰, 상태 변경 요청(POST/PATCH/DELETE)의 출처 · 커스텀 헤더 ·
                           Content-Type 검사, 본문 크기 상한, /api/chat 속도 제한.
- SecurityHeadersMiddleware : 모든 응답에 보안 헤더, /api/* 에 no-store, HTML 에 CSP.
둘 다 순수 ASGI 미들웨어다. Content-Length 없이 스트리밍되는 본문도 상한까지만 읽는다.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from typing import Any, Awaitable, Callable, Iterable

from astrowatch.config import get_settings

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

UNSAFE_METHODS = frozenset({"POST", "PATCH", "DELETE", "PUT"})
MAX_BODY_BYTES = 256 * 1024
CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "astrowatch"
TOKEN_HEADER = "x-astrowatch-token"
TOKEN_EXEMPT = frozenset({"/api/health"})

DETAIL_BAD_ORIGIN = "허용되지 않은 출처"
DETAIL_NO_CSRF_HEADER = "요청 헤더가 올바르지 않습니다"
DETAIL_MEDIA_TYPE = "JSON 본문만 받습니다"
DETAIL_TOO_LARGE = "요청 본문이 너무 큽니다"
DETAIL_UNAUTHORIZED = "접근 토큰이 필요합니다"
DETAIL_RATE = "요청이 너무 많습니다"

CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
# FastAPI 의 /docs · /redoc 은 CDN 스크립트와 인라인 스크립트를 쓴다 — 그 경로에만 완화 CSP
DOCS_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
BASE_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


async def _json_response(send: Send, status: int, detail: str, extra: Iterable[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *extra],
    })
    await send({"type": "http.response.body", "body": body})


class TokenBucket:
    """키(IP)별 인메모리 토큰 버킷. 단일 프로세스 · 로컬 앱 전제."""

    def __init__(self, rate_per_min: int) -> None:
        self.rate = rate_per_min
        self.capacity = float(rate_per_min)
        self.refill = rate_per_min / 60.0
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            if len(self._state) > 10_000:  # 무한 증가 방지
                self._state.clear()
            tokens, last = self._state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens >= 1:
                self._state[key] = (tokens - 1, now)
                return True, 0
            self._state[key] = (tokens, now)
            return False, max(1, int((1 - tokens) / self.refill) + 1)


class RequestGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._bucket: TokenBucket | None = None  # 앱 인스턴스별

    @staticmethod
    def allowed_origins() -> frozenset[str]:
        return frozenset(o.lower() for o in get_settings().allowed_origins)

    def _origin_ok(self, origin: str, host: str) -> bool:
        """허용 목록에 있거나, Origin 의 host:port 가 요청의 Host 와 같으면(같은 출처) 허용.

        uvicorn 을 APP_PORT 와 다른 포트로 띄워도 같은 서버가 서빙한 화면의 요청은 막히지 않아야 한다.
        Host 헤더는 TrustedHostMiddleware 가 먼저 검사한다.
        """
        o = origin.rstrip("/").lower()
        if o in self.allowed_origins():
            return True
        try:
            from urllib.parse import urlsplit

            parts = urlsplit(o)
        except ValueError:
            return False
        return bool(parts.netloc) and parts.scheme in ("http", "https") and parts.netloc == host.strip().lower()

    def _bucket_for(self) -> TokenBucket:
        rate = get_settings().chat_rate_per_min
        if self._bucket is None or self._bucket.rate != rate:
            self._bucket = TokenBucket(rate)
        return self._bucket

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"].upper()
        path: str = scope.get("path", "")
        headers = _headers(scope)

        # 1) 선택적 접근 토큰 — /api/* (health 제외). 상수 시간 비교
        token = get_settings().app_access_token
        if token and path.startswith("/api/") and path not in TOKEN_EXEMPT and method != "OPTIONS":
            given = headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
                await _json_response(send, 401, DETAIL_UNAUTHORIZED)
                return

        length: str | None = None
        if method in UNSAFE_METHODS:
            # 2) 출처 검사 — Origin 이 있으면 허용 목록에 있어야 한다
            origin = headers.get("origin")
            if origin is not None and not self._origin_ok(origin, headers.get("host", "")):
                await _json_response(send, 403, DETAIL_BAD_ORIGIN)
                return
            if headers.get("sec-fetch-site", "").lower() == "cross-site":
                await _json_response(send, 403, DETAIL_BAD_ORIGIN)
                return
            # 3) 커스텀 헤더 강제 — 단순 폼 제출 · 이미지 태그로는 붙일 수 없다 (CSRF 2차 방어)
            if headers.get(CSRF_HEADER, "").lower() != CSRF_HEADER_VALUE:
                await _json_response(send, 403, DETAIL_NO_CSRF_HEADER)
                return
            # 4) 본문 크기 상한
            length = headers.get("content-length")
            if length is not None:
                if not length.strip().isdigit():
                    await _json_response(send, 400, "Content-Length 가 올바르지 않습니다")
                    return
                if int(length) > MAX_BODY_BYTES:
                    await _json_response(send, 413, DETAIL_TOO_LARGE)
                    return
            # 5) 본문이 있는 POST/PATCH 는 JSON 만
            has_body = (length is not None and int(length) > 0) or "transfer-encoding" in headers
            if method in ("POST", "PATCH", "PUT") and has_body:
                ctype = headers.get("content-type", "").lower()
                if not ctype.startswith("application/json"):
                    await _json_response(send, 415, DETAIL_MEDIA_TYPE)
                    return

        # 6) /api/chat 속도 제한 (클라이언트 IP 기준)
        if path == "/api/chat" and method == "POST":
            client = scope.get("client") or ("unknown", 0)
            allowed, retry = self._bucket_for().allow(str(client[0]))
            if not allowed:
                await _json_response(send, 429, DETAIL_RATE, [(b"retry-after", str(retry).encode())])
                return

        if method in UNSAFE_METHODS and length is None:
            # Content-Length 없는(chunked) 본문은 상한까지 미리 읽어 두고 넘으면 413, 아니면 그대로 다시 흘려 준다
            chunks: list[bytes] = []
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body = message.get("body", b"")
                total += len(body)
                if total > MAX_BODY_BYTES:
                    await _json_response(send, 413, DETAIL_TOO_LARGE)
                    return
                chunks.append(body)
                if not message.get("more_body", False):
                    break
            if total and method in ("POST", "PATCH", "PUT"):
                ctype = headers.get("content-type", "").lower()
                if not ctype.startswith("application/json"):
                    await _json_response(send, 415, DETAIL_MEDIA_TYPE)
                    return
            replay = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

            async def replay_receive() -> Message:
                if replay:
                    return replay.pop()
                return await receive()

            await self.app(scope, replay_receive, send)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_api = path == "/api" or path.startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                present = {k.lower() for k, _ in raw}
                for k, v in BASE_HEADERS:
                    if k not in present:
                        raw.append((k, v))
                if is_api:
                    raw = [(k, v) for k, v in raw if k.lower() != b"cache-control"]
                    raw.append((b"cache-control", b"no-store"))
                ctype = next((v for k, v in raw if k.lower() == b"content-type"), b"")
                if ctype.lower().startswith(b"text/html") and b"content-security-policy" not in present:
                    raw.append((b"content-security-policy", (DOCS_CSP if path in DOCS_PATHS else CSP).encode()))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_wrapper)
