"""보안 강화 회귀 테스트 (NF3001 · NF3002) — CORS · CSRF · 보안 헤더 · 토큰 · Host · 자원 한도 ·
비밀 파일 · 경로 검증 · 로그 마스킹 · 입력 검증 · 캘린더 멱등성 · 프론트 이스케이프."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from astrowatch.app.api import chat as chat_api
from astrowatch.clients import http
from astrowatch.config import ROOT_DIR, _resolve, get_settings, reset_settings_cache
from astrowatch.schemas.api import ChatResponse
from astrowatch.schemas.domain import Conditions, Intent
from astrowatch.schemas.plan import PlanCreate
from astrowatch.tools.contracts import EventKind

ALLOWED = "http://127.0.0.1:8000"
EVIL = "https://evil.example"
XRW = {"X-Requested-With": "Astrowatch"}
IS_POSIX = os.name == "posix"


def _app(monkeypatch):
    from astrowatch.app import main

    monkeypatch.setattr(main, "_load_tools", lambda: None)
    monkeypatch.setattr(main, "_start_logging_agent", lambda: None)
    monkeypatch.setattr(main, "_stop_logging_agent", lambda: None)
    return main.create_app()


@pytest.fixture
def client(monkeypatch):
    with TestClient(_app(monkeypatch), raise_server_exceptions=False, headers=XRW) as c:
        yield c


@pytest.fixture
def bare_client(monkeypatch):
    """기본 헤더 없음 (X-Requested-With 미포함)."""
    with TestClient(_app(monkeypatch), raise_server_exceptions=False) as c:
        yield c


def _plan_body(**over):
    body = {
        "event_title": "쌍둥이자리 유성우 극대", "event_kind": "meteor",
        "start_dt": "2026-12-14T22:10:00", "end_dt": "2026-12-15T01:30:00", "location_label": "서울",
    }
    body.update(over)
    return body


def _make_plan(**kw) -> int:
    from astrowatch.storage import plans as plan_store

    data = dict(event_title="유성우", event_kind=EventKind.METEOR, start_dt=datetime(2026, 12, 14, 22, 10),
                end_dt=datetime(2026, 12, 15, 1, 30), location_label="서울")
    data.update(kw)
    return plan_store.create_plan(PlanCreate(**data)).plan.id


# ── 1. CORS ──────────────────────────────────────────────────

def test_cors_preflight_allowed_origin(client):
    res = client.options("/api/plans", headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST"})
    assert res.headers.get("access-control-allow-origin") == ALLOWED
    assert "access-control-allow-credentials" not in res.headers


def test_cors_preflight_evil_origin(client):
    res = client.options("/api/plans", headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in res.headers
    res = client.get("/api/health", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in res.headers


def test_allowed_origins_from_env(monkeypatch):
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "https://demo.trycloudflare.com/, https://x.ngrok.app")
    monkeypatch.setenv("APP_HOST", "192.168.0.10")
    reset_settings_cache()
    s = get_settings()
    assert s.allowed_origins == (
        "http://127.0.0.1:8000", "http://localhost:8000", "http://192.168.0.10:8000",
        "https://demo.trycloudflare.com", "https://x.ngrok.app",
    )
    assert "demo.trycloudflare.com" in s.allowed_hosts and "192.168.0.10" in s.allowed_hosts
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    reset_settings_cache()
    assert not any("0.0.0.0" in o for o in get_settings().allowed_origins)
    assert "0.0.0.0" not in get_settings().allowed_hosts


# ── 2 · 14. CSRF 가드 ─────────────────────────────────────────

def test_csrf_evil_origin_forbidden(client):
    res = client.delete("/api/plans/1", headers={"Origin": EVIL})
    assert res.status_code == 403 and res.json() == {"detail": "허용되지 않은 출처"}


def test_csrf_cross_site_fetch_forbidden(client):
    res = client.delete("/api/plans/1", headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403


def test_csrf_same_origin_passes(client):
    assert client.delete("/api/plans/999").status_code == 404  # Origin 없음 → 통과 후 404
    assert client.delete("/api/plans/999", headers={"Origin": ALLOWED, "Sec-Fetch-Site": "same-origin"}).status_code == 404


def test_csrf_custom_header_required(bare_client):
    assert bare_client.delete("/api/plans/999").status_code == 403
    assert bare_client.post("/api/plans/1/calendar").status_code == 403  # 본문 없는 POST 도
    assert bare_client.get("/api/plans").status_code == 200  # GET 은 헤더 없이도
    assert bare_client.delete("/api/plans/999", headers=XRW).status_code == 404


def test_form_content_type_rejected(client):
    res = client.post("/api/plans", content=b"event_title=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status_code == 415
    res = client.post("/api/chat", content=b'{"message":"hi"}', headers={"Content-Type": "text/plain"})
    assert res.status_code == 415


def test_large_body_rejected(client):
    big = json.dumps(_plan_body(basis="x" * (300 * 1024))).encode()
    res = client.post("/api/plans", content=big, headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_large_chunked_body_rejected(client):
    def gen():
        for _ in range(5):
            yield b" " * (64 * 1024)

    res = client.post("/api/plans", content=gen(), headers={"Content-Type": "application/json"})
    assert res.status_code == 413


# ── 3. 보안 헤더 ─────────────────────────────────────────────

def test_security_headers_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in res.headers["permissions-policy"]
    csp = res.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert res.headers.get("cache-control") != "no-store"


def test_security_headers_api(client):
    res = client.get("/api/health")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in res.headers
    # 가드가 직접 만든 오류 응답에도 헤더가 붙는다
    res = client.delete("/api/plans/1", headers={"Origin": EVIL})
    assert res.headers["x-content-type-options"] == "nosniff" and res.headers["cache-control"] == "no-store"


def test_frontend_has_no_inline_scripts():
    for page in (ROOT_DIR / "frontend").glob("*.html"):
        html = page.read_text(encoding="utf-8")
        for tag in re.findall(r"<script\b[^>]*>", html, re.I):
            assert "src=" in tag, f"{page.name}: 인라인 스크립트"
        assert not re.search(r"\son[a-z]+\s*=", html, re.I), f"{page.name}: on* 핸들러"
        assert "javascript:" not in html.lower()


# ── 12. 접근 토큰 ────────────────────────────────────────────

def test_access_token_required_when_set(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "demo-token-0123456789")
    reset_settings_cache()
    with TestClient(_app(monkeypatch), raise_server_exceptions=False, headers=XRW) as c:
        assert c.get("/api/plans").status_code == 401
        assert c.get("/api/plans", headers={"X-Astrowatch-Token": "wrong"}).status_code == 401
        assert c.get("/api/plans", headers={"X-Astrowatch-Token": "demo-token-0123456789"}).status_code == 200
        assert c.get("/api/health").status_code == 200  # health 는 예외
        assert c.get("/").status_code == 200            # 정적 파일은 토큰 없이 (토큰 입력 UI 를 띄워야 하므로)


def test_access_token_unset_passes(client):
    assert client.get("/api/plans").status_code == 200


# ── 13. Host 헤더 ────────────────────────────────────────────

def test_untrusted_host_rejected(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost"}).status_code == 200


# ── 17. 자원 한도 ────────────────────────────────────────────

def test_chat_rate_limit(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MIN", "2")
    reset_settings_cache()
    monkeypatch.setattr(chat_api, "_run_turn", lambda sid, msg: ChatResponse(
        session_id="sess-0001", intent=Intent.CHAT, answer="ok", conditions=Conditions()))
    with TestClient(_app(monkeypatch), raise_server_exceptions=False, headers=XRW) as c:
        assert c.post("/api/chat", json={"message": "a"}).status_code == 200
        assert c.post("/api/chat", json={"message": "b"}).status_code == 200
        res = c.post("/api/chat", json={"message": "c"})
        assert res.status_code == 429 and res.json() == {"detail": "요청이 너무 많습니다"}
        assert "retry-after" in res.headers
        assert c.get("/api/plans").status_code == 200  # 다른 API 는 영향 없음


def test_list_limit_offset(client):
    ids = [_make_plan(start_dt=datetime(2026, 12, d, 21), end_dt=datetime(2026, 12, d, 23)) for d in (1, 2, 3)]
    assert [p["id"] for p in client.get("/api/plans").json()] == ids
    assert [p["id"] for p in client.get("/api/plans?limit=2&offset=1").json()] == ids[1:]
    assert client.get("/api/plans?limit=0").status_code == 422
    assert client.get("/api/plans?limit=1001").status_code == 422
    assert client.get("/api/plans?offset=-1").status_code == 422
    assert client.get("/api/logs?limit=5").status_code == 200
    assert client.get("/api/sessions?limit=5&offset=0").json() == []


def _raw_response(data: bytes, headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r.raw = io.BytesIO(data)
    r.headers.update(headers or {})
    r.encoding = "utf-8"
    return r


def test_http_get_reads_normal_body(monkeypatch):
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _raw_response(b'{"a": 1}'))
    resp = http.get("svc", "https://example.invalid/x")
    assert resp.json() == {"a": 1} and resp.text == '{"a": 1}'


def test_http_get_caps_response_size(monkeypatch):
    big = b"x" * (http.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _raw_response(big))
    with pytest.raises(http.UpstreamError) as ei:
        http.get("svc", "https://example.invalid/x")
    assert "응답 과대" in str(ei.value)
    monkeypatch.setattr(http.requests, "get",
                        lambda *a, **k: _raw_response(b"{}", {"Content-Length": str(10 * 1024 * 1024)}))
    with pytest.raises(http.UpstreamError):
        http.get("svc", "https://example.invalid/x")


# ── 20. 예외 체인 ────────────────────────────────────────────

@pytest.mark.parametrize("exc, expected", [
    (requests.ConnectionError("https://api.example/x?serviceKey=SECRETKEY123"), http.UpstreamError),
    (requests.Timeout("https://api.example/x?serviceKey=SECRETKEY123"), http.UpstreamTimeout),
])
def test_http_exception_chain_cut(monkeypatch, exc, expected):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(http.requests, "get", boom)
    with pytest.raises(expected) as ei:
        http.get("svc", "https://api.example/x", params={"serviceKey": "SECRETKEY123"})
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    assert "SECRETKEY123" not in str(ei.value)


# ── 4. 비밀 파일 ─────────────────────────────────────────────

def test_write_private_atomic(tmp_path):
    from astrowatch.secure_files import check_private, write_private

    target = tmp_path / "sub" / "token.json"
    write_private(target, '{"token": "a"}')
    write_private(target, '{"token": "b"}')  # 덮어쓰기
    assert target.read_text(encoding="utf-8") == '{"token": "b"}'
    assert [p.name for p in target.parent.iterdir()] == ["token.json"]  # 임시 파일 안 남음
    check_private(target)
    if IS_POSIX:
        assert (target.stat().st_mode & 0o777) == 0o600
        assert (target.parent.stat().st_mode & 0o777) == 0o700


@pytest.mark.skipif(not IS_POSIX, reason="POSIX 권한 검사")
def test_check_private_tightens_mode(tmp_path):
    from astrowatch.secure_files import check_private

    f = tmp_path / "t.json"
    f.write_text("{}")
    os.chmod(f, 0o644)
    check_private(f)
    assert (f.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(not IS_POSIX, reason="심볼릭 링크 생성은 POSIX 에서만 검사 (Windows 는 권한 필요)")
def test_symlink_rejected(tmp_path):
    from astrowatch.secure_files import check_private, write_private

    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(OSError):
        write_private(link, "{}")
    with pytest.raises(http.AuthError):
        check_private(link)
    assert real.read_text() == "{}"


def test_google_token_refresh_saved_privately(tmp_path, monkeypatch):
    """만료 토큰 갱신 → write_private 로 저장 (잠금 안, 저장 직전 재확인)."""
    from astrowatch.clients import calendar_provider as cp

    token = tmp_path / "tok.json"
    token.write_text(json.dumps({"token": "old", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                                 "expiry": "2000-01-01T00:00:00Z"}), encoding="utf-8")
    import google.oauth2.credentials as gc

    def fake_refresh(self, request):
        self.token = "new-access"
        self.expiry = datetime(2099, 1, 1)

    monkeypatch.setattr(gc.Credentials, "refresh", fake_refresh)
    saved = []
    monkeypatch.setattr(cp, "write_private", lambda p, text: saved.append((Path(p), json.loads(text)["token"])))
    creds = cp.GoogleCalendarProvider(token_file=token)._credentials()
    assert creds.token == "new-access" and saved == [(token, "new-access")]


# ── 5 · 15. 설정 경로 검증 ────────────────────────────────────

def test_config_rejects_traversal(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", "../../x")
    reset_settings_cache()
    with caplog.at_level(logging.WARNING, logger="astrowatch.config"):
        assert get_settings().google_token_file is None
    assert any("GOOGLE_TOKEN_FILE" in r.getMessage() for r in caplog.records)
    assert _resolve("data/../../etc/passwd") is None
    assert _resolve("data/a" + chr(0) + "b") is None


def test_config_absolute_path_needs_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_EXTERNAL_PATHS", raising=False)
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    reset_settings_cache()
    s = get_settings()
    assert s.google_token_file is None
    assert s.db_path == ROOT_DIR / "data/astrowatch.db"  # 거부되면 기본값
    monkeypatch.setenv("ALLOW_EXTERNAL_PATHS", "1")
    reset_settings_cache()
    assert get_settings().google_token_file == (tmp_path / "t.json").resolve()


def test_config_relative_inside_root(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json")
    reset_settings_cache()
    assert get_settings().google_token_file == ROOT_DIR / "secrets/google_token.json"


def test_config_secret_path_rules(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", "frontend/google_token.json")  # 정적 서빙 폴더
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", "backend")  # 디렉터리
    reset_settings_cache()
    s = get_settings()
    assert s.google_token_file is None and s.google_client_secret_file is None


# ── 6. 로그 마스킹 ───────────────────────────────────────────

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def test_redact_filter_masks_values():
    from astrowatch.redact import SecretRedactingFilter

    lg = logging.getLogger("astrowatch.test.redact")
    h = _Capture()
    h.addFilter(SecretRedactingFilter(["SuperSecretKey123", "abc%2Bdef=="]))
    lg.addHandler(h)
    try:
        lg.warning("url=https://x?serviceKey=%s", "SuperSecretKey123")
        try:
            raise RuntimeError("failed GET https://x?key=SuperSecretKey123&k2=abc%2Bdef==")
        except RuntimeError:
            lg.exception("boom")
    finally:
        lg.removeHandler(h)
    text = "\n".join(h.lines)
    assert "SuperSecretKey123" not in text and "abc%2Bdef==" not in text
    assert text.count("***") >= 3


def test_install_redaction_uses_settings(monkeypatch, tmp_path):
    from astrowatch import redact

    token = tmp_path / "tok.json"
    token.write_text(json.dumps({"token": "ya29.access-token-value", "refresh_token": "1//refresh-token-value",
                                 "client_secret": "GOCSPX-client-secret"}), encoding="utf-8")
    monkeypatch.setenv("GMS_KEY", "gms-secret-value-1")
    monkeypatch.setenv("DATA_GO_KR_KEY", "abc%2Bdef%3D%3D")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    reset_settings_cache()
    root = logging.getLogger()
    h = _Capture()
    root.addHandler(h)
    try:
        redact.install_redaction()
        logging.getLogger("astrowatch.any").warning(
            "keys %s %s %s %s %s %s", "gms-secret-value-1", "abc+def==", "abc%2Bdef%3D%3D",
            "ya29.access-token-value", "1//refresh-token-value", "GOCSPX-client-secret")
    finally:
        root.removeHandler(h)
        monkeypatch.delenv("GMS_KEY")
        reset_settings_cache()
        redact.install_redaction()
    line = h.lines[-1]
    for secret in ("gms-secret-value-1", "abc+def==", "abc%2Bdef%3D%3D", "ya29.access", "refresh-token", "GOCSPX"):
        assert secret not in line
    assert line.count("***") == 6


# ── 7. SQL 컬럼 allowlist ────────────────────────────────────

def test_update_plan_rejects_unknown_column():
    from astrowatch.storage import plans as plan_store

    pid = _make_plan()
    evil = SimpleNamespace(model_dump=lambda **k: {"event_title = 'x', id": "y"})
    with pytest.raises(ValueError):
        plan_store.update_plan(pid, evil)
    assert plan_store.get_plan(pid).event_title == "유성우"


# ── 8 · 9. 경로 파라미터 · 검증 오류 ──────────────────────────

@pytest.mark.parametrize("path", ["/api/sessions/ab", "/api/logs/ab", "/api/sessions/" + "a" * 65,
                                  "/api/logs/bad.id!", "/api/sessions/%3Cscript%3E"])
def test_session_id_pattern(client, path):
    assert client.get(path).status_code == 422
    assert client.delete(path).status_code == 422


@pytest.mark.parametrize("method, path", [("get", "/api/plans/0"), ("patch", "/api/plans/0"),
                                          ("delete", "/api/plans/0"), ("post", "/api/plans/0/calendar"),
                                          ("get", "/api/plans/-3")])
def test_plan_id_must_be_positive(client, method, path):
    kw = {"json": {"event_title": "x"}} if method == "patch" else {}
    assert getattr(client, method)(path, **kw).status_code == 422


def test_validation_error_does_not_echo_input(client):
    marker = "<script>alert(1)</script>" * 20
    res = client.post("/api/plans", json=_plan_body(event_title=marker))
    assert res.status_code == 422
    for err in res.json()["detail"]:
        assert set(err) == {"loc", "msg", "type"}
    assert "<script>" not in res.text
    res = client.get("/api/sessions/" + "x!" * 5)
    assert all("input" not in e for e in res.json()["detail"])


# ── 18. 캘린더 등록 멱등성 ────────────────────────────────────

def test_deterministic_event_id_format():
    from astrowatch.clients.calendar_provider import deterministic_event_id

    a = deterministic_event_id(7, datetime(2026, 11, 18, 12, 0))
    assert re.fullmatch(r"[a-v0-9]{32}", a)
    assert a == deterministic_event_id(7, datetime(2026, 11, 18, 12, 0))
    assert a != deterministic_event_id(8, datetime(2026, 11, 18, 12, 0))


def test_local_provider_plan_unique():
    from astrowatch.clients import calendar_provider as cp

    prov = cp.LocalCalendarProvider()
    first = prov.add_event("a", datetime(2026, 12, 1, 21), datetime(2026, 12, 1, 23), None, 42)
    with pytest.raises(cp.EventAlreadyExists) as ei:
        prov.add_event("a", datetime(2026, 12, 1, 21), datetime(2026, 12, 1, 23), None, 42)
    assert ei.value.event.event_id == first.event_id


def test_add_twice_second_already_registered():
    from astrowatch.storage import plans as plan_store
    from astrowatch.tools import calendar as cal

    pid = _make_plan()
    first = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    second = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert first.ok and not first.data.already_registered
    assert second.ok and second.data.already_registered and second.data.event_id == first.data.event_id
    # calendar_event_id 기록이 빠진 상태(이전 저장 실패)에서도 공급자 쪽 중복을 막는다
    plan_store.set_calendar_event_id(pid, None)
    third = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert third.ok and third.data.already_registered and third.data.event_id == first.data.event_id
    assert plan_store.get_plan(pid).calendar_event_id == first.data.event_id


def test_concurrent_add_creates_one_event():
    from datetime import date

    from astrowatch.clients import calendar_provider as cp
    from astrowatch.tools import calendar as cal

    pid = _make_plan()
    results = []
    threads = [threading.Thread(target=lambda: results.append(cal.add_plan_to_calendar.invoke({"plan_id": pid})))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.ok for r in results)
    assert sum(not r.data.already_registered for r in results) == 1
    assert len(cp.LocalCalendarProvider().list_events(date(2026, 12, 1), date(2026, 12, 31))) == 1


def test_google_409_is_already_registered(tmp_path, monkeypatch):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    from astrowatch.storage import plans as plan_store
    from astrowatch.tools import calendar as cal

    token = tmp_path / "google_token.json"
    token.write_text(json.dumps({"token": "fake-access", "refresh_token": "fake-refresh", "client_id": "cid",
                                 "client_secret": "csecret", "expiry": "2099-01-01T00:00:00Z"}), encoding="utf-8")
    monkeypatch.setenv("CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    reset_settings_cache()
    calls = []

    class Req:
        def __init__(self, result=None, exc=None):
            self.result, self.exc = result, exc

        def execute(self, num_retries=0):
            if self.exc:
                raise self.exc
            return self.result

    class Events:
        def insert(self, **kw):
            calls.append(("insert", kw))
            return Req(exc=HttpError(Response({"status": 409}), b"{}"))

        def get(self, **kw):
            calls.append(("get", kw))
            return Req({"id": kw["eventId"], "summary": "기존", "htmlLink": "https://calendar.google.com/x",
                        "start": {"dateTime": "2026-12-14T22:10:00+09:00"},
                        "end": {"dateTime": "2026-12-15T01:30:00+09:00"}})

    class Svc:
        def events(self):
            return Events()

    import googleapiclient.discovery

    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: Svc())
    pid = _make_plan()
    res = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert res.ok and res.data.already_registered
    sent_id = calls[0][1]["body"]["id"]
    assert re.fullmatch(r"[a-v0-9]{32}", sent_id)
    assert calls[1] == ("get", {"calendarId": "primary", "eventId": sent_id})
    assert res.data.event_id == sent_id and plan_store.get_plan(pid).calendar_event_id == sent_id


# ── 19. 프론트 이스케이프 (node 스모크) ──────────────────────

NODE = shutil.which("node")

TIMELINE_SMOKE = r"""
const fs = require("fs"), vm = require("vm"), path = require("path");
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this.innerHTML = "";
    this.classList = { add: (c) => { this.className += " " + c; } }; }
  appendChild(c) { this.children.push(c); return c; }
}
const ctx = { document: { createElement: (t) => new El(t), addEventListener() {} }, console };
ctx.window = ctx;
vm.createContext(ctx);
for (const f of ["api.js", "ui.js", "timeline.js"]) {
  vm.runInContext(fs.readFileSync(path.join(process.argv[2], f), "utf8"), ctx, { filename: f });
}
const box = new El("div");
const evil = '"><img src=x onerror=alert(1)>';
ctx.Timeline.render(box, [{ seq: 1, type: evil, stage: null }, { seq: 2, type: "tool_result", tool: evil, ok: false }]);
const html = box.children[0].children.map((li) => li.innerHTML).join("\n");
process.stdout.write(html);
"""


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_timeline_escapes_unknown_type(tmp_path):
    script = tmp_path / "smoke.js"
    script.write_text(TIMELINE_SMOKE, encoding="utf-8")
    out = subprocess.run([NODE, str(script), str(ROOT_DIR / "frontend" / "js")], capture_output=True,
                         text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    assert "<img" not in out.stdout
    assert "&quot;&gt;&lt;img" in out.stdout


def test_redact_keeps_uvicorn_access_args_structure():
    """uvicorn 접근 로그는 args 5개 튜플을 그대로 풀어 쓴다 — 필터가 구조를 깨면 로그마다 traceback 이 난다(실제 회귀)."""
    import logging

    from uvicorn.logging import AccessFormatter

    from astrowatch.redact import SecretRedactingFilter

    f = SecretRedactingFilter(["SUPERSECRETKEY"])
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                            '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", "/api/x?k=SUPERSECRETKEY", "1.1", 200), None)
    assert f.filter(rec) is True
    assert isinstance(rec.args, tuple) and len(rec.args) == 5
    out = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(rec)
    assert "SUPERSECRETKEY" not in out and "***" in out and "GET" in out

    rec2 = logging.LogRecord("x", logging.INFO, __file__, 1, "키 %s 값 %d", ("SUPERSECRETKEY", 3), None)
    f.filter(rec2)
    assert rec2.getMessage() == "키 *** 값 3"


def test_same_host_origin_allowed_on_any_port(monkeypatch):
    """uvicorn 을 APP_PORT 와 다른 포트로 띄워도 같은 서버가 서빙한 화면의 변경 요청은 통과한다(실제 회귀)."""
    from fastapi.testclient import TestClient

    from astrowatch.app.main import create_app
    from astrowatch.config import reset_settings_cache

    monkeypatch.setenv("APP_ALLOWED_HOSTS", "127.0.0.1")
    reset_settings_cache()
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8003") as c:
        h = {"X-Requested-With": "Astrowatch", "Origin": "http://127.0.0.1:8003", "Host": "127.0.0.1:8003"}
        assert c.delete("/api/plans/999999", headers=h).status_code == 404   # 403 이 아니라 라우터까지 도달
        bad = {"X-Requested-With": "Astrowatch", "Origin": "http://127.0.0.1:9999", "Host": "127.0.0.1:8003"}
        assert c.delete("/api/plans/999999", headers=bad).status_code == 403
